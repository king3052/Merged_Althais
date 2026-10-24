"""
claim_attachments.py: X-rays, periodontal charts, photos and narratives sent with a claim.

Dental plans hold crowns, buildups, root canals, scaling and root planing, surgical extractions and implants until they
see the X-ray and a narrative. The clinic adds them to the claim here; when the claim is sent they go with it:

  1. Every file is turned into PDF pages when it's added (JPEG, PNG, HEIC, TIFF, BMP, GIF, WebP photos and X-ray
     exports, or a PDF as it is). A narrative typed in Althais becomes a one-page letter. Everything is stored
     encrypted (AES-256-GCM, crypto_box.py), names included.
  2. The claim gets one attachment control number (ACN). The 837 carries it in PWK06 with the report type in PWK01
     (RB radiology films, P6 periodontal charts, DG diagnostic report, OZ other support data) and PWK02 EL.
  3. Right after the claim is accepted, the documents go up as a single PDF named <ACN>.pdf (Claim.MD's automated
     PDF attachments). Claim.MD holds the claim pending documentation until it arrives, and forwards it to payers
     that take electronic attachments.

Once a claim has been sent, its documents can't change (the payer already has the ACN). If the PDF upload fails,
the claim is still sent, the documents are marked "not sent", and "Send again" retries with the same ACN.
"""
import datetime as dt
import io
import re
import secrets
import unicodedata
from datetime import timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import DateTime, Integer, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import Base, User, engine, ensure_product, get_db, require_any_role, require_user

router = APIRouter()

KINDS = {"xray": "X-ray", "perio": "Periodontal chart", "narrative": "Narrative", "photo": "Intraoral photo", "report": "Report", "other": "Other document"}
REPORT_TYPE = {"xray": "RB", "perio": "P6", "report": "DG", "narrative": "OZ", "photo": "OZ", "other": "OZ"}
MAX_FILE = 10 * 1024 * 1024        # per file as uploaded
MAX_CLAIM_PDF = 20 * 1024 * 1024   # everything for one claim, as sent
MAX_FILES = 20
IMAGE_TYPES = ("image/jpeg", "image/png", "image/heic", "image/heif", "image/tiff", "image/bmp", "image/gif", "image/webp")


def _now():
    return dt.datetime.now(timezone.utc).replace(tzinfo=None)


class ClaimAttachment(Base):
    __tablename__ = "claim_attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    claim_id: Mapped[str] = mapped_column(String(64), index=True)
    acn: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(16), default="other")
    name: Mapped[str] = mapped_column(Text, default="")            # sealed
    caption: Mapped[str] = mapped_column(Text, default="")         # sealed
    pages: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)               # sealed PDF
    status: Mapped[str] = mapped_column(String(16), default="ready")   # ready | sent | failed
    message: Mapped[str] = mapped_column(String(300), default="")
    uploaded_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


Base.metadata.create_all(engine)


# ── encryption ───────────────────────────────────────────────────────────────
def _seal(b: bytes) -> bytes:
    import crypto_box
    return crypto_box.seal_bytes(b, "claim-attachment", "PHI_ENC_KEY")


def _open(b: bytes) -> bytes:
    import crypto_box
    return crypto_box.open_bytes(b, "claim-attachment", "PHI_ENC_KEY")


def _seal_text(s: str) -> str:
    return _seal((s or "").encode()).decode() if s else ""


def _open_text(s: str) -> str:
    return _open(s.encode()).decode() if s else ""


# ── turning files into PDF pages ─────────────────────────────────────────────
class BadFile(ValueError):
    pass


def _latin(s: str) -> str:
    """The PDF's built-in font is Latin-1: keep accents, swap typographic quotes and dashes, drop what can't print."""
    s = (s or "").replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = s.replace("•", "-").replace("…", "...").replace(" ", " ")
    out = []
    for ch in unicodedata.normalize("NFC", s):
        try:
            ch.encode("latin-1")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base or "?")
    return "".join(out)


def _pdf():
    from fpdf import FPDF
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(True, margin=54)
    pdf.set_margins(54, 54, 54)
    return pdf


