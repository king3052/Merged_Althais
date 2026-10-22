"""
patient_intake.py, "Scan to Autofill" for new-patient registration.

Front desk staff upload an insurance card, a photo ID or an intake form; Claude reads each one into structured fields
with a confidence for every field; a review screen shows the registration form pre-filled next to the documents; the
person checks and edits everything and clicks Confirm & Create Patient. Only then is a patient created (by the EMR,
through /api/patients, exactly like a manual registration), and the documents are attached to that patient.

PHI rules followed here:
  - documents and extraction results are stored encrypted (AES-256-GCM, key derived from PHI_ENC_KEY or SECRET_KEY)
  - nothing logs PHI: the audit log records who did what to which document/patient, never field values
  - every endpoint checks the user's role and that the document belongs to their clinic
  - the AI provider is Anthropic (Claude), which signs a BAA; set ANTHROPIC_API_KEY (and INTAKE_MODEL to override)
The extraction prompt lives in prompts/patient_intake_extraction.md so it can be tuned without code changes.
"""

import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import DateTime, Integer, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.concurrency import run_in_threadpool

from auth import Base, User, engine, ensure_product, get_db, require_any_role
import eligibility as elig

router = APIRouter()
INTAKE_ROLES = ("admin", "biller", "provider", "front_desk")
INTAKE_MODEL = os.environ.get("INTAKE_MODEL", "claude-opus-5-5")
EXTRACT_TIMEOUT = int(os.environ.get("INTAKE_TIMEOUT_SECONDS", "75"))
MAX_BYTES = 15 * 1024 * 1024
PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "patient_intake_extraction.md")
TAGS = {"insurance_front": "Insurance card front", "insurance_back": "Insurance card back", "photo_id": "Photo ID",
        "intake_form": "Intake form", "consent_other": "Consent/other"}


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ──────────────────────────────────────────────────────────────────────────
#  Storage (encrypted) and audit
# ──────────────────────────────────────────────────────────────────────────
class PatientDocument(Base):
    __tablename__ = "patient_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    intake_id: Mapped[str] = mapped_column(String(40), index=True)
    mrn: Mapped[str] = mapped_column(String(64), default="", index=True)      # set when the patient is confirmed
    tag: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(Text)                               # encrypted (a file name can hold a patient's name)
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)                          # encrypted file
    extraction: Mapped[str] = mapped_column(Text, default="")                 # encrypted JSON
    status: Mapped[str] = mapped_column(String(16), default="uploaded")       # uploaded | extracted | failed | attached
    uploaded_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    attached_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


class IntakeAudit(Base):
    """Who uploaded, extracted, viewed, reviewed, edited and confirmed. Field names only, never values."""
    __tablename__ = "patient_intake_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    intake_id: Mapped[str] = mapped_column(String(40), index=True)
    mrn: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class IntakeResult(Base):
    """Per-field provenance of a confirmed registration (ai | manual | ai_edited | ai_cleared) for accuracy tracking."""
    __tablename__ = "patient_intake_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    intake_id: Mapped[str] = mapped_column(String(40), index=True)
    mrn: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(Text, default="{}")
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


Base.metadata.create_all(engine)


# AES-256-GCM (crypto_box.py). Files saved before that (Fernet) still open, and are upgraded by `python crypto_box.py reencrypt`.
def seal(b: bytes) -> bytes:
    import crypto_box
    return crypto_box.seal_bytes(b, "phi", "PHI_ENC_KEY")


def unseal(b: bytes) -> bytes:
    import crypto_box
    return crypto_box.open_bytes(b, "phi", "PHI_ENC_KEY", b"althais-phi|")


def seal_text(s: str) -> str:
    return seal((s or "").encode()).decode() if s else ""


def unseal_text(s: str) -> str:
    return unseal(s.encode()).decode() if s else ""


def audit(db: Session, org: str, intake_id: str, user: User, action: str, detail: str = "", mrn: str = ""):
    db.add(IntakeAudit(org_key=org, intake_id=intake_id, mrn=mrn, actor=user.email or str(user.id), action=action, detail=detail[:2000]))


