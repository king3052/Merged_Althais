"""
records_intake.py: outside records coming into a clinic (lab results, imaging reports, X-rays, referrals, records from
another practice), all landing in one Records Inbox (/v4/emr/inbox) where staff check each one and file it to a patient.

Four ways in:
  * Secure upload link. Staff make a link (optionally for one patient) and send it to another office or a patient.
    Anyone with the link can upload up to 10 files (PDF, PNG, JPEG, TIFF, WebP; 15 MB each) until it expires (1 to 30
    days) or is revoked. The uploader sees only the clinic's name: never the patient's name, never other files.
        GET  /send-records/{token}             the upload page (no account)
        POST /api/public/records/{token}       the files
  * Fax. A fax provider that signs a BAA sends each incoming fax to POST /api/intake/fax/inbound, signed with
    FAX_WEBHOOK_SECRET (HMAC-SHA256 of the body, header X-Althais-Signature). The fax number it arrived on picks the clinic
    (Records Inbox > Settings). Built vendor-neutral; a provider whose webhook looks different gets a small adapter.
  * Open Dental. For a patient that came from Open Dental, list their Imaging-module documents and copy one in (through
    Althais's SFTP drop, see opendental.py); a filed record can also be pushed back into Open Dental.
  * A records network (records_network.py): off until a network is under contract; the mock serves synthetic patients.

Every file is checked by its first bytes (not its name), stored encrypted (AES-256-GCM, crypto_box, PHI_ENC_KEY) and
read for a first guess at the patient and the kind of document (text PDFs only: scanned faxes have no text layer until
an OCR service under the BAA is added). Staff confirm the patient before anything is filed; nothing is filed by itself.
Filing an X-ray image for a dental clinic also adds it to the patient's images, so it can go on a claim.

Staff APIs (/api/intake/...) are on the clinic's Patients area and land in the access log like every /api call; the
public upload is recorded there too, as the link it came through.
"""
import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timezone
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import DateTime, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

import auth
from auth import Base, OrgPatient, OrgSettings, _org_namespace, engine, ensure_area, get_db, require_any_role

router = APIRouter()

MAX_FILE = 15 * 1024 * 1024
MAX_FILES = 10
STAFF = ("admin", "biller", "provider", "front_desk")
DOC_TYPES = {"lab": "Lab result", "imaging": "Imaging report", "xray": "X-ray or image", "referral": "Referral",
             "records": "Outside records", "insurance": "Insurance or EOB", "other": "Other"}
SOURCES = {"upload": "Upload link", "fax": "Fax", "opendental": "Open Dental", "network": "Records network", "staff": "Staff upload"}
SETTINGS = "records_intake"
NO_STORE = {"Cache-Control": "no-store"}


def _now():
    return dt.datetime.now(timezone.utc).replace(tzinfo=None)


# ── storage ────────────────────────────────────────────────────────────────────────────────────────────────────
class IntakeDoc(Base):
    __tablename__ = "intake_docs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(16), default="upload")
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)    # new | filed | dismissed
    mime: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    mrn: Mapped[str] = mapped_column(String(64), default="", index=True)
    doc_type: Mapped[str] = mapped_column(String(16), default="")
    link_id: Mapped[int] = mapped_column(Integer, nullable=True)
    ext_ref: Mapped[str] = mapped_column(String(128), default="")                 # fax id, Open Dental DocNum, network ref
    sealed: Mapped[str] = mapped_column(Text, default="")                         # sender, file name, title, guess, note
    data: Mapped[str] = mapped_column(Text, default="")                           # the file, base64, sealed
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    filed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    filed_by: Mapped[str] = mapped_column(String(255), default="")


class IntakeLink(Base):
    __tablename__ = "intake_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mrn: Mapped[str] = mapped_column(String(64), default="")
    sealed: Mapped[str] = mapped_column(Text, default="")                         # label (may name the patient), clinic name
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    files: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[int] = mapped_column(Integer, default=0)


Base.metadata.create_all(engine)


def _seal(obj) -> str:
    import crypto_box
    return crypto_box.seal_text(json.dumps(obj, separators=(",", ":")), "records-intake", "PHI_ENC_KEY")


def _open(s: str):
    import crypto_box
    if not s:
        return {}
    try:
        return json.loads(crypto_box.open_text(s, "records-intake", "PHI_ENC_KEY"))
    except Exception:
        return {}


def _seal_bytes(b: bytes) -> str:
    import crypto_box
    return crypto_box.seal_text(base64.b64encode(b).decode(), "records-intake-file", "PHI_ENC_KEY")