def _header(pdf, title: str, sub: str):
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 16, _latin(title), new_x="LMARGIN", new_y="NEXT")
    if sub:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(0, 12, _latin(sub), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(6)


def image_to_pdf(data: bytes, title: str, sub: str) -> tuple:
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise BadFile("That image couldn't be read. Export the X-ray or photo as JPEG, PNG or PDF and add it again.")
    frames = []
    try:
        n = getattr(im, "n_frames", 1)
    except Exception:
        n = 1
    for i in range(min(n, 20)):          # multi-page TIFFs: every page
        try:
            im.seek(i)
        except EOFError:
            break
        f = ImageOps.exif_transpose(im.copy())
        f = f.convert("L") if f.mode in ("L", "I;16", "I", "1") else f.convert("RGB")
        f.thumbnail((2400, 2400))
        frames.append(f)
    pdf = _pdf()
    for f in frames:
        pdf.add_page()
        _header(pdf, title, sub)
        buf = io.BytesIO()
        f.save(buf, "JPEG", quality=88)
        w_max, h_max = pdf.w - 108, pdf.h - pdf.get_y() - 54
        scale = min(w_max / f.width, h_max / f.height, 1.0 if f.width > 300 else 3.0)
        pdf.image(buf, x=54 + (w_max - f.width * scale) / 2, y=pdf.get_y(), w=f.width * scale, h=f.height * scale)
    return bytes(pdf.output()), len(frames)


def pdf_check(data: bytes) -> int:
    from pypdf import PdfReader
    if not data.startswith(b"%PDF"):
        raise BadFile("That file isn't a PDF.")
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            raise BadFile("That PDF is password-protected. Save it without a password and add it again.")
        return len(r.pages)
    except BadFile:
        raise
    except Exception:
        raise BadFile("That PDF couldn't be read. Save it again from the program that made it.")


def narrative_to_pdf(text: str, title: str, sub: str) -> tuple:
    pdf = _pdf()
    pdf.add_page()
    _header(pdf, title, sub)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 15, _latin(text.strip()), align="L")
    return bytes(pdf.output()), pdf.page_no()


def perio_to_pdf(readings: dict, title: str, sub: str) -> tuple:
    """A periodontal chart as a table payers can read: six probing depths per tooth (facial DB B MB, lingual DL L ML),
    bleeding sites, recession, mobility and furcation."""
    pdf = _pdf()
    pdf.add_page()
    _header(pdf, title, sub)
    heads = [("Tooth", 40), ("Facial PD (DB B MB)", 108), ("Lingual PD (DL L ML)", 108), ("Bleeding", 60), ("Recession", 84), ("Mob", 32), ("Furc", 32)]
    pdf.set_font("Helvetica", "B", 9)
    for h, w in heads:
        pdf.cell(w, 16, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    key = lambda t: (0, int(t)) if str(t).isdigit() else (1, str(t))
    for t in sorted((readings or {}).keys(), key=key):
        r = readings[t] or {}
        pd_ = list(r.get("pd") or []) + [""] * 6
        rec = list(r.get("rec") or []) + [""] * 6
        bop = list(r.get("bop") or [])
        deep = any(str(x).isdigit() and int(x) >= 5 for x in pd_[:6])
        cells = [str(t), " ".join(str(x) for x in pd_[:3]), " ".join(str(x) for x in pd_[3:6]), str(sum(1 for x in bop if x)) + "/6",
                 " ".join(str(x) for x in rec[:6]).strip(), str(r.get("mob") or ""), str(r.get("furc") or "")]
        for (h, w), val in zip(heads, cells):
            pdf.set_font("Helvetica", "B" if deep and h.startswith(("Facial", "Lingual")) else "", 9)
            pdf.cell(w, 14, _latin(val), border=1, align="C")
        pdf.ln()
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 10, "Probing depths in mm; depths of 5 mm or more in bold. Bleeding: sites bleeding on probing out of six.", align="L")
    return bytes(pdf.output()), pdf.page_no()


def data_url_image(url: str, title: str, sub: str) -> tuple:
    """An image kept as a data: URL (the dental EMR's uploads) as PDF pages."""
    import base64
    m = re.match(r"^data:(image/[a-z+.-]+);base64,(.*)$", url or "", re.S)
    if not m:
        raise BadFile("That image isn't stored in a form that can be sent.")
    if "svg" in m.group(1):
        raise BadFile("Drawn (SVG) images can't be sent to payers. Upload the X-ray as JPEG or PNG.")
    return image_to_pdf(base64.b64decode(m.group(2)), title, sub)


def to_pdf(data: bytes, content_type: str, filename: str, title: str, sub: str) -> tuple:
    ct, fn = (content_type or "").lower(), (filename or "").lower()
    if data.startswith(b"%PDF") or ct == "application/pdf" or fn.endswith(".pdf"):
        return data, pdf_check(data)
    if fn.endswith((".dcm", ".dicom")) or ct == "application/dicom":
        raise BadFile("DICOM files can't be sent to payers. Export the X-ray from your imaging software as JPEG, PNG or PDF.")
    if ct in IMAGE_TYPES or ct.startswith("image/") or re.search(r"\.(jpe?g|png|heic|heif|tiff?|bmp|gif|webp)$", fn):
        return image_to_pdf(data, title, sub)
    raise BadFile("Add a JPEG, PNG, HEIC, TIFF or PDF file.")