# ──────────────────────────────────────────────────────────────────────────
#  Parsing, merging and validation (pure functions; tested in tests/)
# ──────────────────────────────────────────────────────────────────────────
DEMOGRAPHICS = ["first_name", "middle_name", "last_name", "date_of_birth", "sex", "address_street", "address_city", "address_state",
                "address_zip", "phone", "email", "emergency_contact_name", "emergency_contact_phone"]
INSURANCE = ["payer_name", "member_id", "group_number", "plan_name", "subscriber_name", "subscriber_date_of_birth",
             "relationship_to_patient", "payer_phone", "rx_bin", "rx_pcn"]
INTAKE = ["medications", "allergies", "medical_history", "preferred_pharmacy", "referring_provider"]
FIELDS = DEMOGRAPHICS + INSURANCE + INTAKE
LIST_FIELDS = {"medications", "allergies", "medical_history"}
DATE_FIELDS = {"date_of_birth", "subscriber_date_of_birth"}
PHONE_FIELDS = {"phone", "emergency_contact_phone", "payer_phone"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD "
                "TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP".split())
CONF = {"high", "medium", "low"}


def _json_from(raw):
    if isinstance(raw, dict):
        return raw
    t = str(raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    return json.loads(m.group(0))


def _conf(c):
    if isinstance(c, (int, float)):
        return "high" if c >= 0.9 else "medium" if c >= 0.7 else "low"
    c = str(c or "").strip().lower()
    return c if c in CONF else "low"


def _date(v):
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt.date.fromisoformat(s)
            return s, True
        except ValueError:
            return s, False
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:   # US order on US documents; anything that isn't a real date stays as printed, marked low
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat(), True
        except ValueError:
            return s, False
    return s, False


def normalize_value(key: str, v):
    """(value, still_valid). Never invents: anything that doesn't fit the field's format is kept as printed, marked low."""
    if v is None:
        return None, True
    if key in LIST_FIELDS:
        items = v if isinstance(v, list) else re.split(r"[;\n]+|,\s(?=[A-Z])", str(v))
        items = [str(x).strip(" -•\t") for x in items if str(x).strip(" -•\t")]
        return (items or None), True
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "n/a", "na", "unknown", "-"):
        return None, True
    if key in DATE_FIELDS:
        return _date(s)
    if key in PHONE_FIELDS:
        d = re.sub(r"\D", "", s)
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        return (d, True) if len(d) == 10 else (s, False)
    if key == "address_state":
        u = s.upper().strip(". ")
        return (u, True) if u in US_STATES else (s, False)
    if key == "address_zip":
        d = re.sub(r"[^\d]", "", s)
        return (d[:5] + ("-" + d[5:] if len(d) == 9 else ""), True) if len(d) in (5, 9) else (s, False)
    if key == "sex":
        u = s.upper()[:1]
        return (u, True) if u in ("F", "M", "X") else (None, True)
    if key == "email":
        return (s.lower(), True) if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else (s, False)
    return re.sub(r"\s+", " ", s), True


def parse_extraction(raw) -> dict:
    """Claude's reply -> {"fields": {key: {value, confidence, box}}, "conflicts": [...], "kind", "quality"}.
    Unknown keys are dropped, nulls are dropped, and values that don't fit their field's format are marked low."""
    obj = _json_from(raw)
    src = obj.get("fields") if isinstance(obj.get("fields"), dict) else obj
    out = {}
    for key in FIELDS:
        f = src.get(key)
        if f is None:
            continue
        if not isinstance(f, dict):
            f = {"value": f, "confidence": "medium"}
        val, ok = normalize_value(key, f.get("value"))
        if val is None:
            continue
        box = f.get("box") if isinstance(f.get("box"), list) and len(f.get("box")) == 4 else None
        try:
            box = [max(0.0, min(1.0, float(b))) for b in box] if box else None
        except (TypeError, ValueError):
            box = None
        out[key] = {"value": val, "confidence": _conf(f.get("confidence")) if ok else "low", "box": box}
    conflicts = [{"key": c.get("key"), "values": [str(v) for v in (c.get("values") or [])][:5]}
                 for c in (obj.get("conflicts") or []) if isinstance(c, dict) and c.get("key") in FIELDS]
    return {"fields": out, "conflicts": conflicts, "kind": str(obj.get("document_kind") or ""),
            "quality": [str(q) for q in (obj.get("quality") or [])][:6]}


