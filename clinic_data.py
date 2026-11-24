"""
clinic_data.py: return a clinic's data, then destroy it, as the business associate agreement promises when a clinic
leaves. Althais admins only (/admin).

  - export: a zip of every row Althais holds for the clinic (one JSON file per table, binary fields base64) plus the
    payment and claim files decrypted back to their original 835 / 837 text, and a README. For the clinic, on request.
  - preview: how many rows each table holds for the clinic, before deleting.
  - certificate: after auth.admin_remove_clinic deletes the clinic, what was deleted, when, and when the last backups
    holding it expire (Cloud SQL keeps BACKUP_RETENTION_DAYS of daily backups, 14 by default). Printable for the clinic.

A clinic's data is kept under two keys: its name (most tables) and the claim store's key (org_<name>, see
billing_activation.claims_namespace). Both are covered, unless another clinic's name gives the same claim store key.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import os
import re
import zipfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from auth import AdminAuditLog, OrgSettings, User, _clinic_exists, _doc_org_key, _org_key_tables, get_db, log_admin_action, require_admin

router = APIRouter()


def namespace(org: str) -> str:
    return "org_" + re.sub(r"[^a-z0-9]+", "_", (org or "").strip().lower()).strip("_")


def keys_for(db: Session, org: str) -> list:
    """The keys this clinic's rows are stored under. The claim store key only if no other clinic shares it."""
    ns = namespace(org)
    others = {_doc_org_key(u) for u in db.scalars(select(User)) if (u.organization or "").strip()} | {
        r.org_key for r in db.scalars(select(OrgSettings).where(OrgSettings.category == "entitlements"))}
    shared = any(o != org and namespace(o) == ns for o in others)
    return [org] if shared or ns == org else [org, ns]


def counts(db: Session, org: str) -> dict:
    keys = keys_for(db, org)
    out = {}
    for t in _org_key_tables(db):
        n = db.execute(text(f'SELECT COUNT(*) FROM "{t}" WHERE org_key IN ({",".join(":k" + str(i) for i in range(len(keys)))})'),
                       {f"k{i}": k for i, k in enumerate(keys)}).scalar() or 0
        if n:
            out[t] = int(n)
    n = db.execute(text("SELECT COUNT(*) FROM staff_files WHERE document_id IN (SELECT id FROM staff_documents WHERE org_key = :k)"), {"k": org}).scalar() or 0
    if n:
        out["staff_files"] = int(n)
    return out


