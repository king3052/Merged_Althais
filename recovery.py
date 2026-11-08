"""
recovery.py: Fix it. Turns what the Lost Money Report finds into money back, and tracks each item until it's paid.

A Lost Money Report run by a clinic (with "save to Recovery" on) opens a recovery case for every item worth chasing:
  denials       -> a corrected claim when the fix is on the claim (modifier, member ID, a resubmission), else an appeal letter
  underpayments -> a reconsideration letter, with the payer's own payments at its usual rate as the proof
  missed charges-> a corrected claim that adds the line (the clinic's own price for it)
  no answer     -> the original claim sent again

Corrected claims are rebuilt from the clinic's own 837 for that claim (frequency code 7 and the payer's claim number in
REF*F8, or 1 for a claim the payer never got). They go out through Althais's Claim.MD account when the claim is the
clinic's own (its billing NPI is the clinic's, in Billing Activation), or download for the clinic's clearinghouse.
Letters are drafted from the case and the clinic's billing profile; anything only the visit note can say is left in
[brackets] and has to be filled in before the letter can be downloaded. Nothing is made up.

Cases close themselves: a newer payment for the claim (an ERA through Althais, or payment files uploaded to the next
Lost Money Report) marks the case Recovered with the amount, or Denied again with the reason.

Pages and APIs (billers and admins): /recovery, /api/recovery/*. The claim file and the patient's name and member ID
are stored encrypted (crypto_box, PHI_ENC_KEY).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

import crypto_box
import lost_money as lm
from auth import Base, ROLE_LEVELS, SessionLocal, current_user, engine, get_db, require_user

router = APIRouter()

OPEN_STATES = ("OPEN", "READY", "SENT", "DENIED_AGAIN")
STATUS_LABEL = {"OPEN": "To do", "READY": "Ready to send", "SENT": "Sent, waiting on the payer", "RECOVERED": "Recovered",
                "PARTIAL": "Partly recovered", "DENIED_AGAIN": "Denied again", "CLOSED": "Closed"}
KIND_LABEL = {"corrected": "Corrected claim", "appeal": "Appeal letter", "reconsideration": "Reconsideration letter", "resend": "Send the claim again"}
SAVED = ("denial", "underpaid", "missed", "no_response")


def _now():
    return dt.datetime.utcnow()


class RecoveryCase(Base):
    """One item from a Lost Money Report, worked until it's paid."""
    __tablename__ = "recovery_cases"
    __table_args__ = (UniqueConstraint("org_key", "dedupe", name="uq_recovery_case"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    dedupe: Mapped[str] = mapped_column(String(191))
    category: Mapped[str] = mapped_column(String(32))          # denial | underpaid | missed | no_response
    kind: Mapped[str] = mapped_column(String(32))              # corrected | appeal | reconsideration | resend
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    claim_id: Mapped[str] = mapped_column(String(64), index=True)
    payer_claim_id: Mapped[str] = mapped_column(String(64), default="")
    payer: Mapped[str] = mapped_column(String(128), default="")
    dos: Mapped[str] = mapped_column(String(10), default="")
    code: Mapped[str] = mapped_column(String(32), default="")
    carc: Mapped[str] = mapped_column(String(8), default="")
    fix: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[float] = mapped_column(Float, default=0.0)
    recovered: Mapped[float] = mapped_column(Float, default=0.0)
    deadline: Mapped[str] = mapped_column(String(10), default="")
    basis_date: Mapped[str] = mapped_column(String(10), default="")    # the newest payment file this case was found in
    sealed: Mapped[str] = mapped_column(Text, default="")              # encrypted JSON: patient, member ID, the claim's 837, letter, evidence
    reference: Mapped[str] = mapped_column(String(128), default="")
    sent_via: Mapped[str] = mapped_column(String(16), default="")      # althais | clinic
    history: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True, default=None)
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True, default=None)


Base.metadata.create_all(engine)


def _seal(doc: dict) -> str:
    return crypto_box.seal_text(json.dumps(doc), "recovery", "PHI_ENC_KEY")


def _open(case: RecoveryCase) -> dict:
    if not case.sealed:
        return {}
    try:
        return json.loads(crypto_box.open_text(case.sealed, "recovery", "PHI_ENC_KEY"))
    except Exception:
        return {}


def _log(case: RecoveryCase, what: str, who: str = ""):
    h = json.loads(case.history or "[]")
    h.append({"at": _now().isoformat(timespec="seconds") + "Z", "what": what, "who": who})
    case.history = json.dumps(h[-50:])