def combine(rows) -> bytes:
    """One PDF for the claim, in the order the documents were added."""
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for r in rows:
        w.append(PdfReader(io.BytesIO(_open(r.data))))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def report_type(kinds) -> str:
    """One PWK per claim: the most specific type present (X-rays first, since that's what dental plans ask for)."""
    for k in ("xray", "perio", "report"):
        if k in kinds:
            return REPORT_TYPE[k]
    return "OZ"


# ── what the claim gate uses ─────────────────────────────────────────────────
def rows_for(db: Session, org: str, claim_id: str):
    return db.scalars(select(ClaimAttachment).where(ClaimAttachment.org_key == org, ClaimAttachment.claim_id == str(claim_id)[:64])
                      .order_by(ClaimAttachment.id)).all()


def bundle(db: Session, org: str, claim_id: str):
    """(acn, report type, combined PDF, rows) for a claim's documents, or None when it has none."""
    rows = rows_for(db, org, claim_id)
    if not rows:
        return None
    return rows[0].acn, report_type({r.kind for r in rows}), combine(rows), rows


def mark(rows, ok: bool, message: str = ""):
    for r in rows:
        r.status = "sent" if ok else "failed"
        r.message = (message or "")[:300]
        if ok:
            r.sent_at = _now()


def send(db: Session, org: str, claim_id: str, connector) -> dict:
    """Send (or resend) a sent claim's documents with its ACN."""
    b = bundle(db, org, claim_id)
    if not b:
        return {"ok": True, "sent": 0}
    acn, _, pdf, rows = b
    if not connector.capabilities.get("attachments"):
        mark(rows, False, f"{connector.label} can't take attachments yet.")
        return {"ok": False, "message": f"{connector.label} can't take attachments yet."}
    res = connector.send_attachment(acn, pdf)
    mark(rows, res.ok, res.message)
    return {"ok": res.ok, "sent": len(rows) if res.ok else 0, "message": res.message, "test": res.environment == "sandbox"}


# ── API ──────────────────────────────────────────────────────────────────────
def _org(user: User) -> str:
    import billing_activation as ba
    org = ba.org_of(user)
    if org.startswith("user:"):
        raise HTTPException(status_code=400, detail="Add your clinic's name in Settings first.")
    return org


def _cid(claim_id: str) -> str:
    cid = str(claim_id or "").strip()[:64]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cid):
        raise HTTPException(status_code=400, detail="That claim number isn't valid.")
    return cid


def _sent(db, org, cid):
    import billing_activation as ba
    return db.scalar(select(ba.ClaimTransmission).where(ba.ClaimTransmission.org_key == org, ba.ClaimTransmission.claim_id == cid,
                                                        ba.ClaimTransmission.delivery == "SENT"))


def _json(r: ClaimAttachment) -> dict:
    return {"id": r.id, "kind": r.kind, "label": KINDS.get(r.kind, "Document"), "name": _open_text(r.name), "caption": _open_text(r.caption),
            "pages": r.pages, "size": r.size, "status": r.status, "message": r.message, "addedBy": r.uploaded_by,
            "addedAt": r.created_at.isoformat() if r.created_at else "", "sentAt": r.sent_at.isoformat() if r.sent_at else ""}


def _listing(db, org, cid):
    rows = rows_for(db, org, cid)
    tx = _sent(db, org, cid)
    return {"claimId": cid, "attachments": [_json(r) for r in rows], "locked": bool(tx), "acn": rows[0].acn if rows else "",
            "reportType": report_type({r.kind for r in rows}) if rows else "", "pages": sum(r.pages for r in rows),
            "size": sum(r.size for r in rows), "kinds": KINDS}


def _guard(user, db):
    ensure_product(user, db, "insurance")