def _open_bytes(s: str) -> bytes:
    import crypto_box
    return base64.b64decode(crypto_box.open_text(s, "records-intake-file", "PHI_ENC_KEY"))


def _hash(token: str) -> str:
    return hashlib.sha256(("althais-intake|" + token).encode()).hexdigest()


# ── what a file is ─────────────────────────────────────────────────────────────────────────────────────────────
def sniff(b: bytes) -> str:
    """The file's type from its first bytes, or "" for anything we don't take."""
    if b[:5] == b"%PDF-":
        return "application/pdf"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return ""


def pdf_text(b: bytes, pages: int = 4) -> str:
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(b))
        return "\n".join((p.extract_text() or "") for p in r.pages[:pages])[:20000]
    except Exception:
        return ""


KEYS = {
    "lab": r"\b(specimen|reference range|ref\.? range|collected|reported|laboratory|hemoglobin|a1c|cbc|cmp|lipid panel|mg/dl|mmol/l|culture|urinalysis)\b",
    "imaging": r"\b(radiolog\w*|impression|x-?ray|ct scan|\bct\b|mri|ultrasound|mammogra\w*|views?\b|findings)\b",
    "xray": r"\b(bitewing|periapical|panoramic|pano|fmx|cbct|radiograph)\b",
    "referral": r"\b(referral|referred|reason for referral|please see|consult(ation)? request)\b",
    "insurance": r"\b(explanation of benefits|eob|claim number|allowed amount|patient responsibility|member id)\b",
    "records": r"\b(progress note|history of present illness|hpi|assessment|plan|discharge summary|medications|problem list|chief complaint)\b",
}
DOB = re.compile(r"(?:DOB|D\.O\.B\.?|Date of Birth|Birth ?Date|Birthdate)\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.I)
NAME = re.compile(r"(?:Patient(?: Name)?|Pt(?: Name)?|Name)\s*[:#]\s*([A-Z][A-Za-z'\-]+\s*,\s*[A-Z][A-Za-z'\-]+|[A-Z][A-Za-z'\-]+\s+[A-Z][A-Za-z'\-]+)")


def _iso(d: str) -> str:
    d = d.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", d)
    if m:
        return d
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", d)
    if not m:
        return ""
    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yr < 100:
        yr += 1900 if yr > (_now().year % 100) else 2000
    try:
        return dt.date(yr, mo, da).isoformat()
    except ValueError:
        return ""


def _tokens(name: str) -> set:
    return {t for t in re.split(r"[^a-z]+", (name or "").lower()) if len(t) > 1}


def guess(b: bytes, mime: str, db: Session, org: str) -> dict:
    """A first guess at what this is and whose it is, for staff to confirm. Never files anything."""
    text = pdf_text(b) if mime == "application/pdf" else ""
    low = text.lower()
    scores = {k: len(re.findall(rx, low)) for k, rx in KEYS.items()}
    if mime.startswith("image/"):
        kind = "xray"
    else:
        best = max(scores, key=lambda k: scores[k]) if text else ""
        kind = best if best and scores[best] >= 2 else ("other" if text else "")
    dob = next((_iso(m.group(1)) for m in DOB.finditer(text) if _iso(m.group(1))), "")
    nm = next((m.group(1).strip() for m in NAME.finditer(text)), "")
    cands = []
    if dob or nm:
        want = _tokens(nm)
        for p in db.scalars(select(OrgPatient).where(OrgPatient.org_key == org)).all():
            score = (2 if dob and p.dob == dob else 0) + (len(want & _tokens(p.name)) if want else 0)
            if score >= 2 or (not dob and want and want <= _tokens(p.name)):
                cands.append({"mrn": p.mrn, "name": p.name, "dob": p.dob, "score": score})
        cands = sorted(cands, key=lambda c: -c["score"])[:5]
    return {"doc_type": kind, "patient_name": nm, "dob": dob, "candidates": cands, "text": bool(text),
            "confidence": "high" if cands and cands[0]["score"] >= 3 else "medium" if cands else "none"}


def save_doc(db: Session, org: str, b: bytes, mime: str, *, source: str, filename: str = "", sender: str = "", note: str = "",
             title: str = "", mrn: str = "", doc_type: str = "", link_id=None, ext_ref: str = "", status: str = "new", by: str = "") -> IntakeDoc:
    g = guess(b, mime, db, org)
    meta = {"filename": filename[:200], "sender": sender[:200], "note": note[:2000], "title": title[:200], "guess": g}
    row = IntakeDoc(org_key=org, source=source, status=status, mime=mime, size=len(b), sha256=hashlib.sha256(b).hexdigest(),
                    mrn=mrn, doc_type=doc_type or g["doc_type"] or "other", link_id=link_id, ext_ref=ext_ref[:128],
                    sealed=_seal(meta), data=_seal_bytes(b))
    if status == "filed":
        row.filed_at, row.filed_by = _now(), by[:255]
    db.add(row)
    db.commit()
    return row


def doc_dict(r: IntakeDoc, names: dict = None) -> dict:
    m = _open(r.sealed)
    g = m.get("guess") or {}
    return {"id": r.id, "source": r.source, "source_label": SOURCES.get(r.source, r.source), "status": r.status, "mime": r.mime, "size": r.size,
            "mrn": r.mrn, "patient": (names or {}).get(r.mrn, ""), "doc_type": r.doc_type, "doc_type_label": DOC_TYPES.get(r.doc_type, r.doc_type),
            "filename": m.get("filename", ""), "sender": m.get("sender", ""), "note": m.get("note", ""), "title": m.get("title", ""),
            "guess": g, "ext_ref": r.ext_ref, "received_at": r.received_at.isoformat() + "Z" if r.received_at else "",
            "filed_at": r.filed_at.isoformat() + "Z" if r.filed_at else "", "filed_by": r.filed_by, "duplicate_of": m.get("duplicate_of")}


# ── access ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _staff(user, db):
    ensure_area(user, "patients")
    return _org_namespace(user)


def _doc(db, org, did) -> IntakeDoc:
    r = db.get(IntakeDoc, did)
    if not r or r.org_key != org:
        from fastapi import HTTPException
        raise HTTPException(404, "That record isn't in this clinic's inbox.")
    return r


def _patient(db, org, mrn):
    return db.scalar(select(OrgPatient).where(OrgPatient.org_key == org, OrgPatient.mrn == str(mrn or "")))


def _names(db, org) -> dict:
    return {p.mrn: p.name for p in db.scalars(select(OrgPatient).where(OrgPatient.org_key == org)).all()}


def _settings(db, org):
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == SETTINGS))
    try:
        return row, (json.loads(row.data or "{}") if row else {})
    except Exception:
        return row, {}