def kind_for(f) -> str:
    if f.category == "underpaid":
        return "reconsideration"
    if f.category == "no_response":
        return "resend"
    if f.category == "missed":
        return "corrected"
    return "corrected" if f.fix else "appeal"


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Opening cases from a report, and closing them from payments
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def save_cases(db: Session, org: str, who: str, a: "lm.Analysis") -> dict:
    """Open (or refresh) a case for every item worth chasing, then close cases the new payment files settle."""
    settled = match(db, a.remits, org=org)
    latest = {}
    for r in a.remits:
        if r.date and r.date > latest.get(r.claim_id, ""):
            latest[r.claim_id] = r.date
    remit_by_claim = {r.claim_id: r for r in a.remits}
    created = refreshed = 0
    for f in a.findings:
        if f.category not in SAVED or not f.claim_id:
            continue
        dedupe = f"{f.category}|{f.claim_id}|{f.code}|{f.carc}"[:191]
        case = db.scalar(select(RecoveryCase).where(RecoveryCase.org_key == org, RecoveryCase.dedupe == dedupe))
        claim = a.claims.get(f.claim_id)
        rm = remit_by_claim.get(f.claim_id)
        if case is None:
            case = RecoveryCase(org_key=org, dedupe=dedupe, category=f.category, kind=kind_for(f), claim_id=f.claim_id[:64], created_by=who,
                                status="OPEN", history="[]", recovered=0.0, basis_date="")
            db.add(case)
            _log(case, "Found by the Lost Money Report", who)
            created += 1
        elif case.status not in OPEN_STATES:
            continue
        else:
            refreshed += 1
        doc = _open(case)
        doc.update({"patient": (claim.patient if claim else "") or (rm.patient if rm else "") or doc.get("patient", ""),
                    "member_id": (claim.member_id if claim else "") or (rm.member_id if rm else "") or doc.get("member_id", ""),
                    "evidence": f.evidence or doc.get("evidence", [])})
        if claim is not None and claim.raw:
            doc["raw"] = claim.raw
        case.sealed = _seal(doc)
        case.payer_claim_id, case.payer, case.dos, case.code, case.carc, case.fix = (f.payer_claim_id or (rm.payer_claim_id if rm else "") or "")[:64], (f.payer or "")[:128], (f.dos or "")[:10], (f.code or "")[:32], (f.carc or "")[:8], (f.fix or "")[:64]
        case.title, case.action, case.why, case.expected, case.deadline = f.title[:255], f.action, f.why, round(f.dollars, 2), (f.deadline or "")[:10]
        case.basis_date = max(case.basis_date or "", latest.get(f.claim_id, ""))
        if (case.kind in ("corrected", "resend") and case.status == "OPEN" and doc.get("raw") and case.fix not in ("needs_drug", "correct")
                and (case.payer_claim_id or case.kind == "resend")):
            case.status = "READY"
    db.flush()
    return {"created": created, "refreshed": refreshed, "recovered": settled["recovered"], "denied_again": settled["denied_again"],
            "recovered_dollars": settled["dollars"]}


def _billing_npi(doc: dict) -> str:
    return next((s[9] for s in (doc.get("raw") or {}).get("hl20") or [] if s and s[0] == "NM1" and len(s) > 9 and s[1] == "85"), "")


def match(db: Session, remits: List["lm.Remit"], org: Optional[str] = None) -> dict:
    """Payments newer than a case settle it: paid -> Recovered (with the amount), still denied -> Denied again.
    With no org (ERAs read from Althais's own Claim.MD account, which serves every clinic), only claims Althais sent
    are matched, and only when the payment file's payee NPI is the claim's billing NPI: claim numbers can repeat
    across clinics."""
    out = {"recovered": 0, "denied_again": 0, "dollars": 0.0}
    by_claim = {}
    for r in remits:
        if not r.reversed:
            by_claim.setdefault(r.claim_id, []).append(r)
    if not by_claim:
        return out
    q = select(RecoveryCase).where(RecoveryCase.claim_id.in_(list(by_claim)[:5000]), RecoveryCase.status.in_(OPEN_STATES))
    if org is not None:
        q = q.where(RecoveryCase.org_key == org)
    else:
        q = q.where(RecoveryCase.sent_via == "althais")
    for case in db.scalars(q).all():
        newer = [r for r in by_claim.get(case.claim_id, []) if (r.date or "") > (case.basis_date or "")]
        if org is None:
            npi = _billing_npi(_open(case))
            newer = [r for r in newer if npi and r.payee_npi == npi]
        if not newer:
            continue
        r = max(newer, key=lambda x: x.date or "")
        whole = case.category == "no_response" or not case.code or "," in case.code
        lines = r.lines if whole else [l for l in r.lines if l.code == case.code]
        if r.lines and not lines:
            continue
        paid = [l for l in lines if l.paid > 0]
        if paid or (not r.lines and r.paid > 0):
            got = sum((lm._allowed(l) or l.paid) for l in paid) if paid else r.paid + r.patient_resp
            amount = round(case.expected if case.category == "underpaid" else got, 2)    # a reprocessed underpayment pays the difference
            case.status, case.recovered, case.closed_at = "RECOVERED", amount, _now()
            _log(case, f"Paid on {r.date or 'a newer payment file'}: ${amount:,.2f}" + (f" (check/EFT {r.check})" if r.check else ""))
            out["recovered"] += 1
            out["dollars"] += amount
        elif any(lm._denied(l) for l in lines) or r.status == "4":
            carc = lm._denial_carc([a for l in lines for a in l.adjustments] + r.adjustments)
            if case.status != "DENIED_AGAIN" or case.carc != carc:
                case.status = "DENIED_AGAIN"
                _log(case, f"Denied again on {r.date or 'a newer payment file'}: {carc} {lm.CARC.get(carc, '')}".strip())
                out["denied_again"] += 1
        case.basis_date = r.date or case.basis_date
    out["dollars"] = round(out["dollars"], 2)
    return out


def on_era(db: Session, era_text: str) -> dict:
    """Called for each ERA Althais reads from Claim.MD (billing_activation.poll_eras)."""
    try:
        remits = [r for ix in lm.interchanges(era_text) for r in lm.parse_835(ix, "claimmd")]
    except Exception:
        return {"recovered": 0, "denied_again": 0, "dollars": 0.0}
    return match(db, remits)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Corrected claims, rebuilt from the clinic's own 837
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def edits_for(case: RecoveryCase) -> List[dict]:
    """The change the report found, as edits a person can see and turn off."""
    f = case.fix or ""
    if f.startswith("add_modifier:"):
        _, code, mod = f.split(":")[:3]
        return [{"op": "add_modifier", "code": code, "mod": mod}]
    if f.startswith("add_line:"):
        _, code, charge = (f.split(":") + ["0"])[:3]
        return [{"op": "add_line", "code": code, "charge": float(charge or 0), "units": 1}]
    return []


