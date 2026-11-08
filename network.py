"""
network.py: what the Althais network learns from every clinic, so each clinic is protected by all of them.

1. What works. Every recovery case that ends (paid, denied again, or given up after it was sent) records which fix was
   tried for which payer, code and denial reason, whether it got paid, and how fast. For a new denial, what_works() ranks
   the fixes by their real record: "Corrected claim with modifier 25: paid 8 of 9 times by Aetna, median 14 days."
2. Payer change alerts. Each Lost Money analysis records, per week, how many lines of each code a payer paid or denied
   at the clinic. refresh_alerts() compares the last 4 weeks with the 12 before: when a payer starts denying a code it
   used to pay, at two or more clinics, every clinic billing that payer is warned (Recovery page, browser extension,
   Monday email). A clinic also gets alerts from its own data alone.

Only patterns are shared: payer, code, modifiers, reason, counts. Clinics are one-way hashes; no amounts, rates,
patients or claim numbers leave a clinic. Stored in network_outcomes, network_signals and org_settings (__network__).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base, OrgSettings, SessionLocal, engine

NETWORK = "__network__"
ALERTS = "payer_alerts"
MIN_SHOWN = 3            # a fix needs this many outcomes before its record is shown
RECENT_DAYS, BASE_DAYS = 28, 84


def _now():
    return dt.datetime.utcnow()


def clinic(org: str) -> str:
    return hashlib.sha256(("althais-network|" + (org or "")).encode()).hexdigest()[:16]


def payer_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class FixOutcome(Base):
    """How one recovery case ended. No amounts beyond what was recovered, no patient or claim details."""
    __tablename__ = "network_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clinic: Mapped[str] = mapped_column(String(16), index=True)
    payer: Mapped[str] = mapped_column(String(64), index=True)
    payer_name: Mapped[str] = mapped_column(String(128), default="")
    code: Mapped[str] = mapped_column(String(32), index=True)
    carc: Mapped[str] = mapped_column(String(8), default="")
    category: Mapped[str] = mapped_column(String(16), default="")
    fix: Mapped[str] = mapped_column(String(64))           # e.g. corrected:mod25, appeal:records, reconsideration, resend
    won: Mapped[int] = mapped_column(Integer)
    days: Mapped[int] = mapped_column(Integer, default=0)
    case_ref: Mapped[str] = mapped_column(String(32), default="")   # hashed, so one case is counted once
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PayerSignal(Base):
    """Per clinic, payer, code (+ modifiers) and week: lines paid and denied, and the denial reasons."""
    __tablename__ = "network_signals"
    __table_args__ = (UniqueConstraint("clinic", "payer", "code", "mods", "week", name="uq_network_signal"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clinic: Mapped[str] = mapped_column(String(16), index=True)
    payer: Mapped[str] = mapped_column(String(64), index=True)
    payer_name: Mapped[str] = mapped_column(String(128), default="")
    code: Mapped[str] = mapped_column(String(32))
    mods: Mapped[str] = mapped_column(String(32), default="-")
    week: Mapped[str] = mapped_column(String(10), index=True)          # Monday of the week, YYYY-MM-DD
    lines: Mapped[int] = mapped_column(Integer, default=0)
    denied: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[str] = mapped_column(String(255), default="{}")


Base.metadata.create_all(engine)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  1. What works
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

FIX_LABEL = {"corrected:mod25": "Corrected claim adding modifier 25", "corrected:modQW": "Corrected claim adding QW",
             "corrected:line": "Corrected claim adding the missed line", "corrected:other": "Corrected claim",
             "corrected:member": "Corrected claim with the right member ID", "resend": "Sending the claim again",
             "reconsideration": "Reconsideration request", "appeal:records": "Appeal with the visit note",
             "appeal:auth": "Retro-authorization and appeal", "appeal:noncovered": "Coverage appeal", "appeal:bundle": "Appeal for separate payment",
             "appeal:duplicate": "Appeal: not a duplicate", "appeal:coverage": "Appeal with coverage proof", "appeal:fix": "Appeal with correction",
             "appeal:review": "Appeal", "appeal:timely": "Timely filing appeal"}


def fix_of(case) -> str:
    import lost_money as lm
    f = case.fix or ""
    if case.kind == "corrected":
        if f.startswith("add_modifier:"):
            return "corrected:mod" + f.split(":")[2]
        if f.startswith("add_line:") or f == "needs_drug":
            return "corrected:line"
        if lm.CARC_KIND.get(case.carc) == "coverage":
            return "corrected:member"
        return "corrected:other"
    if case.kind == "appeal":
        return "appeal:" + lm.CARC_KIND.get(case.carc, "review")
    return case.kind


def record_outcome(db, case, won: bool):
    """Called when a case ends. Counted once per case."""
    ref = hashlib.sha256(f"{case.org_key}|{case.id}".encode()).hexdigest()[:32]
    if db.scalar(select(FixOutcome.id).where(FixOutcome.case_ref == ref)):
        return
    start = case.sent_at or case.created_at or _now()
    db.add(FixOutcome(clinic=clinic(case.org_key), payer=payer_key(case.payer), payer_name=(case.payer or "").title()[:128],
                      code=(case.code or "")[:32], carc=(case.carc or "")[:8], category=case.category or "", fix=fix_of(case),
                      won=1 if won else 0, days=max(0, (_now() - start).days), case_ref=ref))
    db.flush()


def what_works(db, payer: str, code: str, carc: str, org: Optional[str] = None) -> List[dict]:
    """Fixes tried for this payer, code and reason across Althais, best record first. Falls back to the payer and reason
    (any code), then the reason alone, when there isn't enough history for the exact combination."""
    p = payer_key(payer)
    tiers = [("this payer, code and reason", [FixOutcome.payer == p, FixOutcome.code == code, FixOutcome.carc == carc]),
             ("this payer and reason", [FixOutcome.payer == p, FixOutcome.carc == carc]),
             ("this reason, all payers", [FixOutcome.carc == carc])]
    me = clinic(org) if org else None
    for scope, conds in tiers:
        if not carc and scope != tiers[0][0]:
            break
        rows = db.scalars(select(FixOutcome).where(*conds)).all()
        by = defaultdict(list)
        for r in rows:
            by[r.fix].append(r)
        out = []
        for fix, rs in by.items():
            if len(rs) < MIN_SHOWN:
                continue
            wins = [r for r in rs if r.won]
            out.append({"fix": fix, "label": FIX_LABEL.get(fix, fix), "tried": len(rs), "paid": len(wins),
                        "rate": round(len(wins) / len(rs), 2), "medianDays": int(statistics.median(r.days for r in wins)) if wins else None,
                        "clinics": len({r.clinic for r in rs}), "yours": sum(1 for r in rs if me and r.clinic == me), "scope": scope})
        if out:
            return sorted(out, key=lambda x: (-x["rate"], -x["tried"]))
    return []