@router.get("/api/intake/patients")
def patients(q: str = "", user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    """Patients to file a record to, by name, MRN or date of birth (up to 50)."""
    org = _staff(user, db)
    rows = db.scalars(select(OrgPatient).where(OrgPatient.org_key == org).order_by(OrgPatient.name)).all()
    t = q.strip().lower()
    hit = [p for p in rows if not t or t in (p.name or "").lower() or t in (p.mrn or "").lower() or t in (p.dob or "")]
    return [{"mrn": p.mrn, "name": p.name, "dob": p.dob} for p in hit[:50]]


# ── staff: upload links ────────────────────────────────────────────────────────────────────────────────────────
@router.post("/api/intake/links")
async def link_create(request: Request, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    b = await request.json()
    mrn = str(b.get("mrn") or "").strip()
    if mrn and not _patient(db, org, mrn):
        return JSONResponse({"error": "That patient isn't in this clinic."}, status_code=400)
    try:
        days = max(1, min(30, int(b.get("days") or 7)))
    except (TypeError, ValueError):
        days = 7
    token = secrets.token_urlsafe(24)
    label = str(b.get("label") or "").strip()[:200]
    row = IntakeLink(org_key=org, token_hash=_hash(token), mrn=mrn, sealed=_seal({"label": label, "clinic": user.organization or ""}),
                     created_by=(user.email or "")[:255], expires_at=_now() + dt.timedelta(days=days))
    db.add(row)
    db.commit()
    path = f"/send-records/{token}"           # the app adds its own address (location.origin), as the dental links do
    return JSONResponse({"id": row.id, "path": path, "url": (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/") + path if os.environ.get("PUBLIC_BASE_URL") else "",
                         "expires_at": row.expires_at.isoformat() + "Z",
                         "note": "Copy the link now. It isn't shown again."}, headers=NO_STORE)


@router.get("/api/intake/links")
def link_list(user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    names = _names(db, org)
    out = []
    for r in db.scalars(select(IntakeLink).where(IntakeLink.org_key == org).order_by(IntakeLink.created_at.desc()).limit(100)).all():
        m = _open(r.sealed)
        out.append({"id": r.id, "label": m.get("label", ""), "mrn": r.mrn, "patient": names.get(r.mrn, ""), "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() + "Z", "expires_at": r.expires_at.isoformat() + "Z", "uses": r.uses, "files": r.files,
                    "active": not r.revoked and r.expires_at > _now()})
    return out


@router.post("/api/intake/links/{lid}/revoke")
def link_revoke(lid: int, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    r = db.get(IntakeLink, lid)
    if not r or r.org_key != org:
        return JSONResponse({"error": "No such link."}, status_code=404)
    r.revoked = 1
    db.commit()
    return {"ok": True}


# ── public: the upload page ────────────────────────────────────────────────────────────────────────────────────
_hits = defaultdict(deque)
_lock = threading.Lock()


def _limited(key: str, per_hour: int = 30) -> bool:
    now = time.time()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= per_hour:
            return True
        q.append(now)
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v]:
                _hits.pop(k, None)
    return False


def _live_link(db, token):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,64}", token or ""):
        return None
    r = db.scalar(select(IntakeLink).where(IntakeLink.token_hash == _hash(token)))
    return r if r and not r.revoked and r.expires_at > _now() else None


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Send records to {clinic}</title>
<style>
:root{{--b:#1d5bb8;--t:#1c2733;--m:#5f6b78;--l:#d3d9e0;--bg:#eef1f4}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--t);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
.bar{{background:var(--b);color:#fff;padding:14px 18px;font-weight:600;letter-spacing:.28em;font-size:13px}}
main{{max-width:560px;margin:24px auto;padding:0 16px}}.card{{background:#fff;border:1px solid var(--l);border-radius:10px;padding:22px}}
h1{{font-size:21px;margin:0 0 6px}}p{{margin:0 0 14px;color:var(--m)}}label{{display:block;font-size:13px;font-weight:600;margin:14px 0 5px}}
input[type=text],textarea{{width:100%;padding:10px;border:1px solid var(--l);border-radius:7px;font:inherit}}
.drop{{border:2px dashed var(--l);border-radius:9px;padding:22px;text-align:center;color:var(--m);cursor:pointer}}
.drop.on{{border-color:var(--b);background:#eef4fc}}button{{margin-top:18px;width:100%;padding:12px;border:0;border-radius:7px;background:var(--b);color:#fff;font:600 15px system-ui;cursor:pointer}}
button:disabled{{opacity:.6}}ul{{padding-left:18px;margin:10px 0 0;color:var(--t)}}.msg{{margin-top:14px;font-weight:600}}.ok{{color:#17784a}}.err{{color:#b42318}}
small{{display:block;margin-top:16px;color:var(--m);font-size:12.5px}}
</style></head><body><div class="bar">ALTHAIS</div><main><div class="card">
<h1>Send records to {clinic}</h1>
<p>Upload lab results, X-rays, imaging reports or other records. They go straight to {clinic}, encrypted. You won't be able to see them again after sending.</p>
<form id="f"><label>Files <span style="font-weight:400;color:var(--m)">(PDF, PNG, JPEG, TIFF or WebP, up to 10 files, 15 MB each)</span></label>
<div class="drop" id="drop">Drop files here or tap to choose<input id="files" type="file" name="files" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,application/pdf,image/*" hidden></div><ul id="list"></ul>
<label for="sender">Your name and practice</label><input id="sender" name="sender" type="text" maxlength="200" placeholder="e.g. Dr. Lee, Northside Dental">
<label for="note">Note for the clinic <span style="font-weight:400;color:var(--m)">(optional)</span></label><textarea id="note" name="note" rows="3" maxlength="2000"></textarea>
<button id="go" type="submit">Send securely</button><div id="msg" class="msg" role="status"></div></form>
<small>This link expires {expires}. Sent through Althais for {clinic}.</small></div></main>
<script>
var inp=document.getElementById('files'),drop=document.getElementById('drop'),list=document.getElementById('list'),picked=[];
function show(){{list.innerHTML=picked.map(function(f){{return '<li>'+f.name.replace(/[<&>]/g,'')+' ('+Math.ceil(f.size/1024)+' KB)</li>';}}).join('');}}
drop.onclick=function(){{inp.click();}};inp.onchange=function(){{picked=picked.concat([].slice.call(inp.files)).slice(0,10);show();}};
['dragover','dragenter'].forEach(function(e){{drop.addEventListener(e,function(ev){{ev.preventDefault();drop.classList.add('on');}});}});
['dragleave','drop'].forEach(function(e){{drop.addEventListener(e,function(ev){{ev.preventDefault();drop.classList.remove('on');}});}});
drop.addEventListener('drop',function(ev){{picked=picked.concat([].slice.call(ev.dataTransfer.files)).slice(0,10);show();}});
document.getElementById('f').onsubmit=function(ev){{ev.preventDefault();var m=document.getElementById('msg'),b=document.getElementById('go');
if(!picked.length){{m.className='msg err';m.textContent='Choose at least one file.';return;}}
var fd=new FormData();picked.forEach(function(f){{fd.append('files',f);}});fd.append('sender',document.getElementById('sender').value);fd.append('note',document.getElementById('note').value);
b.disabled=true;b.textContent='Sending…';m.textContent='';
fetch('/api/public/records/{token}',{{method:'POST',body:fd}}).then(function(r){{return r.json().then(function(j){{return [r.ok,j];}});}}).then(function(x){{
if(x[0]){{m.className='msg ok';m.textContent=x[1].message;picked=[];show();document.getElementById('note').value='';}}else{{m.className='msg err';m.textContent=x[1].error||'That didn\\u2019t go through. Try again.';}}
b.disabled=false;b.textContent='Send securely';}}).catch(function(){{m.className='msg err';m.textContent='Couldn\\u2019t reach Althais. Check your connection and try again.';b.disabled=false;b.textContent='Send securely';}});}};
</script></body></html>"""

GONE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Link not available</title></head><body style="margin:0;background:#eef1f4;font:15px/1.5 system-ui,sans-serif;color:#1c2733">
<div style="background:#1d5bb8;color:#fff;padding:14px 18px;font-weight:600;letter-spacing:.28em;font-size:13px">ALTHAIS</div>
<main style="max-width:520px;margin:30px auto;padding:0 16px"><h1 style="font-size:21px">This link isn't available</h1>
<p>It may have expired or been turned off. Ask the clinic for a new link.</p></main></body></html>"""


@router.get("/send-records/{token}", include_in_schema=False)
def upload_page(token: str, request: Request, db: Session = Depends(get_db)):
    r = _live_link(db, token)
    if not r:
        return HTMLResponse(GONE, status_code=404, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    m = _open(r.sealed)
    html = PAGE.format(clinic=escape(m.get("clinic") or "the clinic"), token=escape(token), expires=r.expires_at.strftime("%B %-d, %Y"))
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


@router.post("/api/public/records/{token}")
async def upload_files(token: str, request: Request, db: Session = Depends(get_db)):
    import audit
    ip = audit.client_ip(request)
    if _limited("ip:" + ip) or _limited("tok:" + token[:12], per_hour=60):
        return JSONResponse({"error": "Too many uploads. Wait a few minutes and try again."}, status_code=429)
    r = _live_link(db, token)
    if not r:
        return JSONResponse({"error": "This link has expired or was turned off. Ask the clinic for a new one."}, status_code=404)
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    if size > MAX_FILES * MAX_FILE + 1024 * 1024:
        return JSONResponse({"error": "That's more than 10 files or 15 MB a file."}, status_code=413)
    form = await request.form()
    files = [f for f in form.getlist("files") if hasattr(f, "read")][:MAX_FILES + 1]
    if not files:
        return JSONResponse({"error": "Choose at least one file."}, status_code=400)
    if len(files) > MAX_FILES:
        return JSONResponse({"error": "Send at most 10 files at a time."}, status_code=400)
    sender, note = str(form.get("sender") or "")[:200], str(form.get("note") or "")[:2000]
    saved, refused = 0, []
    for f in files:
        b = await f.read(MAX_FILE + 1)
        name = re.sub(r"[^\w .()\-]", "_", str(getattr(f, "filename", "") or "file"))[:120]
        mime = sniff(b)
        if len(b) > MAX_FILE:
            refused.append(f"{name}: over 15 MB")
            continue
        if not mime:
            refused.append(f"{name}: not a PDF or image")
            continue
        save_doc(db, r.org_key, b, mime, source="upload", filename=name, sender=sender, note=note, mrn=r.mrn, link_id=r.id)
        saved += 1
    r.uses, r.files = (r.uses or 0) + 1, (r.files or 0) + saved
    db.commit()
    audit.later(org_key=r.org_key, email="upload link", action="create", what="outside records", record_id=f"link {r.id}",
                method="POST", path="/api/public/records/…", status=200, ip=ip, agent=(request.headers.get("user-agent") or "")[:200],
                detail=f"{saved} file(s) received through an upload link")
    if not saved:
        return JSONResponse({"error": "None of the files could be taken: " + "; ".join(refused)}, status_code=400)
    msg = f"Sent {saved} file{'s' if saved != 1 else ''} to the clinic." + (" Not sent: " + "; ".join(refused) + "." if refused else "")
    return {"ok": True, "message": msg, "saved": saved}


# ── fax ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _digits(n) -> str:
    d = re.sub(r"\D", "", str(n or ""))
    return d[-10:] if len(d) >= 10 else d


def _org_for_fax(db, number: str) -> str:
    want = _digits(number)
    if not want:
        return ""
    for row in db.scalars(select(OrgSettings).where(OrgSettings.category == SETTINGS)).all():
        try:
            nums = json.loads(row.data or "{}").get("fax_numbers") or []
        except Exception:
            continue
        if want in {_digits(n) for n in nums}:
            return row.org_key
    return ""


@router.post("/api/intake/fax/inbound")
async def fax_inbound(request: Request, db: Session = Depends(get_db)):
    """A fax provider's webhook. JSON: {to, from, pages, id, file: {name, mime, base64}}, signed with FAX_WEBHOOK_SECRET."""
    secret = os.environ.get("FAX_WEBHOOK_SECRET", "")
    raw = await request.body()
    if not secret:
        return JSONResponse({"error": "Fax intake isn't set up."}, status_code=503)
    sig = (request.headers.get("x-althais-signature") or "").removeprefix("sha256=")
    if not hmac.compare_digest(sig.encode("utf-8", "ignore"), hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest().encode()):
        return JSONResponse({"error": "Bad signature."}, status_code=401)
    if len(raw) > MAX_FILE * 2:
        return JSONResponse({"error": "Too large."}, status_code=413)
    try:
        b = json.loads(raw)
        f = b.get("file") or {}
        data = base64.b64decode(f.get("base64") or "", validate=True)
    except Exception:
        return JSONResponse({"error": "Send JSON with file.base64."}, status_code=400)
    org = _org_for_fax(db, b.get("to"))
    if not org:
        return JSONResponse({"error": "That fax number isn't assigned to a clinic."}, status_code=404)
    mime = sniff(data)
    if not mime or len(data) > MAX_FILE:
        return JSONResponse({"error": "The fax must be a PDF or image under 15 MB."}, status_code=400)
    fid = str(b.get("id") or "")[:128]
    if fid and db.scalar(select(IntakeDoc.id).where(IntakeDoc.org_key == org, IntakeDoc.source == "fax", IntakeDoc.ext_ref == fid)):
        return {"ok": True, "duplicate": True}          # providers retry webhooks
    pages = b.get("pages")
    row = save_doc(db, org, data, mime, source="fax", filename=str(f.get("name") or "fax.pdf")[:120], ext_ref=fid,
                   sender=f"Fax from {str(b.get('from') or 'unknown number')[:40]}" + (f", {int(pages)} page(s)" if str(pages or "").isdigit() else ""))
    return {"ok": True, "id": row.id}


# ── staff: the inbox ───────────────────────────────────────────────────────────────────────────────────────────
@router.get("/api/intake/inbox")
def inbox(status: str = "new", mrn: str = "", user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    q = select(IntakeDoc).where(IntakeDoc.org_key == org)
    if status in ("new", "filed", "dismissed"):
        q = q.where(IntakeDoc.status == status)
    if mrn:
        q = q.where(IntakeDoc.mrn == mrn)
    rows = db.scalars(q.order_by(IntakeDoc.received_at.desc()).limit(300)).all()
    names = _names(db, org)
    counts = {s: 0 for s in ("new", "filed", "dismissed")}
    for s, n in db.execute(select(IntakeDoc.status, func.count(IntakeDoc.id)).where(IntakeDoc.org_key == org).group_by(IntakeDoc.status)).all():
        counts[s] = n
    shas = {}
    out = []
    for r in rows:
        d = doc_dict(r, names)
        if r.sha256 in shas:
            d["duplicate_of"] = shas[r.sha256]
        shas.setdefault(r.sha256, r.id)
        out.append(d)
    return {"docs": out, "counts": counts, "types": DOC_TYPES}


@router.get("/api/intake/docs/{did}")
def doc_get(did: int, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    r = _doc(db, org, did)
    d = doc_dict(r, _names(db, org))
    d["data"] = f"data:{r.mime};base64," + base64.b64encode(_open_bytes(r.data)).decode()
    return JSONResponse(d, headers=NO_STORE)


@router.post("/api/intake/docs/{did}/file")
async def doc_file(did: int, request: Request, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    """File the record to a patient. A dental clinic filing an X-ray image also gets it in the patient's images."""
    org = _staff(user, db)
    r = _doc(db, org, did)
    b = await request.json()
    mrn = str(b.get("mrn") or "").strip()
    if not _patient(db, org, mrn):
        return JSONResponse({"error": "Pick a patient in this clinic."}, status_code=400)
    kind = str(b.get("doc_type") or r.doc_type or "other")
    if kind not in DOC_TYPES:
        return JSONResponse({"error": "Pick what kind of record this is."}, status_code=400)
    m = _open(r.sealed)
    m["title"] = str(b.get("title") or m.get("title") or "")[:200]
    if "note" in b:
        m["note"] = str(b.get("note") or "")[:2000]
    r.mrn, r.doc_type, r.status, r.filed_at, r.filed_by, r.sealed = mrn, kind, "filed", _now(), (user.email or "")[:255], _seal(m)
    image_id = None
    if kind == "xray" and r.mime in ("image/png", "image/jpeg", "image/webp") and auth.org_practice(user, db) == "dental":
        import dental_db as ddb
        img = ddb.Image(org_key=org, mrn=mrn, kind=str(b.get("image_kind") or "pa")[:12], teeth=[str(t) for t in (b.get("teeth") or [])][:32],
                        taken=str(b.get("taken") or "")[:10] or dt.date.today().isoformat(), device="outside records", mime=r.mime,
                        data=f"data:{r.mime};base64," + base64.b64encode(_open_bytes(r.data)).decode())
        db.add(img)
        db.flush()
        image_id = img.id
    db.commit()
    return {"ok": True, "doc": doc_dict(r, _names(db, org)), "image_id": image_id}


@router.post("/api/intake/docs/{did}/dismiss")
async def doc_dismiss(did: int, request: Request, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    r = _doc(db, org, did)
    try:
        b = await request.json()
    except Exception:
        b = {}
    m = _open(r.sealed)
    m["dismissed_reason"] = str(b.get("reason") or "")[:300]
    r.status, r.sealed, r.filed_by, r.filed_at = "dismissed", _seal(m), (user.email or "")[:255], _now()
    db.commit()
    return {"ok": True}


@router.post("/api/intake/docs/{did}/reopen")
def doc_reopen(did: int, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    r = _doc(db, org, did)
    r.status = "new"
    db.commit()
    return {"ok": True}


@router.post("/api/intake/staff-upload")
async def staff_upload(request: Request, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    """Staff dropping in a file they received some other way (email attachment, CD, scanner): it joins the inbox."""
    org = _staff(user, db)
    form = await request.form()
    out = []
    for f in [f for f in form.getlist("files") if hasattr(f, "read")][:MAX_FILES]:
        b = await f.read(MAX_FILE + 1)
        mime = sniff(b)
        if mime and len(b) <= MAX_FILE:
            out.append(save_doc(db, org, b, mime, source="staff", filename=str(f.filename or "file")[:120], sender=f"Added by {user.email}",
                                mrn=str(form.get("mrn") or "")[:64]).id)
    if not out:
        return JSONResponse({"error": "Add a PDF or image under 15 MB."}, status_code=400)
    return {"ok": True, "ids": out}


# ── settings: fax numbers, and what's connected ────────────────────────────────────────────────────────────────
@router.get("/api/intake/settings")
def settings_get(user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    _, doc = _settings(db, org)
    import opendental as od
    import records_network as rn
    od_conn = od.connection(db, org)
    return {"fax_numbers": doc.get("fax_numbers") or [], "fax_ready": bool(os.environ.get("FAX_WEBHOOK_SECRET")),
            "opendental": {"connected": od_conn.get("connected"), "files_ready": od.sftp_ready()}, "network": rn.status(), "types": DOC_TYPES}


@router.post("/api/intake/settings")
async def settings_save(request: Request, user=Depends(require_any_role("admin")), db: Session = Depends(get_db)):
    org = _staff(user, db)
    b = await request.json()
    nums = []
    for n in (b.get("fax_numbers") or [])[:5]:
        d = _digits(n)
        if len(d) != 10:
            return JSONResponse({"error": f"{n} isn't a 10-digit US fax number."}, status_code=400)
        other = _org_for_fax(db, d)
        if other and other != org:
            return JSONResponse({"error": f"{n} already belongs to another clinic."}, status_code=400)
        nums.append(d)
    row, doc = _settings(db, org)
    if not row:
        row = OrgSettings(org_key=org, category=SETTINGS, data="{}")
        db.add(row)
    doc["fax_numbers"] = nums
    row.data = json.dumps(doc)
    db.commit()
    return {"ok": True, "fax_numbers": nums}


# ── Open Dental: the patient's documents in, filed records out ─────────────────────────────────────────────────
def _od(db, org):
    import opendental as od
    key, clinic = od.stored_key(db, org)
    return od, od.Client(key, clinic)


@router.get("/api/intake/opendental/{mrn}/documents")
def od_documents(mrn: str, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    import opendental as od
    pn = od.pat_num(mrn)
    if not pn or not _patient(db, org, mrn):
        return JSONResponse({"error": "This patient didn't come from Open Dental."}, status_code=400)
    try:
        od, cl = _od(db, org)
        docs = od.documents(cl, pn)
    except od.OpenDentalError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    have = {r.ext_ref for r in db.scalars(select(IntakeDoc).where(IntakeDoc.org_key == org, IntakeDoc.source == "opendental", IntakeDoc.mrn == mrn)).all()}
    for d in docs:
        d["imported"] = ("doc:" + d["doc_num"]) in have
    return {"documents": docs, "files_ready": od.sftp_ready()}


@router.post("/api/intake/opendental/{mrn}/documents/{doc_num}")
def od_import(mrn: str, doc_num: str, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    import opendental as od
    if not od.pat_num(mrn) or not _patient(db, org, mrn) or not doc_num.isdigit():
        return JSONResponse({"error": "This patient didn't come from Open Dental."}, status_code=400)
    try:
        od, cl = _od(db, org)
        data, name = od.download(cl, doc_num)
    except od.OpenDentalError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    mime = sniff(data)
    if not mime:
        return JSONResponse({"error": "That file isn't a PDF or image Althais can keep."}, status_code=400)
    row = save_doc(db, org, data, mime, source="opendental", filename=name, sender="Open Dental Imaging module", mrn=mrn,
                   ext_ref="doc:" + doc_num, status="filed", by=user.email or "")
    return {"ok": True, "doc": doc_dict(row, _names(db, org))}


@router.post("/api/intake/docs/{did}/opendental")
def od_push(did: int, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    r = _doc(db, org, did)
    import opendental as od
    pn = od.pat_num(r.mrn)
    if r.status != "filed" or not pn:
        return JSONResponse({"error": "File it to a patient that came from Open Dental first."}, status_code=400)
    m = _open(r.sealed)
    try:
        od, cl = _od(db, org)
        res = od.upload(cl, pn, _open_bytes(r.data), r.mime, m.get("title") or DOC_TYPES.get(r.doc_type, "Outside record"))
    except od.OpenDentalError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    m["opendental_doc"] = str((res or {}).get("DocNum") or "")
    r.sealed = _seal(m)
    db.commit()
    return {"ok": True, "doc_num": m["opendental_doc"]}


# ── records network ────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/api/intake/network/{mrn}")
def network_query(mrn: str, user=Depends(require_any_role(*STAFF)), db: Session = Depends(get_db)):
    org = _staff(user, db)
    import records_network as rn
    p = _patient(db, org, mrn)
    if not p:
        return JSONResponse({"error": "That patient isn't in this clinic."}, status_code=404)
    try:
        extra = json.loads(p.data or "{}")
    except Exception:
        extra = {}
    try:
        found = rn.current().find({"name": p.name, "dob": p.dob, "sex": p.sex, "synthetic": extra.get("synthetic") or extra.get("sample")})
    except rn.NetworkError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    have = {r.ext_ref for r in db.scalars(select(IntakeDoc).where(IntakeDoc.org_key == org, IntakeDoc.source == "network", IntakeDoc.mrn == mrn)).all()}
    added = 0
    for f in found:
        ref = f"{rn.current().name}:{f.ref}"[:128]
        if ref in have or not sniff(f.data):
            continue
        save_doc(db, org, f.data, sniff(f.data), source="network", filename=f.title[:120], title=f.title, sender=f.source, mrn=mrn,
                 doc_type=f.doc_type if f.doc_type in DOC_TYPES else "other", ext_ref=ref)
        added += 1
    return {"ok": True, "found": len(found), "added": added}