def _json(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def export_zip(db: Session, org: str) -> bytes:
    import crypto_box
    keys = keys_for(db, org)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        tables = {}
        for t in _org_key_tables(db):
            rows = db.execute(text(f'SELECT * FROM "{t}" WHERE org_key IN ({",".join(":k" + str(i) for i in range(len(keys)))})'),
                              {f"k{i}": k for i, k in enumerate(keys)}).mappings().all()
            if rows:
                tables[t] = len(rows)
                z.writestr(f"tables/{t}.json", json.dumps([{k: _json(v) for k, v in r.items()} for r in rows], indent=1, default=str))
        people = [{"email": u.email, "name": u.full_name, "role": u.role, "active": u.active}
                  for u in db.scalars(select(User)) if _doc_org_key(u) == org]
        z.writestr("tables/users.json", json.dumps(people, indent=1))
        n_files = 0
        if "payment_files" in tables:
            import monitor
            for f in db.scalars(select(monitor.PaymentFile).where(monitor.PaymentFile.org_key.in_(keys))):
                try:
                    body = crypto_box.open_text(f.sealed, "payfile", "PHI_ENC_KEY")
                except Exception:
                    continue
                name = re.sub(r"[^\w.-]+", "_", f.name or f"file-{f.id}")[:100]
                z.writestr(f"files/{f.id}-{name}", body)
                n_files += 1
        z.writestr("README.txt",
                   f"Althais data export for {org}\nMade {dt.datetime.utcnow().replace(microsecond=0).isoformat()}Z\n\n"
                   "tables/: every record Althais holds for the clinic, one JSON file per table (binary fields base64; "
                   "fields marked sealed or encrypted are encrypted with Althais's keys).\n"
                   f"files/: the {n_files} payment and claim files, decrypted back to their original 835 / 837 / CSV text.\n"
                   "users.json: the clinic's accounts (no passwords).\n\nThis file contains protected health information. "
                   "Send it only over an encrypted channel, to the person the clinic named.\n")
    return buf.getvalue()


def backup_retention_days() -> int:
    try:
        return max(0, int(os.environ.get("BACKUP_RETENTION_DAYS", "14")))
    except ValueError:
        return 14


def certificate(org: str, deleted: dict, accounts: int, when: dt.datetime) -> dict:
    gone = when + dt.timedelta(days=backup_retention_days() + 1)
    return {"clinic": org, "deletedAt": when.replace(microsecond=0).isoformat() + "Z", "rows": deleted, "totalRows": sum(deleted.values()),
            "accounts": accounts, "backupsExpireBy": gone.date().isoformat(),
            "statement": (f"All of {org}'s data held by Althais Inc. in its application database was permanently deleted on {when.date().isoformat()}. "
                          f"Encrypted database backups that still contain it are kept by Google Cloud SQL for {backup_retention_days()} days and "
                          f"expire by {gone.date().isoformat()}; until then they are protected as the business associate agreement requires "
                          "and are not restored or used.")}


@router.get("/api/admin/clinics/{org_key}/deletion-preview")
def preview(org_key: str, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    if org_key.startswith("user:") or not _clinic_exists(db, org_key):
        return JSONResponse({"error": "Not found."}, status_code=404)
    c = counts(db, org_key)
    return {"clinic": org_key, "keys": keys_for(db, org_key), "rows": c, "totalRows": sum(c.values()),
            "accounts": sum(1 for u in db.scalars(select(User)) if _doc_org_key(u) == org_key)}


@router.get("/api/admin/clinics/{org_key}/export")
def export(org_key: str, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    if org_key.startswith("user:") or not _clinic_exists(db, org_key):
        return JSONResponse({"error": "Not found."}, status_code=404)
    data = export_zip(db, org_key)
    log_admin_action(db, "clinic_exported", target=org_key, detail=f"Downloaded a full data export ({len(data):,} bytes)")
    name = re.sub(r"[^\w-]+", "-", org_key).strip("-")[:60] or "clinic"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="althais-export-{name}.zip"', "Cache-Control": "no-store"})


@router.get("/admin/deletion-certificate/{log_id}", response_class=HTMLResponse)
def certificate_page(log_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    row = db.get(AdminAuditLog, log_id)
    if not row or row.action != "clinic_deleted":
        return HTMLResponse("Not found.", status_code=404)
    try:
        c = json.loads(row.detail.split("CERTIFICATE ", 1)[1])
    except (IndexError, ValueError):
        return HTMLResponse("This deletion has no certificate (it happened before certificates were kept).", status_code=404)
    e = html.escape
    rows = "".join(f"<tr><td>{e(t)}</td><td style='text-align:right'>{n:,}</td></tr>" for t, n in sorted(c["rows"].items()))
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Certificate of data deletion</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:720px;margin:40px auto;color:#1c2733}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}}@media print{{button{{display:none}}}}</style></head><body>
<h1>Certificate of data deletion</h1>
<p><b>Business associate:</b> Althais Inc.<br><b>Covered entity:</b> {e(c['clinic'])}<br><b>Deleted:</b> {e(c['deletedAt'])}<br>
<b>Accounts removed:</b> {c['accounts']}<br><b>Records deleted:</b> {c['totalRows']:,}<br><b>Last backups expire by:</b> {e(c['backupsExpireBy'])}</p>
<p>{e(c['statement'])}</p><table><tr><th>Table</th><th style='text-align:right'>Records</th></tr>{rows}</table>
<p style="margin-top:40px">Signed for Althais Inc.: ______________________________ Date: ____________</p>
<button onclick="print()">Print or save as PDF</button></body></html>""")