def odds(db, case) -> Optional[float]:
    """The real paid rate for this case's own fix, when there's enough history."""
    for w in what_works(db, case.payer, case.code, case.carc):
        if w["fix"] == fix_of(case):
            return w["rate"]
    return None


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  2. Payer change alerts
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _week(d: str) -> str:
    try:
        x = dt.date.fromisoformat(d)
    except ValueError:
        return ""
    return (x - dt.timedelta(days=x.weekday())).isoformat()


def record_signals(db, org: str, a) -> int:
    """Weekly paid/denied counts per payer and code from a Lost Money analysis. Re-running the same files replaces the
    clinic's numbers for those weeks, so nothing doubles."""
    import lost_money as lm
    me = clinic(org)
    agg: Dict[tuple, dict] = {}
    for r in a.remits:
        p = payer_key(r.payer)
        if not p:
            continue
        for l in r.lines:
            wk = _week(l.dos or r.dos or r.date or "")
            if not wk:
                continue
            k = (p, l.code, "+".join(sorted(m for m in l.mods if m)) or "-", wk)
            e = agg.setdefault(k, {"name": (r.payer or "").title(), "lines": 0, "denied": 0, "reasons": Counter()})
            e["lines"] += 1
            if lm._denied(l):
                e["denied"] += 1
                e["reasons"][lm._denial_carc(l.adjustments + r.adjustments)] += 1
    for (p, code, mods, wk), e in agg.items():
        row = db.scalar(select(PayerSignal).where(PayerSignal.clinic == me, PayerSignal.payer == p, PayerSignal.code == code,
                                                  PayerSignal.mods == mods, PayerSignal.week == wk))
        if row is None:
            row = PayerSignal(clinic=me, payer=p, code=code[:32], mods=mods[:32], week=wk)
            db.add(row)
        row.payer_name, row.lines, row.denied, row.reasons = e["name"][:128], e["lines"], e["denied"], json.dumps(dict(e["reasons"].most_common(5)))[:255]
    db.flush()
    return len(agg)