def _same(key, a, b) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return True    # lists are combined, not compared
    n = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
    return n(a) == n(b)


def merge_documents(docs: list) -> dict:
    """docs: [{"id", "tag", "parsed"}] -> {"fields": {key: {value, confidence, docId, tag, box}}, "conflicts": {key: [options]}}.
    When documents disagree on a field (e.g. the name on the ID vs the insurance card), the field is left empty and
    every value is offered as a conflict for a person to choose. Lists (medications, allergies, history) are combined."""
    seen = {}
    for d in docs:
        for key, f in ((d.get("parsed") or {}).get("fields") or {}).items():
            seen.setdefault(key, []).append(dict(f, docId=d.get("id"), tag=d.get("tag")))
        for c in (d.get("parsed") or {}).get("conflicts") or []:   # a document that disagreed with itself
            seen.setdefault(c["key"], [])
            for v in c["values"]:
                seen[c["key"]].append({"value": v, "confidence": "low", "docId": d.get("id"), "tag": d.get("tag"), "box": None, "inner": True})
    fields, conflicts = {}, {}
    for key, vals in seen.items():
        if not vals:
            continue
        if key in LIST_FIELDS:
            items, srcs = [], []
            for v in vals:
                for it in (v["value"] if isinstance(v["value"], list) else [v["value"]]):
                    if not any(re.sub(r"\W", "", it.lower()) == re.sub(r"\W", "", x.lower()) for x in items):
                        items.append(it)
                srcs.append(v)
            fields[key] = {"value": items, "confidence": min((v["confidence"] for v in srcs), key=["low", "medium", "high"].index),
                           "docId": srcs[0]["docId"], "tag": srcs[0]["tag"], "box": srcs[0].get("box")}
            continue
        distinct = []
        for v in vals:
            if not any(_same(key, v["value"], x["value"]) for x in distinct):
                distinct.append(v)
        if len(distinct) > 1:
            conflicts[key] = [{"value": v["value"], "docId": v["docId"], "tag": v["tag"], "confidence": v["confidence"], "box": v.get("box")} for v in distinct]
            continue
        best = max(vals, key=lambda v: ["low", "medium", "high"].index(v["confidence"]))
        fields[key] = {"value": best["value"], "confidence": best["confidence"], "docId": best["docId"], "tag": best["tag"], "box": best.get("box")}
    return {"fields": fields, "conflicts": conflicts}


REQUIRED = {"first_name": "First name", "last_name": "Last name", "date_of_birth": "Date of birth"}


def validate_patient(p: dict, today: Optional[dt.date] = None) -> dict:
    """{field: message} for everything that would stop a clean registration. Empty means valid."""
    today = today or dt.date.today()
    err = {}
    for k, label in REQUIRED.items():
        if not str(p.get(k) or "").strip():
            err[k] = label + " is required."
    for k in DATE_FIELDS:
        v = str(p.get(k) or "").strip()
        if not v or k in err:
            continue
        try:
            d = dt.date.fromisoformat(v)
        except ValueError:
            err[k] = "Use a real date (MM/DD/YYYY)."
            continue
        if d > today:
            err[k] = "Can't be in the future."
        elif d.year < 1900:
            err[k] = "Check the year."
    z = str(p.get("address_zip") or "").strip()
    if z and not re.fullmatch(r"\d{5}(-?\d{4})?", z):
        err["address_zip"] = "ZIP must be 5 or 9 digits."
    for k in PHONE_FIELDS:
        v = str(p.get(k) or "").strip()
        if v and len(re.sub(r"\D", "", v).lstrip("1") if len(re.sub(r"\D", "", v)) == 11 else re.sub(r"\D", "", v)) != 10:
            err[k] = "Phone must be 10 digits."
    st = str(p.get("address_state") or "").strip().upper()
    if st and st not in US_STATES:
        err["address_state"] = "Use a 2-letter US state code."
    e = str(p.get("email") or "").strip()
    if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        err["email"] = "Check the email address."
    if str(p.get("sex") or "").strip() and str(p.get("sex")).upper() not in ("F", "M", "X"):
        err["sex"] = "Choose F, M or X."
    return err


