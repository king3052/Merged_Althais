"""
clinic_records.py: the clinic's appointments and visit notes, kept on the server.

Patients and claims already live on the server (/api/patients, /api/claims). Appointments and visit notes used to live
only in the browser that made them, so clearing the browser or moving to another computer lost them, and nobody else at
the clinic could see them. Now the app (static/v4/sync.js) writes every change here and reads everyone else's changes
back every 30 seconds.

  GET  /api/records?since=<ISO time>   changed records (and deletions) since then; everything without `since`
  POST /api/records                    {records: [{kind, id, data} | {kind, id, deleted: true}]}

Each record is stored whole, encrypted (AES-256-GCM, crypto_box.py, key PHI_ENC_KEY). A deletion leaves a marker so
other browsers remove their copy too. Last write wins; the browser keeps its own unsent change rather than taking an
older copy from the server (see sync.js for how that's decided).
"""
import datetime as dt
import json
import re
from datetime import timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import Base, _org_namespace, engine, ensure_area, ensure_product, get_db, require_any_role, require_user

router = APIRouter()

KINDS = {"appt": ("schedule",), "enc": ("patients", "schedule")}
MAX_RECORD = 512 * 1024
MAX_BATCH = 200
ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _now():
    return dt.datetime.now(timezone.utc).replace(tzinfo=None)


class ClinicRecord(Base):
    __tablename__ = "clinic_records"
    __table_args__ = (UniqueConstraint("org_key", "kind", "rec_id", name="uq_clinic_record"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    rec_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[str] = mapped_column(Text, default="")          # sealed JSON; empty when deleted
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


Base.metadata.create_all(engine)


def _seal(obj) -> str:
    import crypto_box
    return crypto_box.seal_text(json.dumps(obj, separators=(",", ":")), "clinic-record", "PHI_ENC_KEY")


def _open(s: str):
    import crypto_box
    return json.loads(crypto_box.open_text(s, "clinic-record", "PHI_ENC_KEY"))


def _guard(user, db):
    ensure_product(user, db)          # the EMR (full suite)
    ensure_area(user, "patients", "schedule")


def _since(v: str):
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=2)   # a little overlap: merging is idempotent
    except Exception:
        return None


@router.get("/api/records")
def list_records(since: str = "", user=Depends(require_user), db: Session = Depends(get_db)):
    _guard(user, db)
    org = _org_namespace(user)
    q = select(ClinicRecord).where(ClinicRecord.org_key == org)
    t = _since(since) if since else None
    if t:
        q = q.where(ClinicRecord.updated_at >= t)
    else:
        q = q.where(ClinicRecord.deleted == 0)
    out = []
    for r in db.scalars(q.order_by(ClinicRecord.updated_at)).all():
        item = {"kind": r.kind, "id": r.rec_id, "version": r.version, "deleted": bool(r.deleted)}
        if not r.deleted:
            try:
                item["data"] = _open(r.data)
            except Exception:
                continue          # sealed with a key that changed: skip it rather than fail the whole sync
        out.append(item)
    return {"records": out, "server_time": _now().isoformat() + "Z"}


@router.post("/api/records")
async def save_records(request: Request, user=Depends(require_any_role("admin", "biller", "provider", "front_desk")), db: Session = Depends(get_db)):
    _guard(user, db)
    org = _org_namespace(user)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Send JSON."}, status_code=400)
    items = body.get("records") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "Nothing to save."}, status_code=400)
    if len(items) > MAX_BATCH:
        return JSONResponse({"error": f"Send at most {MAX_BATCH} records at a time."}, status_code=400)
    saved, refused = [], []
    for it in items:
        kind, rid = str((it or {}).get("kind") or ""), str((it or {}).get("id") or "")
        if kind not in KINDS or not ID.match(rid):
            refused.append({"kind": kind, "id": rid, "why": "bad kind or id"})
            continue
        deleted = bool(it.get("deleted"))
        data = it.get("data")
        if not deleted:
            if not isinstance(data, dict) or str(data.get("id") or "") != rid:
                refused.append({"kind": kind, "id": rid, "why": "data must be the record, with the same id"})
                continue
            raw = json.dumps(data, separators=(",", ":"))
            if len(raw) > MAX_RECORD:
                refused.append({"kind": kind, "id": rid, "why": "record too large"})
                continue
        row = db.scalar(select(ClinicRecord).where(ClinicRecord.org_key == org, ClinicRecord.kind == kind, ClinicRecord.rec_id == rid))
        if not row:
            if deleted:
                saved.append({"kind": kind, "id": rid, "version": 0, "deleted": True})
                continue
            row = ClinicRecord(org_key=org, kind=kind, rec_id=rid, version=0)
            db.add(row)
        row.version = (row.version or 0) + 1
        row.deleted = 1 if deleted else 0
        row.data = "" if deleted else _seal(data)
        row.updated_by = (user.email or "")[:255]
        row.updated_at = _now()
        saved.append({"kind": kind, "id": rid, "version": row.version, "deleted": deleted})
    db.commit()
    return {"saved": saved, "refused": refused, "server_time": _now().isoformat() + "Z"}