def detect(db, today: Optional[dt.date] = None, min_clinics: int = 2, only_clinic: Optional[str] = None) -> List[dict]:
    """Payer + code combinations whose denial rate jumped in the last 4 weeks against the 12 before."""
    import lost_money as lm
    today = today or dt.date.today()
    recent_from = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    base_from = (today - dt.timedelta(days=RECENT_DAYS + BASE_DAYS)).isoformat()
    q = select(PayerSignal).where(PayerSignal.week >= _week(base_from))
    if only_clinic:
        q = q.where(PayerSignal.clinic == only_clinic)
    groups = defaultdict(lambda: {"recent": [0, 0, set(), Counter(), ""], "base": [0, 0, set()], "name": ""})
    for s in db.scalars(q).all():
        g = groups[(s.payer, s.code, s.mods)]
        g["name"] = s.payer_name or g["name"]
        if s.week >= _week(recent_from):
            r = g["recent"]
            r[0] += s.lines
            r[1] += s.denied
            if s.denied:
                r[2].add(s.clinic)
                r[3].update(json.loads(s.reasons or "{}"))
                r[4] = min(r[4] or s.week, s.week)
        else:
            b = g["base"]
            b[0] += s.lines
            b[1] += s.denied
            b[2].add(s.clinic)
    out = []
    for (p, code, mods), g in groups.items():
        rl, rd, rc, reasons, since = g["recent"]
        bl, bd, bc = g["base"]
        if rd < 3 or rl < 4 or bl < 5 or len(rc) < min_clinics:
            continue
        rate, base = rd / rl, bd / bl
        if rate >= max(2 * base, base + 0.15) and rate >= 0.25:
            carc = reasons.most_common(1)[0][0] if reasons else ""
            out.append({"payer": p, "payerName": g["name"], "code": code, "mods": mods, "carc": carc, "reason": lm.CARC.get(carc, ""),
                        "rate": round(rate, 2), "baseRate": round(base, 2), "denied": rd, "clinics": len(rc), "since": since,
                        "message": f"{g['name'] or p} started denying {code}{' with ' + mods.replace('+', ', ') if mods != '-' else ''} "
                                   f"({round(rate * 100)}% of lines in the last 4 weeks, up from {round(base * 100)}%)"
                                   + (f" at {len(rc)} Althais clinics" if len(rc) > 1 else "")
                                   + (f". Most common reason: {carc}, {lm.CARC.get(carc, 'see the remark')}." if carc else ".")})
    return sorted(out, key=lambda x: (-x["clinics"], -x["denied"]))


def refresh_alerts(today: Optional[dt.date] = None) -> int:
    """Hourly (after the payment monitor): the network's current payer change alerts."""
    with SessionLocal() as db:
        alerts = detect(db, today)
        row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == NETWORK, OrgSettings.category == ALERTS))
        if row is None:
            row = OrgSettings(org_key=NETWORK, category=ALERTS, data="{}")
            db.add(row)
        row.data = json.dumps({"alerts": alerts[:200], "updated": _now().isoformat(timespec="seconds") + "Z"})
        db.commit()
        return len(alerts)


def alerts_for(db, org: str, payers: Optional[List[str]] = None, today: Optional[dt.date] = None) -> List[dict]:
    """Alerts that matter to this clinic: network alerts for payers it bills, plus changes in its own data."""
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == NETWORK, OrgSettings.category == ALERTS))
    net = (json.loads(row.data or "{}").get("alerts") or []) if row else []
    mine = {s.payer for s in db.scalars(select(PayerSignal).where(PayerSignal.clinic == clinic(org))).all()}
    if payers:
        mine |= {payer_key(p) for p in payers}
    out = [dict(a, scope="network") for a in net if a["payer"] in mine]
    seen = {(a["payer"], a["code"], a["mods"]) for a in out}
    for a in detect(db, today, min_clinics=1, only_clinic=clinic(org)):
        if (a["payer"], a["code"], a["mods"]) not in seen:
            out.append(dict(a, scope="yours"))
    return out[:20]