# ──────────────────────────────────────────────────────────────────────────
#  Files and the AI call
# ──────────────────────────────────────────────────────────────────────────
def prepare_upload(raw: bytes, content_type: str, filename: str):
    """(bytes, content_type) ready to store and send: HEIC/HEIF converted to JPEG, large photos scaled down."""
    ct, name = (content_type or "").lower(), (filename or "").lower()
    if ct == "application/pdf" or name.endswith(".pdf"):
        if not raw.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="That file isn't a readable PDF.")
        return raw, "application/pdf"
    is_heic = ct in ("image/heic", "image/heif") or name.endswith((".heic", ".heif"))
    if not (ct.startswith("image/") or is_heic or name.endswith((".jpg", ".jpeg", ".png", ".webp"))):
        raise HTTPException(status_code=400, detail="Upload a JPG, PNG, HEIC or PDF.")
    from PIL import Image, ImageOps
    if is_heic:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise HTTPException(status_code=400, detail="HEIC photos aren't supported on this server yet. Save it as JPG and upload again.")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    except Exception:
        raise HTTPException(status_code=400, detail="That image couldn't be opened.")
    if not is_heic and im.format in ("JPEG", "PNG") and len(raw) <= 4_500_000 and max(im.size) <= 3000:
        return raw, "image/jpeg" if im.format == "JPEG" else "image/png"
    im = im.convert("RGB")
    im.thumbnail((2600, 2600))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def _prompt(tag: str) -> str:
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read().format(tag=TAGS.get(tag, "unknown"))


def claude_extract(data: bytes, content_type: str, tag: str) -> str:
    """One document through Claude; returns the raw reply text. Raises on any failure."""
    import anthropic
    client = anthropic.Anthropic(timeout=EXTRACT_TIMEOUT, max_retries=1)
    b64 = base64.standard_b64encode(data).decode()
    block = ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}} if content_type == "application/pdf"
             else {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}})
    msg = client.messages.create(model=INTAKE_MODEL, max_tokens=4000,
                                 messages=[{"role": "user", "content": [block, {"type": "text", "text": _prompt(tag)}]}])
    return "".join(getattr(b, "text", "") for b in msg.content)


EXTRACTOR = claude_extract   # replaced in tests


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ──────────────────────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────────────────────
def _org_namespace(user) -> str:
    """The clinic's key, the same derivation as main._org_namespace (patients and claims use it)."""
    base = (user.organization or user.email or "unknown").strip().lower()
    return "org_" + re.sub(r"[^a-z0-9]+", "_", base).strip("_")


def _user(user=Depends(require_any_role(*INTAKE_ROLES)), db: Session = Depends(get_db)):
    ensure_product(user, db)   # the EMR (full suite)
    return user


def _doc(db, user, doc_id) -> PatientDocument:
    d = db.get(PatientDocument, doc_id)
    if not d or d.org_key != _org_namespace(user):
        raise HTTPException(status_code=404, detail="Not found.")
    return d


@router.post("/api/intake/documents")
async def upload(file: UploadFile = File(...), tag: str = Form(...), intake_id: str = Form(""), user=Depends(_user), db: Session = Depends(get_db)):
    if tag not in TAGS:
        raise HTTPException(status_code=400, detail="Choose what this document is.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="That file is over 15 MB.")
    data, ct = await run_in_threadpool(prepare_upload, raw, file.content_type or "", file.filename or "")
    intake_id = re.sub(r"[^a-zA-Z0-9]", "", intake_id)[:40] or secrets.token_hex(12)
    org = _org_namespace(user)
    d = PatientDocument(org_key=org, intake_id=intake_id, tag=tag, filename=seal_text(file.filename or "document"), content_type=ct, size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), data=seal(data), uploaded_by=user.id)
    db.add(d)
    db.flush()
    audit(db, org, intake_id, user, "uploaded", f"document {d.id} ({TAGS[tag]}, {ct}, {len(data)} bytes)")
    db.commit()
    return {"id": d.id, "intakeId": intake_id, "tag": tag, "contentType": ct, "size": len(data)}