@router.get("/api/claims/{claim_id}/attachments")
def list_attachments(claim_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _guard(user, db)
    org, cid = _org(user), _cid(claim_id)
    return _listing(db, org, cid)


def _add(db, user, org, cid, kind, name, caption, pdf, pages):
    if _sent(db, org, cid):
        raise HTTPException(status_code=409, detail="This claim was already sent, so its documents can't change. Send a corrected claim to add more.")
    rows = rows_for(db, org, cid)
    if len(rows) >= MAX_FILES:
        raise HTTPException(status_code=400, detail=f"A claim can carry up to {MAX_FILES} documents.")
    if sum(r.size for r in rows) + len(pdf) > MAX_CLAIM_PDF:
        raise HTTPException(status_code=400, detail="That's more than 20 MB of documents for one claim. Send fewer or smaller images.")
    acn = rows[0].acn if rows else "A" + secrets.token_hex(7).upper()
    r = ClaimAttachment(org_key=org, claim_id=cid, acn=acn, kind=kind, name=_seal_text(name[:200]), caption=_seal_text(caption[:300]),
                        pages=pages, size=len(pdf), data=_seal(pdf), uploaded_by=user.email or "")
    db.add(r)
    db.commit()
    return _listing(db, org, cid)


@router.post("/api/claims/{claim_id}/attachments")
async def add_attachment(claim_id: str, file: UploadFile = File(...), kind: str = Form("xray"), caption: str = Form(""), patient: str = Form(""),
                         user: User = Depends(require_any_role("admin", "biller", "provider")), db: Session = Depends(get_db)):
    _guard(user, db)
    org, cid = _org(user), _cid(claim_id)
    kind = kind if kind in KINDS else "other"
    data = await file.read(MAX_FILE + 1)
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_FILE:
        raise HTTPException(status_code=400, detail="Files can be up to 10 MB. Export the image at a smaller size.")
    sub = " · ".join(x for x in (f"Claim {cid}", patient.strip()[:80], caption.strip()[:200]) if x)
    try:
        from starlette.concurrency import run_in_threadpool
        pdf, pages = await run_in_threadpool(to_pdf, data, file.content_type or "", file.filename or "", KINDS[kind], sub)
    except BadFile as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _add(db, user, org, cid, kind, file.filename or KINDS[kind], caption.strip(), pdf, pages)


@router.post("/api/claims/{claim_id}/attachments/narrative")
async def add_narrative(claim_id: str, request: Request, user: User = Depends(require_any_role("admin", "biller", "provider")), db: Session = Depends(get_db)):
    _guard(user, db)
    org, cid = _org(user), _cid(claim_id)
    b = await request.json()
    text = str(b.get("text") or "").strip()
    if len(text) < 10:
        raise HTTPException(status_code=400, detail="Write the narrative first (why the treatment was needed).")
    if len(text) > 6000:
        raise HTTPException(status_code=400, detail="Keep the narrative under 6,000 characters.")
    sub = " · ".join(x for x in (f"Claim {cid}", str(b.get("patient") or "").strip()[:80], str(b.get("provider") or "").strip()[:80],
                                 str(b.get("dos") or "").strip()[:20]) if x)
    pdf, pages = narrative_to_pdf(text, "Narrative", sub)
    return _add(db, user, org, cid, "narrative", "Narrative", text[:120], pdf, pages)


@router.get("/api/claims/{claim_id}/attachments/{att_id}")
def view_attachment(claim_id: str, att_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _guard(user, db)
    org, cid = _org(user), _cid(claim_id)
    r = db.scalar(select(ClaimAttachment).where(ClaimAttachment.id == att_id, ClaimAttachment.org_key == org, ClaimAttachment.claim_id == cid))
    if not r:
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(_open(r.data), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{cid}-{r.id}.pdf"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/api/claims/{claim_id}/attachments/{att_id}")
def remove_attachment(claim_id: str, att_id: int, user: User = Depends(require_any_role("admin", "biller", "provider")), db: Session = Depends(get_db)):
    _guard(user, db)
    org, cid = _org(user), _cid(claim_id)
    if _sent(db, org, cid):
        raise HTTPException(status_code=409, detail="This claim was already sent, so its documents can't change.")
    r = db.scalar(select(ClaimAttachment).where(ClaimAttachment.id == att_id, ClaimAttachment.org_key == org, ClaimAttachment.claim_id == cid))
    if not r:
        raise HTTPException(status_code=404, detail="Not found.")
    db.delete(r)
    db.commit()
    return _listing(db, org, cid)


@router.post("/api/claims/{claim_id}/attachments/send")
def resend(claim_id: str, user: User = Depends(require_any_role("admin", "biller")), db: Session = Depends(get_db)):
    """Retry the documents of a claim that was sent but whose documents didn't go."""
    _guard(user, db)
    import clearinghouse as chx
    org, cid = _org(user), _cid(claim_id)
    if not _sent(db, org, cid):
        raise HTTPException(status_code=409, detail="The claim hasn't been sent yet. Its documents go with it.")
    res = send(db, org, cid, chx.connector_for(org))
    db.commit()
    out = _listing(db, org, cid)
    out["result"] = res
    if not res.get("ok"):
        return JSONResponse(dict(out, error=res.get("message") or "The documents weren't sent."), status_code=502)
    return out
