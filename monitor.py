"""
monitor.py: Althais watching a clinic's payments all the time, not just when someone uploads files.

Every payment file Althais sees for a clinic is kept (encrypted) for 400 days:
  - ERAs read from Althais's Claim.MD account each hour (billing_activation.poll_eras), routed to the clinic whose
    billing NPI is the payee on the file
  - payment and claim files a clinic uploads to the Lost Money Report (with "save to Recovery" on)
Claims Althais itself sent (claim transmissions) are rebuilt as 837s on the fly, so they can be corrected and resent.

Each hour, for every clinic with new files since its last run, the Lost Money analysis runs again over the last 200 days:
new items open Recovery cases, paid items close theirs, and the clinic's payer memory (money_checks) is refreshed. The
Recovery page shows what's being watched and when it last ran; the Monday email reports the results.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import List, Optional, Tuple

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

import crypto_box
import lost_money as lm
from auth import Base, OrgSettings, SessionLocal, engine

WINDOW_DAYS = 200
KEEP_DAYS = 400
STATUS = "payment_monitor"


def _now():
    return dt.datetime.utcnow()


class PaymentFile(Base):
    """One payment (835) or claim (837 / CSV) file for one clinic. The contents are encrypted."""
    __tablename__ = "payment_files"
    __table_args__ = (UniqueConstraint("org_key", "sha", name="uq_payment_file"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    sha: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))           # 835 | 837 | csv
    source: Mapped[str] = mapped_column(String(16))        # claimmd | upload
    name: Mapped[str] = mapped_column(String(160), default="")
    sealed: Mapped[str] = mapped_column(Text)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


Base.metadata.create_all(engine)


def _kind(text: str) -> str:
    head = text[:5000]
    if "ISA" in head[:200]:
        m = re.search(r"ST.(83[57])", head)
        return m.group(1) if m else ""
    return "csv" if ("," in head[:2000] or "\t" in head[:2000]) else ""


def add_file(db, org: str, name: str, text: str, source: str) -> bool:
    """Keep one file for the clinic. Returns False if it was already kept or isn't a payment/claim file."""
    if not org or not text:
        return False
    kind = _kind(text)
    if not kind:
        return False
    sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    if db.scalar(select(PaymentFile.id).where(PaymentFile.org_key == org, PaymentFile.sha == sha)):
        return False
    db.add(PaymentFile(org_key=org, sha=sha, kind=kind, source=source, name=(name or "")[:160],
                       sealed=crypto_box.seal_text(text, "payfile", "PHI_ENC_KEY")))
    db.flush()
    return True


def add_uploads(db, org: str, pairs: List[Tuple[str, bytes]]) -> int:
    """The files a clinic just uploaded to the Lost Money Report (zips opened)."""
    n = 0
    for name, data in _flatten(pairs):
        n += add_file(db, org, name, lm._decode(data), "upload")
    return n


def _flatten(pairs, depth=0):
    import io
    import zipfile
    for name, data in pairs:
        if (name.lower().endswith(".zip") or data[:4] == b"PK\x03\x04") and depth < 2:
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                continue
            inner = [(f"{name}/{i.filename}", z.read(i)) for i in z.infolist()
                     if not i.is_dir() and not i.filename.startswith("__MACOSX") and i.file_size <= lm.MAX_FILE]
            yield from _flatten(inner, depth + 1)
        elif len(data) <= lm.MAX_FILE:
            yield name, data


def _npi_map(db) -> dict:
    import billing_activation as ba
    out = {}
    for row in db.scalars(select(ba.BillingProfile)).all():
        try:
            npi = re.sub(r"\D", "", str(json.loads(row.data or "{}").get("npi") or ""))
        except Exception:
            continue
        if len(npi) == 10:
            out.setdefault(npi, row.org_key)
    return out


def route_era(db, text: str, eraid: str = "") -> List[str]:
    """An ERA from Althais's Claim.MD account goes to the clinic(s) whose billing NPI is the payee on it."""
    try:
        remits = [r for ix in lm.interchanges(text) for r in lm.parse_835(ix, "claimmd")]
    except Exception:
        return []
    npis = {r.payee_npi for r in remits if r.payee_npi}
    if not npis:
        return []
    owners = _npi_map(db)
    orgs = sorted({owners[n] for n in npis if n in owners})
    for org in orgs:
        add_file(db, org, f"claimmd-era-{eraid or 'x'}.835", text, "claimmd")
    return orgs