def _elements(seg: list) -> str:
    return "*".join(re.sub(r"[~*]", " ", str(v)) for v in seg).rstrip("*")


def build_corrected(raw: dict, edits: List[dict], payer_claim_id: str = "", frequency: str = "7", for_claimmd: bool = False) -> str:
    """One claim as a new 837P file. frequency 7 replaces the paid/denied claim (with REF*F8, the payer's claim number);
    1 sends it as new (a claim the payer never got)."""
    if not raw or not raw.get("clm"):
        raise ValueError("The original claim file isn't saved for this case. Upload the 837 with the payment files and run the report again.")
    if frequency == "7" and not payer_claim_id:
        raise ValueError("A corrected claim needs the payer's claim number (it's on the payment file).")
    hl20 = [list(s) for s in raw.get("hl20") or []]
    hl22 = [list(s) for s in raw.get("hl22") or []]
    hl23 = [list(s) for s in raw.get("hl23") or []]
    clm = [list(s) for s in raw["clm"]]
    for e in edits:
        op = e.get("op")
        if op == "set_member_id":
            v = re.sub(r"[^A-Za-z0-9-]", "", str(e.get("value") or "")).upper()
            if not v:
                raise ValueError("Enter the member ID.")
            for s in hl22:
                if s[0] == "NM1" and len(s) > 1 and s[1] == "IL":
                    s += [""] * (10 - len(s))
                    s[8], s[9] = "MI", v
        elif op == "add_modifier":
            code, mod = str(e.get("code") or "").upper(), re.sub(r"[^A-Z0-9]", "", str(e.get("mod") or "").upper())[:2]
            hit = False
            for s in clm:
                if s[0] == "SV1" and len(s) > 1:
                    parts = s[1].split(":")
                    if len(parts) > 1 and parts[1].upper() == code:
                        mods = [m for m in parts[2:6] if m]
                        if mod not in mods:
                            if len(mods) >= 4:
                                raise ValueError(f"{code} already has four modifiers.")
                            mods.append(mod)
                        s[1] = ":".join(parts[:2] + mods)
                        hit = True
            if not hit:
                raise ValueError(f"{code} isn't on this claim.")
        elif op == "add_line":
            code = re.sub(r"[^A-Z0-9]", "", str(e.get("code") or "").upper())
            charge, units = float(e.get("charge") or 0), float(e.get("units") or 1)
            if not re.fullmatch(r"[A-Z0-9]{5}", code) or charge <= 0 or units <= 0:
                raise ValueError("A new line needs a 5-character code, a charge and units.")
            first = next((s for s in clm if s[0] == "SV1"), None)
            ptr = first[7] if first and len(first) > 7 and first[7] else "1"
            dos = next((s[3] for s in clm if s[0] == "DTP" and len(s) > 3 and s[1] == "472"), "")
            mods = [re.sub(r"[^A-Z0-9]", "", m.upper())[:2] for m in e.get("mods") or [] if m][:4]
            clm += [["LX", "0"], ["SV1", ":".join(["HC", code] + mods), f"{charge * units:.2f}", "UN", f"{units:g}", "", "", ptr]]
            if dos:
                clm.append(["DTP", "472", "D8", dos])
        else:
            raise ValueError(f"Unknown change: {op}")
    # the claim header: frequency, the payer's claim number, the total
    head = clm[0]
    head += [""] * (6 - len(head))
    pos = (head[5].split(":") + ["", "", ""])[:3]
    head[5] = ":".join([pos[0] or "11", pos[1] or "B", frequency])
    head[2] = f"{sum(float(s[2] or 0) for s in clm if s[0] == 'SV1'):.2f}"
    clm = [s for s in clm if not (s[0] == "REF" and len(s) > 1 and s[1] == "F8")]
    if frequency in ("7", "8"):
        at = 1
        while at < len(clm) and (clm[at][0] in ("DTP", "PWK", "CN1", "AMT") or (clm[at][0] == "REF" and len(clm[at]) > 1 and clm[at][1] in ("4N", "F5", "EW", "9F", "G1"))):
            at += 1
        clm.insert(at, ["REF", "F8", re.sub(r"[~*:^]", "", payer_claim_id)[:50]])
    n = 0
    for s in clm:
        if s[0] == "LX":
            n += 1
            s[1] = str(n)
    if hl20:
        hl20[0] = ["HL", "1", "", "20", "1"]
    if hl22:
        hl22[0] = ["HL", "2", "1", "22", "1" if hl23 else "0"]
    if hl23:
        hl23[0] = ["HL", "3", "2", "23", "0"]
    header = [list(s) for s in raw.get("header") or []]
    env = raw.get("env") or {}
    isa_q = (env.get("isa") or ["ZZ", "ALTHAIS", "ZZ", "CLAIMMD"]) + ["", "", "", ""]
    gs = (env.get("gs") or ["ALTHAIS", "CLAIMMD"]) + ["", ""]
    if for_claimmd:
        isa_q, gs = ["ZZ", "ALTHAIS", "ZZ", "CLAIMMD"], ["ALTHAIS", "CLAIMMD"]
        header = [["NM1", "40", "2", "CLAIMMD", "", "", "", "", "46", "CLAIMMD"] if (s[0] == "NM1" and len(s) > 1 and s[1] == "40") else s for s in header]
    now = dt.datetime.now()
    ctl = now.strftime("%H%M%S")[-9:].rjust(9, "0")
    body = [["ST", "837", "0001", "005010X222A1"], ["BHT", "0019", "00", f"R{ctl}", now.strftime("%Y%m%d"), now.strftime("%H%M"), "CH"]]
    body += header + hl20 + hl22 + hl23 + clm
    body.append(["SE", str(len(body) + 1), "0001"])
    isa = (f"ISA*00*          *00*          *{(isa_q[0] or 'ZZ')[:2]}*{(isa_q[1] or 'ALTHAIS')[:15].ljust(15)}*{(isa_q[2] or 'ZZ')[:2]}*"
           f"{(isa_q[3] or 'CLAIMMD')[:15].ljust(15)}*{now.strftime('%y%m%d')}*{now.strftime('%H%M')}*^*00501*{ctl}*0*P*:")
    segs = [isa, f"GS*HC*{gs[0] or 'ALTHAIS'}*{gs[1] or 'CLAIMMD'}*{now.strftime('%Y%m%d')}*{now.strftime('%H%M')}*1*X*005010X222A1"]
    segs += [_elements(s) for s in body] + ["GE*1*1", f"IEA*1*{ctl}"]
    return "~\n".join(segs) + "~\n"