@router.patch("/api/intake/documents/{doc_id}")
def retag(doc_id: int, body: dict = Body(...), user=Depends(_user), db: Session = Depends(get_db)):
    d = _doc(db, user, doc_id)
    if body.get("tag") in TAGS and d.status != "attached":
        d.tag, d.status = body["tag"], "uploaded"
        audit(db, d.org_key, d.intake_id, user, "retagged", f"document {d.id} as {TAGS[d.tag]}")
        db.commit()
    return {"ok": True}


@router.delete("/api/intake/documents/{doc_id}")
def remove(doc_id: int, user=Depends(_user), db: Session = Depends(get_db)):
    d = _doc(db, user, doc_id)
    if d.status == "attached":
        raise HTTPException(status_code=400, detail="This document is already part of a patient's record.")
    audit(db, d.org_key, d.intake_id, user, "removed", f"document {d.id}")
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.get("/api/intake/documents/{doc_id}/file")
def view(doc_id: int, user=Depends(_user), db: Session = Depends(get_db)):
    d = _doc(db, user, doc_id)
    audit(db, d.org_key, d.intake_id, user, "viewed", f"document {d.id}", mrn=d.mrn)
    db.commit()
    return Response(content=unseal(d.data), media_type=d.content_type,
                    headers={"Cache-Control": "no-store, private", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


def _extract_one(doc_id: int, data: bytes, ct: str, tag: str):
    try:
        return doc_id, parse_extraction(EXTRACTOR(data, ct, tag)), ""
    except Exception as e:
        return doc_id, None, type(e).__name__


@router.post("/api/intake/{intake_id}/extract")
def extract(intake_id: str, user=Depends(_user), db: Session = Depends(get_db)):
    """Read every document in this intake. Returns what could be read even when some documents fail, so the person
    always lands in the form with whatever was extracted."""
    org = _org_namespace(user)
    docs = db.scalars(select(PatientDocument).where(PatientDocument.org_key == org, PatientDocument.intake_id == intake_id)).all()
    if not docs:
        raise HTTPException(status_code=404, detail="Upload a document first.")
    if not available():
        audit(db, org, intake_id, user, "extract_unavailable", f"{len(docs)} documents")
        db.commit()
        return {"status": "unavailable", "fields": {}, "conflicts": {}, "documents": [_doc_json(d) for d in docs],
                "message": "Automatic reading isn't set up on this server. Fill in the form from the documents."}
    todo = [(d.id, unseal(d.data), d.content_type, d.tag) for d in docs if d.status != "extracted" or not d.extraction]
    results, failed = {}, []
    if todo:
        pool = ThreadPoolExecutor(max_workers=min(4, len(todo)))
        futs = [pool.submit(_extract_one, *t) for t in todo]
        for f in futs:
            try:
                did, parsed, err = f.result(timeout=EXTRACT_TIMEOUT + 15)
            except FutureTimeout:
                continue
            if parsed is None:
                failed.append(did)
            else:
                results[did] = parsed
        pool.shutdown(wait=False, cancel_futures=True)
    parsed_docs = []
    tried = {t[0] for t in todo}
    for d in docs:
        if d.id in results:
            d.extraction, d.status = seal_text(json.dumps(results[d.id])), "extracted"
        elif d.id in tried:          # failed, or ran out of time
            d.status = "failed"
        if d.status == "extracted" and d.extraction:
            parsed_docs.append({"id": d.id, "tag": d.tag, "parsed": json.loads(unseal_text(d.extraction))})
    merged = merge_documents(parsed_docs)
    n_fail = sum(1 for d in docs if d.status == "failed")
    audit(db, org, intake_id, user, "extracted", f"{len(parsed_docs)} read, {n_fail} failed; {len(merged['fields'])} fields, {len(merged['conflicts'])} conflicts; model {INTAKE_MODEL}")
    db.commit()
    status = "ok" if not n_fail else ("partial" if parsed_docs else "failed")
    msg = "" if status == "ok" else ("Some documents couldn’t be read. Check those fields by hand." if status == "partial" else
                                     "The documents couldn’t be read automatically. Fill in the form from them.")
    quality = {d["id"]: d["parsed"].get("quality") for d in parsed_docs if d["parsed"].get("quality")}
    return {"status": status, "message": msg, "fields": merged["fields"], "conflicts": merged["conflicts"], "quality": quality,
            "documents": [_doc_json(d) for d in docs]}


def _doc_json(d: PatientDocument):
    return {"id": d.id, "tag": d.tag, "tagLabel": TAGS.get(d.tag, d.tag), "contentType": d.content_type, "status": d.status,
            "uploaded": d.created_at.isoformat() if d.created_at else "", "filename": unseal_text(d.filename) if d.filename else ""}


@router.post("/api/intake/{intake_id}/confirm")
async def confirm(intake_id: str, body: dict = Body(...), user=Depends(_user), db: Session = Depends(get_db)):
    """The person clicked Confirm & Create Patient and the EMR created the patient. Attach the documents, record which
    fields came from the AI and which were typed or changed, and run the eligibility check."""
    org = _org_namespace(user)
    mrn = re.sub(r"[^A-Za-z0-9-]", "", str(body.get("mrn") or ""))[:64]
    if not mrn:
        raise HTTPException(status_code=400, detail="The patient's MRN is missing.")
    docs = db.scalars(select(PatientDocument).where(PatientDocument.org_key == org, PatientDocument.intake_id == intake_id)).all()
    for d in docs:
        d.mrn, d.status, d.attached_at = mrn, "attached", _now()
    prov = {k: v for k, v in (body.get("provenance") or {}).items() if k in FIELDS and v in ("ai", "manual", "ai_edited", "ai_cleared")}
    db.add(IntakeResult(org_key=org, intake_id=intake_id, mrn=mrn, provenance=json.dumps(prov), model=INTAKE_MODEL))
    counts = {s: sum(1 for v in prov.values() if v == s) for s in ("ai", "manual", "ai_edited", "ai_cleared")}
    audit(db, org, intake_id, user, "reviewed", f"{counts['ai']} AI-filled kept, {counts['ai_edited']} AI-filled edited, {counts['ai_cleared']} cleared, {counts['manual']} typed", mrn=mrn)
    if counts["ai_edited"] or counts["ai_cleared"]:
        audit(db, org, intake_id, user, "edited", "fields: " + ", ".join(sorted(k for k, v in prov.items() if v in ("ai_edited", "ai_cleared"))), mrn=mrn)
    audit(db, org, intake_id, user, "confirmed", f"patient created with {len(docs)} documents attached", mrn=mrn)
    db.commit()
    ins = body.get("insurance") or {}
    result = None
    if ins.get("payer") and ins.get("member_id"):
        req = elig.EligibilityRequest(payer=ins.get("payer", ""), member_id=ins.get("member_id", ""), patient_dob=ins.get("patient_dob", ""),
                                      subscriber_first=ins.get("subscriber_first", ""), subscriber_last=ins.get("subscriber_last", ""),
                                      subscriber_dob=ins.get("subscriber_dob", ""), relationship=ins.get("relationship", "self"), payer_id=ins.get("payer_id", ""),
                                      **_billing_ids(db, user))
        result = (await run_in_threadpool(elig.check_eligibility, req)).as_dict()
        audit(db, org, intake_id, user, "eligibility", f"status {result['status']} via {result['source'] or 'none'}", mrn=mrn)
        db.commit()
    return {"ok": True, "attached": len(docs), "eligibility": result}


def _billing_ids(db, user):
    try:
        import billing_activation as ba
        _, data = ba.load_profile(db, ba.org_of(user))
        return {"provider_npi": (data or {}).get("npi", ""), "provider_tin": (data or {}).get("tin", "")}
    except Exception:
        return {"provider_npi": "", "provider_tin": ""}


@router.get("/api/patients/{mrn}/scans")
def patient_scans(mrn: str, user=Depends(_user), db: Session = Depends(get_db)):
    org = _org_namespace(user)
    docs = db.scalars(select(PatientDocument).where(PatientDocument.org_key == org, PatientDocument.mrn == mrn, PatientDocument.status == "attached")
                      .order_by(PatientDocument.created_at.desc())).all()
    return {"documents": [_doc_json(d) for d in docs]}