def _sent_claims(db, org: str, since: dt.datetime) -> List[Tuple[str, bytes]]:
    """Claims Althais sent for the clinic, rebuilt as 837s so the analysis knows what was billed (and can correct it)."""
    import billing_activation as ba
    import clearinghouse as chx
    _, prof = ba.load_profile(db, org, create=False)
    payers = {re.sub(r"[^a-z]", "", (p.get("name") or "").lower()): p for p in prof.get("payers") or [] if p.get("payerId")}
    out = []
    rows = db.scalars(select(ba.ClaimTransmission).where(ba.ClaimTransmission.org_key == org, ba.ClaimTransmission.delivery == "SENT",
                                                         ba.ClaimTransmission.sent_at >= since)).all()
    for tx in rows[-2000:]:
        try:
            c = json.loads(tx.snapshot or "{}")
            if not c or chx.is_dental(c):
                continue
            p = payers.get(re.sub(r"[^a-z]", "", (c.get("payer") or "").lower()))
            if not p:
                continue
            pv = ba.provider_for(prof, c.get("provider"), c.get("npi") or "") or {}
            edi = chx.build_837p(dict(c, billingProfile=prof, npi=c.get("npi") or pv.get("npi") or ""), p["payerId"], p.get("name") or c.get("payer"))
            out.append((f"althais-claim-{tx.claim_id}.837", edi.encode()))
        except Exception:
            continue
    return out


def run(db, org: str, now: Optional[dt.datetime] = None) -> dict:
    """The Lost Money analysis over the clinic's last 200 days of files; opens and closes Recovery cases."""
    import money_checks
    import recovery
    now = now or _now()
    since = now - dt.timedelta(days=WINDOW_DAYS)
    files = db.scalars(select(PaymentFile).where(PaymentFile.org_key == org, PaymentFile.received_at >= since).order_by(PaymentFile.id)).all()
    pairs = []
    for f in files:
        try:
            pairs.append((f.name or f"file-{f.id}", crypto_box.open_text(f.sealed, "payfile", "PHI_ENC_KEY").encode()))
        except Exception:
            continue
    pairs += _sent_claims(db, org, since)
    a = lm.analyze(pairs, now.date())
    saved = {"created": 0, "recovered": 0, "denied_again": 0, "recovered_dollars": 0.0}
    if a.remits or a.claims:
        money_checks.learn(db, org, a)
        saved = recovery.save_cases(db, org, "Althais (watching payments)", a)
    s = a.summary()
    status = {"lastRun": now.isoformat(timespec="seconds") + "Z", "files": len(files), "payments": s["payments"], "claims": s["claims"],
              "found": s["found"], "period": s["period"], "newCases": saved.get("created", 0), "recovered": saved.get("recovered", 0),
              "recoveredDollars": saved.get("recovered_dollars", 0.0), "latestFile": max((f.received_at for f in files), default=_now()).isoformat(timespec="seconds") + "Z",
              "lastFileId": max((f.id for f in files), default=0)}
    _set_status(db, org, status)
    return status


def _status_row(db, org):
    return db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == STATUS))


def status(db, org: str) -> dict:
    row = _status_row(db, org)
    out = json.loads(row.data or "{}") if row else {}
    out["filesKept"] = db.scalar(select(func.count(PaymentFile.id)).where(PaymentFile.org_key == org)) or 0
    out["fromClaimmd"] = db.scalar(select(func.count(PaymentFile.id)).where(PaymentFile.org_key == org, PaymentFile.source == "claimmd")) or 0
    return out


def _set_status(db, org: str, data: dict):
    row = _status_row(db, org)
    if row is None:
        row = OrgSettings(org_key=org, category=STATUS, data="{}")
        db.add(row)
    row.data = json.dumps(data)


def run_due(now: Optional[dt.datetime] = None) -> int:
    """Hourly: every clinic with files newer than its last run. Also drops files past the keep window. Returns clinics run."""
    now = now or _now()
    n = 0
    with SessionLocal() as db:
        db.query(PaymentFile).filter(PaymentFile.received_at < now - dt.timedelta(days=KEEP_DAYS)).delete(synchronize_session=False)
        db.commit()
        latest = dict(db.execute(select(PaymentFile.org_key, func.max(PaymentFile.id)).group_by(PaymentFile.org_key)).all())
        for org, newest in latest.items():
            try:
                if newest <= int(status(db, org).get("lastFileId") or 0):      # nothing newer than what the last run read
                    continue
                run(db, org, now)
                db.commit()
                n += 1
            except Exception as e:
                db.rollback()
                print(f"[MONITOR] {type(e).__name__}")       # no details: they could include clinic data
    try:                # payer change alerts across the network (network.py)
        import network
        network.refresh_alerts(now.date())
    except Exception as e:
        print(f"[NETWORK] alerts: {type(e).__name__}")
    return n