def can_send(db: Session, org: str, case: RecoveryCase, doc: dict) -> dict:
    """Through Althais only when the claim is this clinic's own: its billing NPI is the clinic's, in Billing Activation."""
    import clearinghouse as chx
    if case.kind not in ("corrected", "resend"):
        return {"ok": False, "why": "Letters go to the payer's appeals address, portal or fax. Download the PDF."}
    if not doc.get("raw"):
        return {"ok": False, "why": "The original claim file isn't saved. Upload the 837 with the payment files and run the report again."}
    conn = chx.connector_for(org)
    if isinstance(conn, chx.Sandbox):
        return {"ok": True, "why": "Test mode: nothing goes to a payer.", "test": True}
    if not isinstance(conn, chx.ClaimMD):
        return {"ok": False, "why": "Althais's clearinghouse isn't connected for this clinic. Download the claim file for your clearinghouse."}
    import billing_activation as ba
    _, data = ba.load_profile(db, org, create=False)
    npi = re.sub(r"\D", "", str(data.get("npi") or ""))
    claim_npi = _billing_npi(doc)
    if not npi or npi != re.sub(r"\D", "", claim_npi):
        return {"ok": False, "why": "This claim's billing NPI isn't the one in your Billing Activation. Download the corrected claim and send it through your clearinghouse."}
    return {"ok": True, "why": "Sends through Althais (Claim.MD)."}


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Letters
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

ARGUMENT = {
    "records": ("The service was medically necessary and is documented in the enclosed visit note. "
                "[From the visit note: the presenting problem, the findings, and why {code} was needed that day.] "
                "Please reprocess the claim for payment."),
    "auth": ("[Either: Authorization was obtained before the visit, number ____. Or: No authorization could be obtained because the "
             "patient needed urgent care the same day: say what made it urgent.] Please reprocess the claim for payment."),
    "noncovered": ("We believe this service is covered under the patient's plan. [Cite the plan provision or the reason it applies, "
                   "or say that the patient signed a waiver and will be billed.] Please review the denial."),
    "bundle": ("The evaluation and management service was significant and separately identifiable from the procedure performed the "
               "same day, as documented in the enclosed note, so modifier 25 applies under CPT guidelines. "
               "[From the note: the problem evaluated beyond the procedure.] Please reprocess {code} separately."),
    "duplicate": ("This is not a duplicate. [Explain: a separate visit, a different service, or a repeat service and why.] "
                  "Please reprocess the claim."),
    "coverage": ("The patient was covered on the date of service. [Give the correct member ID or primary payer, "
                 "and how coverage was confirmed (eligibility check date or card).] Please reprocess the claim."),
    "fix": ("The claim has been corrected. [Say what was corrected.] Please reprocess it."),
    "timely": ("The claim was filed on time. [Give the original submission date and attach the clearinghouse acceptance report.] "
               "Please reprocess it."),
    "review": ("[State why the denial is wrong, from the visit note and the payer's remark codes.] Please reprocess the claim."),
}


def draft_letter(case: RecoveryCase, doc: dict, profile: dict) -> str:
    addr = profile.get("address") or {}
    contact = profile.get("contact") or {}
    clinic = profile.get("legalName") or "[Clinic legal name]"
    today = dt.date.today().strftime("%B %d, %Y").replace(" 0", " ")
    head = [today, "", f"{case.payer or '[Payer]'}", "Attn: Provider Appeals / Claim Reconsideration", "[Payer appeals address or fax]", "",
            f"Re: {'Request for reconsideration of payment' if case.kind == 'reconsideration' else 'Appeal of claim denial'}",
            f"Patient: {doc.get('patient') or '[Patient name]'}",
            f"Member ID: {doc.get('member_id') or '[Member ID]'}",
            f"Payer claim number: {case.payer_claim_id or '[Payer claim number]'}",
            f"Our claim number: {case.claim_id}",
            f"Date of service: {case.dos or '[Date of service]'}",
            f"Service: {case.code}",
            f"Billing provider: {clinic}, NPI {profile.get('npi') or '[NPI]'}", "", "To whom it may concern:", ""]
    if case.kind == "reconsideration":
        ev = doc.get("evidence") or []
        body = [f"{case.payer} processed {case.code} on this claim below the amount it usually allows our clinic for this service. "
                f"{case.why}",
                "",
                ("Claims where you allowed the usual amount for the same service: " + "; ".join(ev) + ".") if ev else
                "[List two or three claims where the payer allowed the usual amount for this service.]",
                "",
                f"Please reprocess the line at the contracted rate and pay the difference of ${case.expected:,.2f}."]
    else:
        kind = lm.CARC_KIND.get(case.carc, "review")
        reason = f"reason code {case.carc} ({lm.CARC.get(case.carc, 'see the remittance')})" if case.carc else "no reason code"
        body = [f"We are appealing the denial of {case.code} on this claim, which was denied with {reason}.",
                "", ARGUMENT.get(kind, ARGUMENT["review"]).format(code=case.code)]
    tail = ["", "Enclosed: the visit note, a copy of the remittance advice" + (", and the corrected claim." if case.kind == "corrected" else "."),
            "", "Thank you,", "", contact.get("name") or "[Name]", f"Billing, {clinic}",
            ", ".join(x for x in (addr.get("line1"), addr.get("city"), addr.get("state"), addr.get("zip")) if x) or "[Address]",
            " | ".join(x for x in (contact.get("phone"), contact.get("email")) if x) or "[Phone / email]"]
    if "medicare" in (case.payer or "").lower() and "advantage" not in (case.payer or "").lower() and case.kind != "reconsideration":
        tail += ["", "(Original Medicare: send this with the Medicare redetermination form, CMS-20027, within 120 days of the notice.)"]
    return "\n".join(head + body + tail)


def placeholders(text: str) -> List[str]:
    return re.findall(r"\[[^\]]{2,200}\]", text or "")


def letter_pdf(text: str) -> bytes:
    from fpdf import FPDF
    pdf = FPDF(format="Letter")
    pdf.set_margins(22, 20, 22)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    safe = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    for para in safe.encode("latin-1", "replace").decode("latin-1").split("\n"):
        pdf.multi_cell(0, 5.6, para or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _biller(user=Depends(require_user)):
    if ROLE_LEVELS.get(user.role or "viewer", 0) < ROLE_LEVELS["biller"]:
        raise HTTPException(status_code=403, detail="Billers and admins only.")
    return user


def _org(user) -> str:
    import billing_activation as ba
    org = ba.org_of(user)
    if org.startswith("user:"):
        raise HTTPException(status_code=400, detail="Add your clinic's name in Settings first.")
    return org


def _case(db, user, cid: int) -> RecoveryCase:
    case = db.get(RecoveryCase, cid)
    if not case or case.org_key != _org(user):
        raise HTTPException(status_code=404, detail="No such case.")
    return case


def _profile(db, org: str, doc: dict):
    """The clinic's billing profile; the claim's own billing provider fills what isn't set up yet."""
    import billing_activation as ba
    row, data = ba.load_profile(db, org, create=False)
    nm1 = next((s for s in (doc.get("raw") or {}).get("hl20") or [] if s and s[0] == "NM1" and len(s) > 9 and s[1] == "85"), None)
    if nm1:
        data = dict(data)
        data["legalName"] = data.get("legalName") or nm1[3].title()
        data["npi"] = data.get("npi") or nm1[9]
    return row, data


def _row(c: RecoveryCase) -> dict:
    return {"id": c.id, "category": c.category, "kind": c.kind, "kindLabel": KIND_LABEL.get(c.kind, c.kind), "status": c.status,
            "statusLabel": STATUS_LABEL.get(c.status, c.status), "claimId": c.claim_id, "payerClaimId": c.payer_claim_id, "payer": c.payer,
            "dos": c.dos, "code": c.code, "carc": c.carc, "title": c.title, "action": c.action, "why": c.why, "expected": c.expected,
            "recovered": c.recovered, "deadline": c.deadline, "reference": c.reference, "sentVia": c.sent_via,
            "daysLeft": (dt.date.fromisoformat(c.deadline) - dt.date.today()).days if c.deadline else None,
            "updatedAt": c.updated_at.isoformat() + "Z" if c.updated_at else ""}


@router.get("/api/recovery/cases")
def list_cases(user=Depends(_biller), db: Session = Depends(get_db)):
    org = _org(user)
    rows = db.scalars(select(RecoveryCase).where(RecoveryCase.org_key == org).order_by(RecoveryCase.id.desc()).limit(5000)).all()
    open_ = [c for c in rows if c.status in OPEN_STATES]
    return {"cases": [_row(c) for c in rows],
            "totals": {"open": round(sum(c.expected for c in open_), 2), "openCount": len(open_),
                       "sent": round(sum(c.expected for c in rows if c.status == "SENT"), 2),
                       "recovered": round(sum(c.recovered for c in rows if c.status in ("RECOVERED", "PARTIAL")), 2),
                       "recoveredCount": sum(1 for c in rows if c.status in ("RECOVERED", "PARTIAL")),
                       "dueSoon": sum(1 for c in open_ if c.deadline and (dt.date.fromisoformat(c.deadline) - dt.date.today()).days <= 14)}}


@router.get("/api/recovery/cases/{cid}")
def case_detail(cid: int, user=Depends(_biller), db: Session = Depends(get_db)):
    import billing_activation as ba
    org = _org(user)
    case = _case(db, user, cid)
    doc = _open(case)
    out = _row(case)
    out["history"] = json.loads(case.history or "[]")
    out["hasClaim"] = bool(doc.get("raw"))
    out["edits"] = edits_for(case)
    out["needsDrug"] = case.fix == "needs_drug"
    out["coverage"] = lm.CARC_KIND.get(case.carc) == "coverage"
    out["memberId"] = doc.get("member_id", "")
    if case.kind in ("appeal", "reconsideration") or (case.kind == "corrected" and case.category == "denial" and case.fix == "correct"):
        _, profile = _profile(db, org, doc)
        out["letter"] = doc.get("letter") or draft_letter(case, doc, profile)
        out["placeholders"] = placeholders(out["letter"])
    out["send"] = can_send(db, org, case, doc)
    return out


@router.post("/api/recovery/cases/{cid}/letter")
async def save_letter(cid: int, request: Request, user=Depends(_biller), db: Session = Depends(get_db)):
    body = await request.json()
    case = _case(db, user, cid)
    doc = _open(case)
    doc["letter"] = str(body.get("text") or "")[:20000]
    case.sealed = _seal(doc)
    _log(case, "Letter edited", user.email)
    db.commit()
    return {"ok": True, "placeholders": placeholders(doc["letter"])}


@router.get("/api/recovery/cases/{cid}/letter.pdf")
def letter_download(cid: int, user=Depends(_biller), db: Session = Depends(get_db)):
    import billing_activation as ba
    case = _case(db, user, cid)
    doc = _open(case)
    _, profile = _profile(db, case.org_key, doc)
    text = doc.get("letter") or draft_letter(case, doc, profile)
    left = placeholders(text)
    if left:
        return JSONResponse({"error": "Fill in the parts in [brackets] first: " + "; ".join(left[:3])}, status_code=400)
    _log(case, "Letter downloaded", user.email)
    db.commit()
    return Response(letter_pdf(text), media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=appeal-{re.sub(r'[^A-Za-z0-9-]', '', case.claim_id)}.pdf", "Cache-Control": "no-store"})


def _claim_for(case: RecoveryCase, doc: dict, body: dict, for_claimmd: bool) -> str:
    edits = [e for e in (body.get("edits") if body.get("edits") is not None else edits_for(case)) if isinstance(e, dict)]
    if body.get("memberId"):
        edits.append({"op": "set_member_id", "value": body["memberId"]})
    if body.get("drug"):
        d = body["drug"]
        edits.append({"op": "add_line", "code": d.get("code"), "charge": d.get("charge"), "units": d.get("units") or 1})
    if case.fix == "needs_drug" and not body.get("drug"):
        raise ValueError("Add the drug's J-code, units and charge from the visit note.")
    freq = "1" if case.kind == "resend" else "7"
    return build_corrected(doc.get("raw") or {}, edits, case.payer_claim_id, freq, for_claimmd=for_claimmd)


@router.post("/api/recovery/cases/{cid}/claim")
async def claim_file(cid: int, request: Request, user=Depends(_biller), db: Session = Depends(get_db)):
    """The corrected claim as an 837 file, for the clinic's own clearinghouse."""
    body = await request.json()
    case = _case(db, user, cid)
    try:
        edi = _claim_for(case, _open(case), body, for_claimmd=False)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _log(case, "Corrected claim downloaded", user.email)
    db.commit()
    return Response(edi, media_type="application/edi-x12",
                    headers={"Content-Disposition": f"attachment; filename=corrected-{re.sub(r'[^A-Za-z0-9-]', '', case.claim_id)}.837", "Cache-Control": "no-store"})


@router.post("/api/recovery/cases/{cid}/send")
async def send_case(cid: int, request: Request, user=Depends(_biller), db: Session = Depends(get_db)):
    """Send the corrected claim through Althais's Claim.MD account. Needs confirm: true."""
    import clearinghouse as chx
    import billing_activation as ba
    body = await request.json()
    if body.get("confirm") is not True:
        return JSONResponse({"error": "Confirm before sending."}, status_code=400)
    org = _org(user)
    case = _case(db, user, cid)
    if case.status == "SENT":
        return {"ok": True, "status": "already_sent", "reference": case.reference}
    doc = _open(case)
    ok = can_send(db, org, case, doc)
    if not ok["ok"]:
        return JSONResponse({"error": ok["why"]}, status_code=400)
    try:
        edi = _claim_for(case, doc, body, for_claimmd=True)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    conn = chx.connector_for(org)
    res = conn.upload_x12(f"recovery-{case.id}.837", edi)
    if not res.ok:
        _log(case, f"Send failed: {res.message}", user.email)
        db.commit()
        return JSONResponse({"error": res.message}, status_code=502)
    case.status, case.sent_via, case.sent_at, case.reference = "SENT", "althais", _now(), (res.reference or "")[:128]
    case.basis_date = max(case.basis_date or "", dt.date.today().isoformat())
    _log(case, ("Sent in test mode" if res.environment == "sandbox" else "Sent through Althais (Claim.MD)") + (f", reference {res.reference}" if res.reference else ""), user.email)
    ba.audit(db, org, user, "recovery_claim_sent", f"case {case.id}, claim {case.claim_id}")
    db.commit()
    return {"ok": True, "status": "sent", "reference": res.reference, "test": res.environment == "sandbox", "message": res.message}


@router.post("/api/recovery/cases/{cid}/status")
async def set_status(cid: int, request: Request, user=Depends(_biller), db: Session = Depends(get_db)):
    body = await request.json()
    case = _case(db, user, cid)
    st = str(body.get("status") or "")
    if st not in STATUS_LABEL:
        return JSONResponse({"error": "Unknown status."}, status_code=400)
    case.status = st
    if st == "SENT":
        case.sent_via, case.sent_at = case.sent_via or "clinic", case.sent_at or _now()
        case.basis_date = max(case.basis_date or "", dt.date.today().isoformat())
    if st in ("RECOVERED", "PARTIAL"):
        try:
            case.recovered = round(max(float(body.get("recovered") if body.get("recovered") is not None else case.expected), 0), 2)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Enter the amount recovered."}, status_code=400)
    if st in ("RECOVERED", "PARTIAL", "CLOSED"):
        case.closed_at = _now()
    note = str(body.get("note") or "")[:300]
    _log(case, f"Marked {STATUS_LABEL[st].lower()}" + (f": ${case.recovered:,.2f}" if st in ("RECOVERED", "PARTIAL") else "") + (f". {note}" if note else ""), user.email)
    db.commit()
    return _row(case)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  The page
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

@router.get("/recovery", response_class=HTMLResponse)
def recovery_page(request: Request, db: Session = Depends(get_db)):
    u = current_user(request, db)
    if not u:
        return RedirectResponse("/login?next=/recovery", status_code=303)
    if ROLE_LEVELS.get(u.role or "viewer", 0) < ROLE_LEVELS["biller"]:
        return HTMLResponse("Billers and admins only.", status_code=403)
    return HTMLResponse(PAGE.replace("/*STYLE*/", lm.STYLE), headers={"Cache-Control": "no-store"})


PAGE = r"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Recovery</title><style>/*STYLE*/
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 10px}.tabs button{background:var(--chip);color:var(--ink)}.tabs button.on{background:var(--accent);color:#fff}
tr.r{cursor:pointer}tr.r:hover td{background:var(--chip)}
.panel{position:fixed;top:0;right:0;bottom:0;width:min(640px,100%);background:var(--card);border-left:1px solid var(--line);overflow:auto;padding:22px 20px 40px;box-shadow:-12px 0 40px rgba(0,0,0,.12);display:none}
.panel.on{display:block}.x{float:right;background:var(--chip);color:var(--ink)}
textarea{width:100%;min-height:340px;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
input{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}.ghost{background:var(--chip);color:var(--ink)}
.warn{background:var(--warn-soft);color:var(--warn);padding:8px 10px;border-radius:8px;font-size:13px}
.ok{background:var(--accent-soft);color:var(--accent);padding:8px 10px;border-radius:8px;font-size:13px}
.hist{font-size:12.5px;color:var(--muted)}.hist li{margin:3px 0}label.e{display:flex;gap:8px;align-items:center;margin:4px 0}
.empty{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px;text-align:center;color:var(--muted)}
</style></head><body><main>
<div class=top><div><h1>Recovery</h1><p class=sub>Every item the Lost Money Report found, worked until it's paid. Payments close cases on their own.</p></div>
<a href="/lost-money"><button>Run a Lost Money Report</button></a></div>
<div class=cards>
<div class="card hero"><div class=k>Recovered</div><div class=v id=tRec>$0</div><div class=s id=tRecN></div></div>
<div class=card><div class=k>Still to recover</div><div class=v id=tOpen>$0</div><div class=s id=tOpenN></div></div>
<div class=card><div class=k>Sent, waiting on payers</div><div class=v id=tSent>$0</div><div class=s>&nbsp;</div></div>
<div class=card><div class=k>Deadlines in 14 days</div><div class=v id=tDue>0</div><div class=s>work these first</div></div></div>
<div class=tabs id=tabs></div><div id=list></div></main>
<div class=panel id=panel></div>
<script>
const $=s=>document.querySelector(s),money=v=>!v?'$0':'$'+Number(v||0).toLocaleString(undefined,{maximumFractionDigits:v>=100?0:2,minimumFractionDigits:v>=100?0:2});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let ALL=[],TAB='todo';
const TABS={todo:['To do',c=>['OPEN','READY','DENIED_AGAIN'].includes(c.status)],sent:['Sent',c=>c.status==='SENT'],done:['Recovered',c=>['RECOVERED','PARTIAL'].includes(c.status)],closed:['Closed',c=>c.status==='CLOSED'],all:['All',c=>true]};
async function api(u,o){const r=await fetch(u,Object.assign({credentials:'same-origin'},o||{}));if(!r.ok){let m=await r.text();try{m=JSON.parse(m).error||JSON.parse(m).detail||m}catch(_){};throw new Error(m)}return r}
async function load(){const j=await (await api('/api/recovery/cases')).json();ALL=j.cases;const t=j.totals;
$('#tRec').textContent=money(t.recovered);$('#tRecN').textContent=t.recoveredCount+' item(s)';$('#tOpen').textContent=money(t.open);$('#tOpenN').textContent=t.openCount+' item(s)';
$('#tSent').textContent=money(t.sent);$('#tDue').textContent=t.dueSoon;draw()}
function draw(){$('#tabs').innerHTML=Object.entries(TABS).map(([k,[l,f]])=>`<button class="${k===TAB?'on':''}" onclick="TAB='${k}';draw()">${l} (${ALL.filter(f).length})</button>`).join('');
const rows=ALL.filter(TABS[TAB][1]).sort((a,b)=>(a.daysLeft??9999)-(b.daysLeft??9999)||b.expected-a.expected);
if(!ALL.length){$('#list').innerHTML='<div class=empty>Nothing here yet. Run a Lost Money Report with your payment files, and keep “Save what can be recovered” on.</div>';return}
$('#list').innerHTML='<div class=wrap><table><tr><th>Item</th><th>Claim</th><th>Payer</th><th>Next step</th><th>Deadline</th><th>Status</th><th class=n>Dollars</th></tr>'+rows.map(c=>
`<tr class=r onclick="open_(${c.id})"><td><b>${esc(c.title)}</b><div class=why>${esc(c.dos)} · ${esc(c.code)}</div></td><td>${esc(c.claimId)}</td><td>${esc(c.payer)}</td><td>${esc(c.kindLabel)}</td>
<td class="${c.daysLeft!=null&&c.daysLeft<15?'late':''}" style="white-space:nowrap">${c.deadline?esc(c.deadline)+'<div class=why>'+c.daysLeft+' days</div>':'—'}</td>
<td><span class="chip ${c.status==='RECOVERED'?'high':c.status==='DENIED_AGAIN'?'medium':'low'}">${esc(c.statusLabel)}</span></td>
<td class=n>${c.status==='RECOVERED'||c.status==='PARTIAL'?money(c.recovered):money(c.expected)}</td></tr>`).join('')+'</table></div>'}
let CUR=null;
async function open_(id){const c=await (await api('/api/recovery/cases/'+id)).json();CUR=c;const P=$('#panel');P.classList.add('on');
let h=`<button class="x" onclick="$('#panel').classList.remove('on')">Close</button><h2 style="margin-top:0">${esc(c.title)}</h2>
<p class=sub>${esc(c.payer)} · claim ${esc(c.claimId)}${c.payerClaimId?' · payer # '+esc(c.payerClaimId):''} · ${esc(c.dos)} · <b>${money(c.expected)}</b></p>
<p>${esc(c.why)}</p><p><b>What gets it paid:</b> ${esc(c.action)}</p>`;
if(c.kind==='corrected'||c.kind==='resend'){
 h+=`<h2>${c.kind==='resend'?'Send the claim again':'Corrected claim'}</h2>`;
 if(!c.hasClaim)h+=`<div class=warn>The original claim file isn't saved for this case. Upload the claim files (837) with the payment files and run the report again, or fix it in your own system.</div>`;
 else{ if(c.edits.length)h+=c.edits.map((e,i)=>`<label class=e><input type=checkbox checked data-i="${i}"> ${e.op==='add_modifier'?'Add modifier '+esc(e.mod)+' to '+esc(e.code):'Add '+esc(e.code)+' at '+money(e.charge)}</label>`).join('');
  else if(c.kind==='corrected'&&!c.needsDrug)h+=`<p class=why>Make the fix the payer asked for in your system, or download this claim as a corrected claim (frequency 7, with the payer's claim number) and edit it.</p>`;
  if(c.needsDrug)h+=`<div class=row>Drug from the note: <input id=dCode placeholder="J-code" size=7> units <input id=dUnits value=1 size=3> charge $<input id=dCharge size=6></div>`;
  if(c.coverage)h+=`<div class=row>Correct member ID <input id=mid value="${esc(c.memberId)}"></div>`;
  h+=`<div class=row><button onclick="send()" ${c.send.ok&&c.status!=='SENT'?'':'disabled'}>${c.status==='SENT'?'Sent':'Send through Althais'}</button><button class=ghost onclick="dlClaim()">Download corrected claim (837)</button></div>
  <div class="${c.send.ok?'ok':'warn'}">${esc(c.send.why)}</div>`;}}
if(c.letter!==undefined){h+=`<h2>${c.kind==='reconsideration'?'Reconsideration letter':'Appeal letter'}</h2><p class=sub>Fill in everything in [brackets] from the visit note. Nothing in this letter is guessed.</p>
<textarea id=letter>${esc(c.letter)}</textarea><div id=ph></div><div class=row><button class=ghost onclick="saveLetter()">Save</button><button onclick="dlLetter()">Download PDF</button></div>`}
h+=`<h2>Status</h2><div class=row><button class=ghost onclick="st('SENT')">Mark sent</button><button class=ghost onclick="rec()">Mark recovered</button><button class=ghost onclick="st('CLOSED')">Close (won't recover)</button><button class=ghost onclick="st('OPEN')">Reopen</button></div>
<p class=sub>Currently: <b>${esc(c.statusLabel)}</b>${c.reference?' · reference '+esc(c.reference):''}</p><ul class=hist>${c.history.slice().reverse().map(x=>`<li>${esc(x.at.slice(0,16).replace('T',' '))} — ${esc(x.what)}${x.who?' ('+esc(x.who)+')':''}</li>`).join('')}</ul><p id=msg></p>`;
P.innerHTML=h;if(c.letter!==undefined)ph(c.placeholders)}
function ph(list){$('#ph').innerHTML=list.length?`<div class=warn>${list.length} part(s) still to fill in: ${list.slice(0,3).map(esc).join(' · ')}</div>`:'<div class=ok>Ready to download.</div>'}
function body(){const b={edits:(CUR.edits||[]).filter((e,i)=>{const x=document.querySelector(`input[data-i="${i}"]`);return !x||x.checked})};
 if($('#mid')&&$('#mid').value!==CUR.memberId)b.memberId=$('#mid').value;
 if($('#dCode'))b.drug={code:$('#dCode').value,units:+$('#dUnits').value||1,charge:+$('#dCharge').value||0};return b}
const msg=(t,bad)=>{const m=$('#msg');m.className=bad?'warn':'ok';m.textContent=t};
async function dlClaim(){try{const r=await api(`/api/recovery/cases/${CUR.id}/claim`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body())});
 const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download='corrected-'+CUR.claimId+'.837';a.click();msg('Downloaded. Send it through your clearinghouse, then Mark sent.')}catch(e){msg(e.message,1)}}
async function send(){if(!confirm(`Send this claim for ${CUR.payer} through Althais now?`))return;
 try{const j=await (await api(`/api/recovery/cases/${CUR.id}/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign(body(),{confirm:true}))})).json();
 msg(j.test?'Sent in test mode (nothing went to a payer).':'Sent. Althais will close this case when the payment comes in.');await load();open_(CUR.id)}catch(e){msg(e.message,1)}}
async function saveLetter(){try{const j=await (await api(`/api/recovery/cases/${CUR.id}/letter`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('#letter').value})})).json();ph(j.placeholders);msg('Saved.')}catch(e){msg(e.message,1)}}
async function dlLetter(){try{await saveLetter();const r=await api(`/api/recovery/cases/${CUR.id}/letter.pdf`);const u=URL.createObjectURL(await r.blob());
 const a=document.createElement('a');a.href=u;a.download='letter-'+CUR.claimId+'.pdf';a.click();msg('Downloaded. Send it to the payer, then Mark sent.')}catch(e){msg(e.message,1)}}
async function st(s,extra){try{await api(`/api/recovery/cases/${CUR.id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({status:s},extra||{}))});await load();open_(CUR.id)}catch(e){msg(e.message,1)}}
function rec(){const v=prompt('Amount recovered ($)',CUR.expected);if(v===null)return;st('RECOVERED',{recovered:+v})}
load().catch(e=>$('#list').innerHTML='<div class=empty>'+esc(e.message)+'</div>');
</script></body></html>"""
