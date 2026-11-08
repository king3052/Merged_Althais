"""
lost_money.py: the Lost Money Report. Point it at a clinic's claim files (837P) and payment files (835 ERAs), or a
claims export (CSV), and it finds the money the clinic is leaving on the table, in dollars, claim by claim.

It needs no contracts, no fee schedules and no change of EHR: every expected amount comes from what the clinic's own
payers have paid it before. Nothing is stored; files are read in memory and the report is returned.

What it looks for (each finding carries dollars, a confidence and the action that gets the money):
  1. Denials still worth fixing or appealing, with the deadline and the fix for each reason code
  2. Underpayments: lines a payer paid below the rate it usually pays this clinic for the same code
  3. Prices set below what payers pay ("lesser of charge or fee schedule" caps the payment at the clinic's price)
  4. Missed charges: add-on codes a payer pays the clinic on some visits but that are missing on others (99051 on
     weekends, S9088), injections without the drug or the drug without the injection, nebulizer pairs, venipuncture
  5. Claims sent with no answer from the payer
  6. Coding risks: deleted codes (99201, 99241, 99251...), procedure + visit without modifier 25, waived tests without QW
  7. Visit-level pattern: the share of level 3 visits against urgent care benchmarks, clinic-wide and per provider
  8. Rules each payer taught us: its denial patterns, written as checks to run before the next claim goes out
plus money lost for good (past the deadline: the prevention case) and patient balances to collect.

Use it:
  - in the app: /lost-money (billers and admins, on their own files) and /admin/lost-money (Althais staff, for a
    prospect's files under a BAA)
  - from a terminal: python lost_money.py FILES_OR_FOLDERS... -o report.html  (also writes the worklist as CSV)
  - python lost_money.py --sample -o sample.html   builds a report from made-up data, for demos

Estimates are labelled as such. Dollar amounts marked "possible" need a person to confirm before anyone acts on them.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import os
import re
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Reference data
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

CARC = {
    "1": "Deductible", "2": "Coinsurance", "3": "Copay", "4": "Procedure code inconsistent with the modifier, or a modifier is missing",
    "5": "Procedure code or type of bill inconsistent with the place of service", "6": "Procedure inconsistent with the patient's age",
    "7": "Procedure inconsistent with the patient's sex", "8": "Procedure inconsistent with the provider type or specialty",
    "9": "Diagnosis inconsistent with the patient's age", "10": "Diagnosis inconsistent with the patient's sex",
    "11": "Diagnosis inconsistent with the procedure", "15": "Authorization number missing or invalid",
    "16": "Claim lacks information or has submission errors", "18": "Duplicate claim or service",
    "22": "Another payer may be primary (coordination of benefits)", "23": "Impact of prior payer's adjudication",
    "24": "Covered by a capitation agreement or managed care plan", "26": "Expenses before coverage started",
    "27": "Expenses after coverage ended", "29": "Filing deadline passed", "31": "Patient not identified as the payer's insured",
    "45": "Charge exceeds the fee schedule or contracted amount", "50": "Not deemed medically necessary",
    "55": "Experimental or investigational", "59": "Multiple or concurrent procedure rules", "96": "Non-covered charge",
    "97": "Included in the payment for another service (bundled)", "109": "Not covered by this payer; bill the correct payer",
    "119": "Benefit maximum reached", "125": "Submission or billing error", "129": "Prior processing information incorrect",
    "140": "Patient or insured health ID number and name do not match", "146": "Diagnosis invalid for the date of service",
    "150": "Payment adjusted: level of service not supported by the information submitted", "151": "Frequency or number of services not supported",
    "167": "Diagnosis not covered", "170": "Not paid when performed by this type of provider", "177": "Patient has not met eligibility requirements",
    "181": "Procedure code invalid on the date of service", "182": "Modifier invalid on the date of service",
    "185": "Rendering provider not eligible to perform the service", "197": "Precertification or authorization absent",
    "198": "Precertification or authorization exceeded", "200": "Expenses incurred during a lapse in coverage",
    "204": "Service not covered by the patient's current benefit plan", "206": "NPI missing", "207": "NPI invalid",
    "208": "NPI does not match", "226": "Information requested from the provider was not provided or was insufficient",
    "234": "Procedure not paid separately", "236": "Procedure or modifier combination not allowed (NCCI)",
    "252": "Attachment or other documentation required", "253": "Sequestration (federal 2% reduction)",
    "B7": "Provider not certified or eligible to be paid for this procedure on this date", "B15": "Service requires another qualifying service",
    "B16": "New patient qualifications not met", "N/A": "",
}

# What gets each denial reason paid. kind: how the money comes back. odds: how often that works, as a rough tier.
PLAYBOOK = {
    "fix": ("Fix and resubmit a corrected claim", "high"),
    "coverage": ("Re-check the patient's insurance and bill the right payer, or fix the member ID", "high"),
    "bundle": ("Add the right modifier (25 on the visit, or 59/XU on the procedure) if the service was separate, then resubmit", "medium"),
    "records": ("Appeal with the visit note and any requested records", "medium"),
    "auth": ("Ask the payer for a retro-authorization, then appeal", "medium"),
    "noncovered": ("Check whether the plan really excludes it; if the patient signed a waiver, bill the patient", "low"),
    "duplicate": ("Check it isn't really a duplicate; if it was a separate service, resubmit with a modifier (76/77/59) and notes", "low"),
    "timely": ("Only recoverable with proof it was filed on time (clearinghouse acceptance report)", "low"),
    "review": ("Read the payer's remark and decide", "medium"),
}
CARC_KIND = {
    **{c: "fix" for c in ("4", "5", "6", "7", "8", "9", "10", "11", "16", "125", "129", "140", "146", "181", "182", "206", "207", "208", "B16")},
    **{c: "coverage" for c in ("22", "24", "26", "27", "31", "109", "177", "200")},
    **{c: "bundle" for c in ("97", "234", "236", "B15")},
    **{c: "records" for c in ("50", "55", "150", "151", "167", "226", "252", "170", "185", "B7")},
    **{c: "auth" for c in ("15", "197", "198")},
    **{c: "noncovered" for c in ("96", "119", "204")},
    "18": "duplicate", "29": "timely",
}
# Reductions that are normal and not an underpayment
NORMAL_REDUCTIONS = {"45", "253", "59", "131", "94", "237", "B10", "1", "2", "3", "23", "100", "102", "104", "144"}

# Days to appeal or correct after the payment file, by payer type. Real limits vary by contract: these are the usual
# floors, used to rank urgency, and the report says to check the contract.
APPEAL_DAYS = {"medicare": 120, "medicare_advantage": 60, "medicaid": 90, "commercial": 180}

# Filing indicator (CLP06 / SBR09) to payer type
FILING = {"MA": "medicare", "MB": "medicare", "16": "medicare_advantage", "MC": "medicaid", "CH": "commercial", "VA": "commercial",
          "WC": "commercial", "BL": "commercial", "CI": "commercial", "HM": "commercial", "12": "commercial", "13": "commercial",
          "14": "commercial", "15": "commercial", "17": "commercial", "AM": "commercial", "DS": "commercial", "LM": "commercial",
          "OF": "commercial", "TV": "commercial", "ZZ": "commercial", "11": "commercial"}

EM_NEW = ["99202", "99203", "99204", "99205"]
EM_EST = ["99211", "99212", "99213", "99214", "99215"]
EM_ALL = set(EM_NEW + EM_EST + ["99201"])
EM_LEVEL = {"99201": 1, "99202": 2, "99203": 3, "99204": 4, "99205": 5, "99211": 1, "99212": 2, "99213": 3, "99214": 4, "99215": 5}

# Codes no longer in CPT, or that Medicare doesn't pay. Urgent care still sends some of these.
DELETED = {"99201": "deleted from CPT in 2021; use 99202", "99241": "deleted from CPT in 2023; use an office visit code",
           "99251": "deleted from CPT in 2023", "99217": "observation codes were deleted in 2023",
           "99218": "observation codes were deleted in 2023", "99219": "observation codes were deleted in 2023",
           "99220": "observation codes were deleted in 2023"}
CONSULTS = {"99242", "99243", "99244", "99245"}   # Medicare stopped paying consult codes in 2010

# CLIA-waived tests Medicare and most Medicaid plans want the QW modifier on
QW_TESTS = {"87880": "rapid strep", "87804": "rapid flu", "87807": "rapid RSV", "87811": "rapid COVID antigen",
            "87426": "COVID antigen", "87428": "COVID + flu antigen", "87635": "COVID molecular", "87636": "COVID + flu molecular",
            "87637": "COVID + flu + RSV molecular", "87651": "strep molecular", "86308": "mono screen", "83036": "A1c",
            "81003": "automated urinalysis", "87502": "flu molecular", "85610": "PT/INR (point of care)"}

INHALATION_DRUG = re.compile(r"^J76(0[8-9]|[1-9]\d)$")     # J7608-J7699 inhalation solutions
NEB_ADMIN = {"94640", "94644", "94645"}
DRUG_ADMIN = {"96372", "96373", "96374", "96375", "96360", "96365"}
BLOOD_LABS = {"80047", "80048", "80050", "80051", "80053", "80061", "80069", "80074", "80076", "82550", "82565", "82947", "83690",
              "84075", "84295", "84443", "84450", "84460", "84484", "84520", "84702", "85025", "85027", "85651", "85652", "86140",
              "86308", "86592", "86677", "86701", "86803", "87040", "84132", "82310", "82374", "82435", "83735", "84100", "84155"}
VENIPUNCTURE = {"36415", "36416", "36410", "G0471"}

# Add-on codes some payers pay urgent care. Only flagged for a payer that paid the same code to this clinic in these files.
ADDONS = {"99051": "after-hours, weekend or holiday visit (weekend visits only are flagged)", "S9088": "services provided in an urgent care center"}

# Minor procedures and services that make the visit need modifier 25 when billed on the same day
def _proc_needs_25(code: str) -> bool:
    if not code.isdigit():
        return False
    n = int(code)
    return (10030 <= n <= 10180 or 11000 <= n <= 11765 or 12001 <= n <= 13160 or 16000 <= n <= 16036 or 17000 <= n <= 17286
            or 20526 <= n <= 20611 or 23650 <= n <= 23655 or 24640 == n or 26010 <= n <= 26785 or 28190 <= n <= 28675
            or 29000 <= n <= 29799 or 30300 <= n <= 30906 or 51701 <= n <= 51703 or 65205 <= n <= 65222 or 69200 <= n <= 69210
            or code in {"94640", "94644", "96372", "96373", "96374", "96360", "96365", "92950", "31500", "46050", "46083", "46320"})

PRICE_MODS = {"22", "26", "TC", "50", "51", "52", "53", "62", "73", "74", "80", "81", "82", "AS", "QK", "QX", "QZ"}

# Visit-level benchmark. Urgent care E/M coding clusters in levels 3 and 4. Published urgent care data puts level 3 well
# under 40% of visits once coding follows the 2021 guidelines (one 13-center urgent care network went from 38.7% to 27.0%
# level 3 after autonomous coding, Feb 2026; Experity's multi-year urgent care data shows level 4 overtaking level 3).
# Above this share of level 3 visits, the report estimates what moving the excess to level 4 would be worth. It is a
# pattern, not proof: each visit needs its note read before recoding.
LEVEL3_BENCHMARK = 0.35
LEVEL5_AUDIT_RISK = 0.10
DEFAULT_L3_TO_L4 = 35.00     # used only when the clinic's own payments don't show the 99213 -> 99214 difference


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Data model
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class Adj:
    group: str
    carc: str
    amount: float


@dataclass
class Line:
    code: str
    mods: List[str] = field(default_factory=list)
    units: float = 1.0
    charge: float = 0.0
    paid: float = 0.0
    allowed: Optional[float] = None
    adjustments: List[Adj] = field(default_factory=list)
    remarks: List[str] = field(default_factory=list)
    dos: str = ""


@dataclass
class Claim:
    """One claim as sent (837P or a CSV row group)."""
    claim_id: str
    payer: str = ""
    payer_id: str = ""
    payer_type: str = "commercial"
    dos: str = ""
    provider: str = ""
    pos: str = ""
    charge: float = 0.0
    dx: List[str] = field(default_factory=list)
    lines: List[Line] = field(default_factory=list)
    source: str = ""


@dataclass
class Remit:
    """One claim's answer from a payer (an 835 CLP loop, or a CSV row group with payment columns)."""
    claim_id: str
    payer: str = ""
    payer_id: str = ""
    payer_type: str = "commercial"
    status: str = ""            # CLP02
    charge: float = 0.0
    paid: float = 0.0
    patient_resp: float = 0.0
    payer_claim_id: str = ""
    date: str = ""              # payment date (BPR16)
    check: str = ""
    dos: str = ""
    provider: str = ""
    adjustments: List[Adj] = field(default_factory=list)
    lines: List[Line] = field(default_factory=list)
    source: str = ""

    @property
    def reversed(self) -> bool:
        return self.status == "22"


@dataclass
class Finding:
    category: str
    title: str
    dollars: float
    confidence: str             # high | medium | low
    action: str
    why: str = ""
    claim_id: str = ""
    payer_claim_id: str = ""
    payer: str = ""
    dos: str = ""
    code: str = ""
    deadline: str = ""
    days_left: Optional[int] = None
    counted: bool = True        # counts toward the money found (False: prevention or information)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Reading files
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _f(x) -> float:
    try:
        return float(str(x).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _date8(d: str) -> str:
    d = (d or "").strip()
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 and d[:8].isdigit() else ""


def _payer_type(indicator: str, name: str) -> str:
    n = (name or "").upper()
    t = FILING.get((indicator or "").upper(), "")
    if t:
        if t == "commercial" and "MEDICARE" in n:
            return "medicare_advantage"
        return t
    if "MEDICARE" in n:
        return "medicare_advantage" if any(w in n for w in ("ADVANTAGE", "HMO", "PPO", "HUMANA", "UNITED", "AETNA", "WELLCARE")) else "medicare"
    if "MEDICAID" in n or "CHIP" in n:
        return "medicaid"
    return "commercial"


def interchanges(text: str) -> List[str]:
    """Split text that may hold several ISA...IEA interchanges (files are often concatenated)."""
    text = (text or "").lstrip("﻿").strip()
    out, i = [], text.find("ISA")
    while i != -1:
        j = text.find("ISA", i + 106)
        # an "ISA" inside data is unlikely but possible: a real header has the element separator at offset 3 and 16 elements
        while j != -1 and not (len(text) > j + 105 and text[j + 3] == text[i + 3]):
            j = text.find("ISA", j + 3)
        out.append(text[i:j] if j != -1 else text[i:])
        i = j
    return out


def _prov_name(last: str, first: str, npi: str) -> str:
    name = " ".join(p for p in (first.title(), last.title()) if p)
    return f"{name} ({npi})" if name and npi else name or npi


def _segments(x: str):
    x = x.strip()
    if not x.startswith("ISA") or len(x) < 106:
        raise ValueError("not an X12 file")
    el, comp, term = x[3], x[104], x[105]
    if term in "\r\n":
        term = "\n"
    segs = []
    for raw in x.replace("\r\n", "\n").split(term):
        raw = raw.strip("\r\n ")
        if raw:
            segs.append(raw.split(el))
    return segs, comp


def _svc_code(v: str, comp: str) -> Tuple[str, List[str]]:
    parts = (v or "").split(comp)
    if len(parts) == 1:
        return parts[0].strip().upper(), []
    return parts[1].strip().upper(), [m.strip().upper() for m in parts[2:6] if m.strip()]


def parse_835(x: str, source: str = "") -> List[Remit]:
    segs, comp = _segments(x)
    remits: List[Remit] = []
    payer = payer_id = date = check = ""
    r: Optional[Remit] = None
    line: Optional[Line] = None
    for s in segs:
        sid = s[0].upper()
        g = lambda i: s[i].strip() if len(s) > i else ""
        if sid == "ST":
            payer = payer_id = date = check = ""
            r = line = None
        elif sid == "BPR":
            date = _date8(g(16))
        elif sid == "TRN":
            check = g(2)
        elif sid == "N1" and g(1) == "PR":
            payer = g(2)
        elif sid == "REF" and g(1) == "2U" and r is None:
            payer_id = g(2)
        elif sid == "CLP":
            r = Remit(claim_id=g(1), status=g(2), charge=_f(g(3)), paid=_f(g(4)), patient_resp=_f(g(5)), payer_claim_id=g(7),
                      payer=payer, payer_id=payer_id, payer_type=_payer_type(g(6), payer), date=date, check=check, source=source)
            remits.append(r)
            line = None
        elif r is None:
            continue
        elif sid == "NM1" and g(1) == "82":
            r.provider = _prov_name(g(3), g(4), g(9))
        elif sid == "DTM" and g(1) == "232" and not r.dos:
            r.dos = _date8(g(2))
        elif sid == "SVC":
            code, mods = _svc_code(g(1), comp)
            line = Line(code=code, mods=mods, charge=_f(g(2)), paid=_f(g(3)), units=_f(g(5)) or 1.0, dos=r.dos)
            r.lines.append(line)
        elif sid == "DTM" and g(1) in ("472", "150") and line is not None:
            line.dos = _date8(g(2))
            if not r.dos:
                r.dos = line.dos
        elif sid == "CAS":
            target = line if line is not None else r
            for i in range(2, len(s), 3):
                if s[i].strip():
                    target.adjustments.append(Adj(g(1), s[i].strip(), _f(s[i + 1] if i + 1 < len(s) else 0)))
        elif sid == "AMT" and g(1) == "B6" and line is not None:
            line.allowed = _f(g(2))
        elif sid == "LQ" and line is not None:
            line.remarks.append(g(2))
    return remits


def parse_837p(x: str, source: str = "") -> List[Claim]:
    segs, comp = _segments(x)
    claims: List[Claim] = []
    payer = payer_id = indicator = billing_npi = ""
    c: Optional[Claim] = None
    line: Optional[Line] = None
    nm1_ctx = ""
    for s in segs:
        sid = s[0].upper()
        g = lambda i: s[i].strip() if len(s) > i else ""
        if sid == "ST":
            if g(1) != "837":
                return claims
            c = line = None
        elif sid == "HL" and g(3) == "22":
            payer = payer_id = indicator = ""
            c = line = None
        elif sid == "SBR":
            indicator = g(9)
        elif sid == "NM1":
            nm1_ctx = g(1)
            if g(1) == "85":
                billing_npi = g(9)
            elif g(1) == "PR":
                payer, payer_id = g(3), g(9)
            elif g(1) == "82" and c is not None:
                c.provider = _prov_name(g(3), g(4), g(9))
        elif sid == "CLM":
            pos = (g(5).split(comp) or [""])[0]
            c = Claim(claim_id=g(1), charge=_f(g(2)), pos=pos, payer=payer, payer_id=payer_id,
                      payer_type=_payer_type(indicator, payer), provider=billing_npi, source=source)
            claims.append(c)
            line = None
        elif c is None:
            continue
        elif sid == "HI":
            for v in s[1:]:
                parts = v.split(comp)
                if len(parts) > 1 and parts[0] in ("ABK", "ABF", "BK", "BF"):
                    c.dx.append(parts[1])
        elif sid == "SV1":
            code, mods = _svc_code(g(1), comp)
            line = Line(code=code, mods=mods, charge=_f(g(2)), units=_f(g(4)) or 1.0)
            c.lines.append(line)
        elif sid == "DTP" and g(1) == "472":
            d = _date8(g(3).split("-")[0])
            if line is not None:
                line.dos = d
            if not c.dos:
                c.dos = d
    for cl in claims:
        if not cl.dos:
            cl.dos = next((l.dos for l in cl.lines if l.dos), "")
        for l in cl.lines:
            l.dos = l.dos or cl.dos
    return claims


# CSV exports: every PM system names columns differently. These are the names we recognise (lower case, no punctuation).
CSV_COLS = {
    "claim_id": ["claimid", "claim", "claimnumber", "claimno", "encounterid", "encounter", "visitid", "ticket", "invoice", "patientcontrolnumber", "pcn"],
    "dos": ["dos", "dateofservice", "servicedate", "visitdate", "fromdate", "date"],
    "payer": ["payer", "payername", "insurance", "insurancename", "carrier", "plan", "primaryinsurance"],
    "payer_type": ["payertype", "financialclass", "insurancetype", "plantype"],
    "code": ["cpt", "cptcode", "code", "procedure", "procedurecode", "hcpcs", "proccode", "cpthcpcs"],
    "mods": ["modifiers", "modifier", "mods", "mod", "modifier1"],
    "mod2": ["modifier2", "mod2"], "mod3": ["modifier3", "mod3"],
    "units": ["units", "unit", "qty", "quantity"],
    "charge": ["charge", "charges", "billed", "billedamount", "chargeamount", "amountbilled", "fee"],
    "allowed": ["allowed", "allowedamount", "allowable", "expected"],
    "paid": ["paid", "payment", "insurancepaid", "payerpaid", "paidamount", "inspaid", "insurancepayment"],
    "patient_resp": ["patientresp", "patientresponsibility", "patientbalance", "ptresp", "copaycoins"],
    "adjustment": ["adjustment", "writeoff", "contractualadjustment", "adjustmentamount"],
    "carc": ["carc", "denialcode", "reasoncode", "adjustmentreason", "denialreason", "adjcode", "remitcode"],
    "status": ["status", "claimstatus", "linestatus"],
    "provider": ["provider", "renderingprovider", "rendering", "doctor", "physician", "providername"],
    "paid_date": ["paiddate", "paymentdate", "checkdate", "remitdate", "postdate", "depositdate"],
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def parse_csv(text: str, source: str = "") -> Tuple[List[Claim], List[Remit]]:
    """A claims or payments export: one row per service line. Rows with the same claim ID become one claim."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return [], []
    head = [_norm(h) for h in rows[0]]
    col = {}
    for key, names in CSV_COLS.items():
        for n in names:
            if n in head:
                col[key] = head.index(n)
                break
    if "code" not in col or "charge" not in col:
        raise ValueError("couldn't find a procedure code column and a charge column")
    has_pay = "paid" in col
    get = lambda r, k: (r[col[k]].strip() if k in col and col[k] < len(r) else "")
    claims: Dict[str, Claim] = {}
    remits: Dict[str, Remit] = {}
    for n, r in enumerate(rows[1:], start=2):
        if not any(v.strip() for v in r):
            continue
        cid = get(r, "claim_id") or f"row{n}"
        dos = _any_date(get(r, "dos"))
        payer = get(r, "payer")
        ptype = _payer_type("", payer + " " + get(r, "payer_type"))
        mods = [m for m in re.split(r"[\s,;:/]+", " ".join(get(r, k) for k in ("mods", "mod2", "mod3")).upper()) if m]
        ln = Line(code=get(r, "code").upper().split(" ")[0], mods=mods, units=_f(get(r, "units")) or 1.0,
                  charge=_f(get(r, "charge")), dos=dos)
        c = claims.setdefault(cid, Claim(claim_id=cid, payer=payer, payer_type=ptype, dos=dos, provider=get(r, "provider"), source=source))
        c.lines.append(ln)
        c.charge += ln.charge
        if has_pay:
            rl = Line(code=ln.code, mods=list(mods), units=ln.units, charge=ln.charge, paid=_f(get(r, "paid")), dos=dos)
            if "allowed" in col and get(r, "allowed"):
                rl.allowed = _f(get(r, "allowed"))
            carcs = [x for x in re.split(r"[\s,;/]+", get(r, "carc").upper()) if x]
            adj_amt = _f(get(r, "adjustment"))
            if not adj_amt and carcs and rl.paid <= 0:
                adj_amt = rl.charge              # a denial code on an unpaid line: the whole charge was denied
            for code in carcs:
                m = re.match(r"^(CO|PR|OA|PI|CR)-?(\w+)$", code)
                grp, carc = (m.group(1), m.group(2)) if m else ("CO", code)
                rl.adjustments.append(Adj(grp, carc, adj_amt if len(carcs) == 1 else 0.0))
            pr = _f(get(r, "patient_resp"))
            if pr:
                rl.adjustments.append(Adj("PR", "2", pr))
            rm = remits.setdefault(cid, Remit(claim_id=cid, payer=payer, payer_type=ptype, dos=dos, provider=get(r, "provider"),
                                               date=_any_date(get(r, "paid_date")), source=source))
            rm.lines.append(rl)
            rm.charge += rl.charge
            rm.paid += rl.paid
            rm.patient_resp += pr
    for rm in remits.values():
        if rm.paid <= 0 and any(a.group != "PR" for l in rm.lines for a in l.adjustments):
            rm.status = "4"
        else:
            rm.status = "1"
    # a payments export with no payment and no adjustment on a row is a claim that hasn't been answered yet
    answered = {k: v for k, v in remits.items() if v.paid > 0 or any(l.adjustments for l in v.lines) or v.date}
    return list(claims.values()), list(answered.values())


def _any_date(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s.split(" ")[0] if fmt != "%b %d, %Y" else s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 200 * 1024 * 1024


@dataclass
class Dataset:
    claims: List[Claim] = field(default_factory=list)
    remits: List[Remit] = field(default_factory=list)
    files: List[dict] = field(default_factory=list)     # name, kind, count, error

    def add(self, name: str, data: bytes, _depth: int = 0):
        if name.lower().endswith(".zip") or data[:4] == b"PK\x03\x04":
            if _depth > 1:
                self.files.append({"name": name, "kind": "zip", "count": 0, "error": "zip inside a zip inside a zip: skipped"})
                return
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                self.files.append({"name": name, "kind": "zip", "count": 0, "error": "not a readable zip"})
                return
            total = 0
            for info in z.infolist():
                if info.is_dir() or info.filename.startswith("__MACOSX"):
                    continue
                if info.file_size > MAX_FILE or total + info.file_size > MAX_TOTAL:
                    self.files.append({"name": f"{name}/{info.filename}", "kind": "?", "count": 0, "error": "too large: skipped"})
                    continue
                total += info.file_size
                self.add(f"{name}/{info.filename}", z.read(info), _depth + 1)
            return
        if len(data) > MAX_FILE:
            self.files.append({"name": name, "kind": "?", "count": 0, "error": "larger than 25 MB: split it and upload the parts"})
            return
        text = _decode(data)
        try:
            if "ISA" in text[:2048]:
                found = 0
                kinds = set()
                for ix in interchanges(text):
                    term = ix[105] if len(ix) > 105 else "~"
                    st = re.search(r"(?:" + re.escape(term) + r"|\n)\s*ST" + re.escape(ix[3] if len(ix) > 3 else "*") + r"(\d{3})", ix[:5000])
                    kind = st.group(1) if st else ""
                    if kind == "835":
                        r = parse_835(ix, name)
                        self.remits += r
                        found += len(r)
                        kinds.add("835 payments")
                    elif kind == "837":
                        c = parse_837p(ix, name)
                        self.claims += c
                        found += len(c)
                        kinds.add("837 claims")
                    else:
                        kinds.add(f"X12 {kind or '?'} (not used)")
                self.files.append({"name": name, "kind": ", ".join(sorted(kinds)), "count": found,
                                   "error": "" if found else "no claims or payments found in it"})
            elif "," in text[:2048] or "\t" in text[:2048]:
                c, r = parse_csv(text, name)
                self.claims += c
                self.remits += r
                self.files.append({"name": name, "kind": "CSV export", "count": len(c), "error": ""})
            else:
                self.files.append({"name": name, "kind": "?", "count": 0, "error": "not an 835, 837 or CSV file"})
        except (ValueError, IndexError) as e:
            self.files.append({"name": name, "kind": "?", "count": 0, "error": f"couldn't read it: {e}"})


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Analysis
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _allowed(l: Line) -> Optional[float]:
    if l.allowed is not None:
        return l.allowed
    if l.paid <= 0 and not any(a.group == "PR" for a in l.adjustments):
        return None
    return round(l.paid + sum(a.amount for a in l.adjustments if a.group == "PR" or a.carc == "253"), 2)


def _price_key(l: Line) -> Tuple[str, Tuple[str, ...]]:
    return l.code, tuple(sorted(m for m in l.mods if m in PRICE_MODS))


def _denied(l: Line) -> bool:
    if l.paid > 0 or l.charge <= 0:
        return False
    non_pr = sum(a.amount for a in l.adjustments if a.group != "PR")
    pr = sum(a.amount for a in l.adjustments if a.group == "PR")
    return non_pr > 0 and pr < l.charge * 0.5


def _denial_carc(adjs: List[Adj]) -> str:
    pick = [a for a in adjs if a.group != "PR" and a.carc not in ("45", "253")]
    if not pick:
        pick = [a for a in adjs if a.group != "PR"]
    return max(pick, key=lambda a: a.amount).carc if pick else ""


def _days(a: str, b: str) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except ValueError:
        return None


class Analysis:
    def __init__(self, ds: Dataset, today: Optional[dt.date] = None):
        self.ds = ds
        self.today = today or dt.date.today()
        self.findings: List[Finding] = []
        # the latest non-reversed answer for each claim and payer
        latest: Dict[Tuple[str, str], Remit] = {}
        for r in sorted(ds.remits, key=lambda r: (r.date or "", 0 if r.reversed else 1)):
            k = (r.claim_id, (r.payer or "").upper())
            if r.reversed:
                latest.pop(k, None)
            else:
                latest[k] = r
        self.remits = list(latest.values())
        self.remits_by_claim: Dict[str, List[Remit]] = defaultdict(list)
        for r in self.remits:
            self.remits_by_claim[r.claim_id].append(r)
        self.claims = {c.claim_id: c for c in ds.claims}
        dates = [d for d in [c.dos for c in ds.claims] + [r.dos for r in self.remits] + [l.dos for r in self.remits for l in r.lines] if d]
        self.start, self.end = (min(dates), max(dates)) if dates else ("", "")
        span = (_days(self.start, self.end) or 0) + 1 if dates else 0
        self.span_days = max(span, 30) if dates else 0
        self._rate_cache: Dict[tuple, Optional[Tuple[float, str]]] = {}
        self._ratio: Optional[float] = None
        self.rates = self._rates()
        self.payer_paid_codes = self._payer_paid_codes()

    # ── expected amounts, from the clinic's own payments ──
    def _rates(self):
        per_payer: Dict[Tuple[str, str, tuple], List[Tuple[str, float]]] = defaultdict(list)
        any_payer: Dict[Tuple[str, tuple], List[float]] = defaultdict(list)
        for r in self.remits:
            for l in r.lines:
                a = _allowed(l)
                if a is None or a <= 0 or _denied(l):
                    continue
                if any(x.carc in ("59",) for x in l.adjustments):
                    continue
                per_unit = round(a / (l.units or 1), 2)
                code, mods = _price_key(l)
                per_payer[((r.payer or "").upper(), code, mods)].append((l.dos or r.date or "", per_unit))
                any_payer[(code, mods)].append(per_unit)
        return {"payer": per_payer, "any": any_payer}

    def expected(self, payer: str, l: Line) -> Tuple[float, str]:
        """What this line should bring in (allowed, before patient share), and how we know."""
        code, mods = _price_key(l)
        key = ((payer or "").upper(), code, mods)
        if key not in self._rate_cache:
            seen = [v for _, v in self.rates["payer"].get(key, [])]
            if seen:
                self._rate_cache[key] = (_mode(seen), "this payer's usual rate for you")
            else:
                seen = self.rates["any"].get((code, mods), [])
                self._rate_cache[key] = (statistics.median(seen), "your other payers' median rate") if seen else None
        hit = self._rate_cache[key]
        if hit:
            return round(hit[0] * (l.units or 1), 2), hit[1]
        ratio = self.collection_ratio()
        return round(l.charge * ratio, 2), f"your charge x your average allowed-to-charge ratio ({ratio:.0%})"

    def collection_ratio(self) -> float:
        if self._ratio is None:
            self._ratio = self._collection_ratio()
        return self._ratio

    def _collection_ratio(self) -> float:
        tot_c = tot_a = 0.0
        for r in self.remits:
            for l in r.lines:
                a = _allowed(l)
                if a and l.charge > 0 and not _denied(l):
                    tot_c += l.charge
                    tot_a += a
        return min(max(tot_a / tot_c, 0.2), 1.0) if tot_c else 0.5

    def _payer_paid_codes(self):
        out: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        for r in self.remits:
            for l in r.lines:
                if l.paid > 0:
                    out[(r.payer or "").upper()][l.code].append(_allowed(l) or l.paid)
        return out

    def _visits(self):
        """Every visit we know about: the claim as sent if we have it, otherwise the lines on the payment file."""
        seen = set()
        for c in self.claims.values():
            seen.add(c.claim_id)
            yield c.claim_id, c.payer, c.payer_type, c.dos, c.provider, c.lines
        for r in self.remits:
            if r.claim_id in seen:
                continue
            seen.add(r.claim_id)
            yield r.claim_id, r.payer, r.payer_type, r.dos or (r.lines[0].dos if r.lines else ""), r.provider, r.lines

    def add(self, **kw):
        self.findings.append(Finding(**kw))

    # ── 1. denials ──
    def denials(self):
        for r in self.remits:
            items = []
            if r.lines:
                for l in r.lines:
                    if _denied(l):
                        items.append((l, _denial_carc(l.adjustments + r.adjustments)))
            elif r.status == "4" or (r.paid <= 0 and r.charge > 0 and any(a.group != "PR" for a in r.adjustments)):
                items.append((Line(code="(whole claim)", charge=r.charge, adjustments=r.adjustments), _denial_carc(r.adjustments)))
            if not items:
                continue
            # paid by another payer (e.g. secondary) after this denial? then it isn't lost
            others = [o for o in self.remits_by_claim.get(r.claim_id, []) if o is not r and o.paid > 0]
            window = APPEAL_DAYS.get(r.payer_type, 180)
            base = r.date or r.dos
            deadline = ""
            left = None
            if base:
                try:
                    d = dt.date.fromisoformat(base) + dt.timedelta(days=window)
                    deadline, left = d.isoformat(), (d - self.today).days
                except ValueError:
                    pass
            for l, carc in items:
                if others and any(ol.code == l.code and ol.paid > 0 for o in others for ol in o.lines):
                    continue
                kind = CARC_KIND.get(carc, "review")
                action, odds = PLAYBOOK[kind]
                value, how = self.expected(r.payer, l) if l.code != "(whole claim)" else (round(r.charge * self.collection_ratio(), 2), "your average allowed-to-charge ratio")
                reason = f"{_group_of(l.adjustments + r.adjustments, carc)}-{carc}: {CARC.get(carc, 'see the payer remark')}" if carc else "denied, no reason code"
                remarks = f" Remarks: {', '.join(l.remarks)}." if l.remarks else ""
                lost = left is not None and left < 0
                if kind == "timely" or lost:
                    self.add(category="lost", title=f"{l.code} denied ({reason})" + (" — past the appeal window" if lost else ""),
                             dollars=value, confidence="high", action="Prevention: " + ("file within the payer's limit; keep clearinghouse acceptance reports" if kind == "timely" else "work denials within days of the payment file"),
                             why=f"{reason}.{remarks} Worth about ${value:,.2f} ({how}).", claim_id=r.claim_id, payer_claim_id=r.payer_claim_id,
                             payer=r.payer, dos=l.dos or r.dos, code=l.code, deadline=deadline, days_left=left, counted=False)
                    continue
                conf = {"high": "high", "medium": "medium", "low": "low"}[odds]
                self.add(category="denial", title=f"{l.code} denied — {CARC.get(carc, 'reason ' + carc) if carc else 'no reason code'}",
                         dollars=value, confidence=conf, action=action,
                         why=f"{reason}.{remarks} Expected about ${value:,.2f} ({how}). Appeal window assumed {window} days for a {r.payer_type.replace('_', ' ')} payer: check the contract.",
                         claim_id=r.claim_id, payer_claim_id=r.payer_claim_id, payer=r.payer, dos=l.dos or r.dos, code=l.code,
                         deadline=deadline, days_left=left, counted=conf != "low")

    # ── 2. underpayments, against the payer's own usual rate ──
    def underpayments(self):
        settled: Dict[tuple, Tuple[float, int]] = {}
        by_year = defaultdict(list)
        for (payer, code, mods), vals in self.rates["payer"].items():
            for d, v in vals:
                by_year[(payer, code, mods, (d or "")[:4])].append(v)
        for r in self.remits:
            for l in r.lines:
                a = _allowed(l)
                if a is None or a <= 0 or _denied(l):
                    continue
                if any(x.carc not in NORMAL_REDUCTIONS and x.group != "PR" and x.amount > 0 for x in l.adjustments):
                    reasons = [x.carc for x in l.adjustments if x.carc not in NORMAL_REDUCTIONS and x.group != "PR" and x.amount > 0]
                else:
                    reasons = []
                if any(x.carc == "59" for x in l.adjustments):
                    continue
                code, mods = _price_key(l)
                k = ((r.payer or "").upper(), code, mods, (l.dos or r.date or "")[:4])
                vals = by_year.get(k) or []
                if len(vals) < 3:
                    continue
                if k not in settled:
                    m = _mode(vals)
                    settled[k] = (m, Counter(round(v, 2) for v in vals)[m])
                rate, n_rate = settled[k]
                if n_rate < 2:      # no settled rate: prices vary too much to call one line short
                    continue
                per_unit = a / (l.units or 1)
                short = round((rate - per_unit) * (l.units or 1), 2)
                if per_unit < rate * 0.98 - 0.25 and short >= 1.0:
                    extra = f" The payer also took {', '.join(CARC.get(c, c) for c in reasons)}." if reasons else ""
                    self.add(category="underpaid", title=f"{l.code} paid below {r.payer}'s usual rate",
                             dollars=short, confidence="high" if n_rate >= 3 else "medium",
                             action="Ask the payer to reprocess at the contracted rate (call or reconsideration form); attach a paid example at the usual rate",
                             why=f"Allowed ${a:,.2f} for {l.units:g} unit(s); {r.payer} allowed ${rate:,.2f} per unit on {n_rate} of {len(vals)} other {l.code} lines that year.{extra}",
                             claim_id=r.claim_id, payer_claim_id=r.payer_claim_id, payer=r.payer, dos=l.dos or r.dos, code=l.code)
        # rate drops: a payer's usual rate for a code fell during the period
        for (payer, code, mods), vals in self.rates["payer"].items():
            vals = sorted(v for v in vals if v[0])
            if len(vals) < 8:
                continue
            half = len(vals) // 2
            early, late = _mode([v for _, v in vals[:half]]), _mode([v for _, v in vals[half:]])
            if late < early * 0.97 and early - late >= 1:
                n_late = len(vals) - half
                self.add(category="rate_drop", title=f"{payer.title()} cut what it pays you for {code}",
                         dollars=round((early - late) * n_late, 2), confidence="low",
                         action="Check whether you agreed to the new rate; if not, dispute it with the payer's provider relations",
                         why=f"Usual rate went from ${early:,.2f} to ${late:,.2f} per unit around {vals[half][0]} ({n_late} lines since).",
                         payer=payer.title(), code=code, counted=False)

    # ── 3. prices below what payers pay ──
    def low_prices(self):
        """A line paid at 100% of the charge, with no contractual reduction, was capped by the clinic's own price: payers
        pay the lesser of the charge or their rate. What the payer would have paid comes from (a) amounts any payer
        allowed above that price, or (b) the payer's rate level against another payer, applied to that payer's rate."""
        capped: Dict[Tuple[str, str], list] = defaultdict(list)
        uncapped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        allowed_by_code: Dict[str, List[float]] = defaultdict(list)
        for r in self.remits:
            p = (r.payer or "").upper()
            for l in r.lines:
                a = _allowed(l)
                if a is None or a <= 0 or l.charge <= 0 or _denied(l):
                    continue
                pu_c, pu_a = l.charge / (l.units or 1), a / (l.units or 1)
                allowed_by_code[l.code].append(pu_a)
                if pu_a >= pu_c - 0.01 and not any(x.carc == "45" and x.amount > 0.01 for x in l.adjustments):
                    capped[(p, l.code)].append((r, l, pu_c))
                elif any(x.carc == "45" and x.amount > 0.01 for x in l.adjustments):
                    uncapped[(p, l.code)].append(pu_a)
        rate = {k: _mode(v) for k, v in uncapped.items() if v}
        payers = {p for p, _ in rate}

        def index(p: str, q: str) -> Optional[float]:
            shared = [c for (pp, c) in rate if pp == p and (q, c) in rate]
            if len(shared) < 3:
                return None
            return statistics.median(rate[(p, c)] / rate[(q, c)] for c in shared)

        quiet = set()
        for (p, code), items in sorted(capped.items()):
            charge = statistics.median(c for _, _, c in items)
            units = sum(l.units or 1 for _, l, _ in items)
            higher = sorted(a for a in allowed_by_code[code] if a > charge + 0.5)
            est, how, conf = None, "", "low"
            if higher:
                est, how, conf = statistics.median(higher), f"other payments for {code} were allowed up to ${max(higher):,.2f}", "medium"
            else:
                guesses = []
                for q in payers - {p}:
                    if (q, code) in rate:
                        ix = index(p, q)
                        if ix:
                            guesses.append((ix * rate[(q, code)], q, ix))
                if guesses:
                    g = statistics.median(x for x, _, _ in guesses)
                    if g > charge + 0.5:
                        q0 = guesses[0]
                        est, how = g, f"{p.title()} pays about {q0[2]:.2f}x what {q0[1].title()} pays on codes they share, and {q0[1].title()} allows ${rate[(q0[1], code)]:,.2f} for {code}"
            if est:
                self.add(category="low_price", title=f"{p.title()} paid your full price for {code} (${charge:,.2f}): it's below their rate",
                         dollars=round((est - charge) * units, 2), confidence=conf, counted=conf != "low",
                         action=f"Raise your charge for {code} to at least ${est * 1.1:,.2f}; payers pay the lesser of your charge or their rate",
                         why=f"{len(items)} line(s) paid at 100% of your charge with no contractual reduction; {how}. Estimated {p.title()} rate ${est:,.2f}.",
                         payer=p.title(), code=code)
            elif len(items) >= 3 and code not in quiet:
                quiet.add(code)
                self.add(category="low_price", title=f"Payers pay your full price for {code}: it may be set too low",
                         dollars=0.0, confidence="low", counted=False,
                         action=f"Compare your {code} charge (${charge:,.2f}) with your payers' fee schedules and set it at or above the highest",
                         why=f"{len(items)} line(s) paid at 100% of your charge with no contractual reduction. That usually means your price is below the payer's rate.",
                         payer=p.title(), code=code)

    # ── 4. missed charges ──
    def missed_charges(self):
        for cid, payer, ptype, dos, prov, lines in self._visits():
            codes = {l.code for l in lines}
            has_em = bool(codes & EM_ALL)
            pkey = (payer or "").upper()
            paid_here = self.payer_paid_codes.get(pkey, {})
            # add-on codes this payer pays the clinic for, missing on this visit
            if has_em:
                for addon, what in ADDONS.items():
                    if addon in codes or len(paid_here.get(addon, [])) < 2:
                        continue
                    if addon == "99051":
                        try:
                            if dt.date.fromisoformat(dos).weekday() < 5:
                                continue
                        except ValueError:
                            continue
                    val = round(_mode([round(v, 2) for v in paid_here[addon]]), 2)
                    self.add(category="missed", title=f"{addon} missing ({what})", dollars=val, confidence="medium",
                             action=f"Add {addon} on a corrected claim if the visit qualified, and add it to your charge template",
                             why=f"{payer} paid you for {addon} on {len(paid_here[addon])} other visits (usually ${val:,.2f}); this {'weekend ' if addon == '99051' else ''}visit has none.",
                             claim_id=cid, payer=payer, dos=dos, code=addon)
            drugs = {c for c in codes if c.startswith("J") and not INHALATION_DRUG.match(c) and not c.startswith("J8")}
            inhaled = {c for c in codes if INHALATION_DRUG.match(c)}
            if codes & DRUG_ADMIN and not drugs and not any(c.startswith(("Q", "90")) and len(c) == 5 for c in codes if c not in DRUG_ADMIN):
                self.add(category="missed", title="Injection given, no drug billed", dollars=self._typical("J", payer, 12.0), confidence="medium",
                         action="Add the drug's J-code and units from the note (e.g. J1885 ketorolac, J0696 ceftriaxone, J1100 dexamethasone)",
                         why=f"{', '.join(sorted(codes & DRUG_ADMIN))} is on the claim but no drug code. Unless the drug was free or patient-supplied, it's billable.",
                         claim_id=cid, payer=payer, dos=dos, code=",".join(sorted(codes & DRUG_ADMIN)))
            if drugs and not codes & DRUG_ADMIN:
                self.add(category="missed", title="Drug billed, no injection fee", dollars=self.expected(payer, Line(code="96372", charge=40.0))[0], confidence="medium",
                         action="Add 96372 (or the IV push/infusion code) if the clinic gave the injection",
                         why=f"{', '.join(sorted(drugs))} is billed with no administration code.", claim_id=cid, payer=payer, dos=dos, code="96372")
            if inhaled and not codes & NEB_ADMIN:
                self.add(category="missed", title="Nebulizer medicine billed, no treatment code", dollars=self.expected(payer, Line(code="94640", charge=60.0))[0],
                         confidence="medium", action="Add 94640 for the nebulizer treatment (76 modifier for a repeat)",
                         why=f"{', '.join(sorted(inhaled))} billed without 94640.", claim_id=cid, payer=payer, dos=dos, code="94640")
            if codes & NEB_ADMIN and not inhaled:
                self.add(category="missed", title="Nebulizer treatment billed, no medicine", dollars=self._typical("J76", payer, 5.0), confidence="low",
                         action="Add the inhalation drug (e.g. J7613 albuterol, J7620 albuterol-ipratropium) with units", counted=False,
                         why="94640 is on the claim with no J76xx medicine.", claim_id=cid, payer=payer, dos=dos, code="J7613")
            if codes & BLOOD_LABS and not codes & VENIPUNCTURE:
                self.add(category="missed", title="Blood test billed, no blood draw", dollars=self.expected(payer, Line(code="36415", charge=15.0))[0],
                         confidence="medium", action="Add 36415 (venipuncture) when the clinic drew the blood",
                         why=f"{', '.join(sorted(codes & BLOOD_LABS))} billed without 36415.", claim_id=cid, payer=payer, dos=dos, code="36415")

    def _typical(self, prefix: str, payer: str, fallback: float) -> float:
        vals = [v for code, vs in self.payer_paid_codes.get((payer or "").upper(), {}).items() if code.startswith(prefix) for v in vs]
        if not vals:
            vals = [v for p in self.payer_paid_codes.values() for code, vs in p.items() if code.startswith(prefix) for v in vs]
        return round(statistics.median(vals), 2) if vals else fallback

    # ── 5. claims with no answer ──
    def no_response(self):
        if not self.claims or not self.remits:
            return
        last_remit = max((r.date for r in self.remits if r.date), default="")
        if not last_remit:
            return
        first_remit = min((r.date for r in self.remits if r.date), default="")
        for c in self.claims.values():
            if c.claim_id in self.remits_by_claim or not c.dos:
                continue
            age = _days(c.dos, last_remit)
            if age is None or age < 45 or (first_remit and c.dos < (dt.date.fromisoformat(first_remit) - dt.timedelta(days=60)).isoformat()):
                continue        # too new to chase, or older than the payment files cover
            value = sum(self.expected(c.payer, l)[0] for l in c.lines)
            limit = 365 if c.payer_type == "medicare" else 90 if c.payer_type == "medicaid" else 180
            since = _days(c.dos, self.today.isoformat()) or 0
            self.add(category="no_response", title=f"No answer from {c.payer or 'the payer'} after {age} days",
                     dollars=round(value, 2), confidence="medium",
                     action="Check claim status (payer portal or 276); if the payer never got it, resubmit now" + (f" — the usual {limit}-day filing limit is close" if since > limit - 30 else ""),
                     why=f"Sent for {c.dos}; no payment or denial in the payment files, which run to {last_remit}. It may be in a file that wasn't uploaded.",
                     claim_id=c.claim_id, payer=c.payer, dos=c.dos, code=", ".join(l.code for l in c.lines[:4]))

    # ── 6. coding risks ──
    def coding_risks(self):
        denied_lines = {(r.claim_id, l.code) for r in self.remits for l in r.lines if _denied(l)}
        for cid, payer, ptype, dos, prov, lines in self._visits():
            codes = {l.code for l in lines}
            for l in lines:
                if l.code in DELETED:
                    self.add(category="coding", title=f"{l.code} is not a valid code", dollars=0.0, confidence="high", counted=False,
                             action=f"Rebill with a current code: {DELETED[l.code]}", why="Payers deny deleted codes outright.",
                             claim_id=cid, payer=payer, dos=dos, code=l.code)
                if l.code in CONSULTS and ptype in ("medicare", "medicare_advantage"):
                    self.add(category="coding", title=f"Consult code {l.code} sent to Medicare", dollars=0.0, confidence="high", counted=False,
                             action="Bill the office visit level instead (99202-99215); Medicare stopped paying consult codes in 2010",
                             why="Medicare and most Medicare Advantage plans deny consult codes.", claim_id=cid, payer=payer, dos=dos, code=l.code)
                if l.code in QW_TESTS and "QW" not in l.mods and ptype in ("medicare", "medicare_advantage", "medicaid"):
                    self.add(category="coding", title=f"{l.code} ({QW_TESTS[l.code]}) without QW", dollars=0.0, confidence="medium", counted=False,
                             action="Add modifier QW for a CLIA-waived test; check your CLIA certificate number is on the claim",
                             why=f"{ptype.replace('_', ' ').title()} wants QW on waived tests." + (" This line was denied." if (cid, l.code) in denied_lines else ""),
                             claim_id=cid, payer=payer, dos=dos, code=l.code)
            em = [l for l in lines if l.code in EM_ALL]
            procs = sorted(c for c in codes if _proc_needs_25(c))
            if em and procs and not any("25" in l.mods for l in em):
                self.add(category="coding", title="Visit + procedure without modifier 25", dollars=0.0, confidence="medium", counted=False,
                         action="Put modifier 25 on the visit code when the visit was a separate, significant service (and the note says so)",
                         why=f"{em[0].code} billed with {', '.join(procs[:4])}. Without 25 most payers fold the visit into the procedure."
                             + (" The visit was denied." if (cid, em[0].code) in denied_lines else ""),
                         claim_id=cid, payer=payer, dos=dos, code=em[0].code)

    # ── 7. visit levels ──
    def visit_levels(self):
        counts = Counter()
        by_prov: Dict[str, Counter] = defaultdict(Counter)
        for cid, payer, ptype, dos, prov, lines in self._visits():
            for l in lines:
                if l.code in EM_ALL and l.code not in ("99211",):
                    counts[l.code] += 1
                    by_prov[prov or "unknown"][l.code] += 1
                    break
        total = sum(counts.values())
        self.levels = {"counts": dict(counts), "total": total, "by_provider": {p: dict(c) for p, c in by_prov.items()}}
        if total < 20:
            return
        l3 = counts["99213"] + counts["99203"]
        l5 = counts["99215"] + counts["99205"]
        share3, share5 = l3 / total, l5 / total
        self.levels.update(share3=share3, share5=share5)
        diff = self._level_gap()
        if share3 > LEVEL3_BENCHMARK:
            excess = round(l3 - LEVEL3_BENCHMARK * total)
            self.add(category="levels", title=f"{share3:.0%} of visits coded level 3 (urgent care benchmark: under {LEVEL3_BENCHMARK:.0%})",
                     dollars=round(excess * diff, 2), confidence="low", counted=False,
                     action="Have a certified coder review a sample of level 3 notes against the 2021 office visit rules (medical decision making or total time); Althais can pre-screen every note",
                     why=f"{excess} visits above the benchmark share. If those notes support level 4, each is worth about ${diff:,.2f} more ({'from your own 99213 vs 99214 payments' if diff != DEFAULT_L3_TO_L4 else 'a typical difference; your files had too few paid 99213/99214 lines to measure yours'}). A pattern, not proof: every recode needs the note to support it.",
                     code="99213/99203")
        if share5 > LEVEL5_AUDIT_RISK:
            self.add(category="levels", title=f"{share5:.0%} of visits coded level 5: audit risk", dollars=0.0, confidence="medium", counted=False,
                     action="Check that level 5 notes document high-complexity decision making or 40+/54+ minutes; payers audit outliers",
                     why="Level 5 is rare in urgent care. A high share draws payer reviews and recoupments.", code="99215/99205")
        if len(by_prov) > 1:
            for prov, c in by_prov.items():
                n = sum(c.values())
                if n < 15:
                    continue
                p3 = (c["99213"] + c["99203"]) / n
                if p3 - share3 >= 0.10:
                    excess = round((p3 - share3) * n)
                    self.add(category="levels", title=f"Provider {prov}: {p3:.0%} level 3 vs the clinic's {share3:.0%}",
                             dollars=round(excess * diff, 2), confidence="low", counted=False,
                             action="Review this provider's level 3 notes with them; most undercoding is one or two habits",
                             why=f"{n} visits; about {excess} more level 3s than the clinic average.", code="99213/99203")

    def _level_gap(self) -> float:
        a3 = self.rates["any"].get(("99213", ()), [])
        a4 = self.rates["any"].get(("99214", ()), [])
        if len(a3) >= 3 and len(a4) >= 3:
            d = statistics.median(a4) - statistics.median(a3)
            if d > 0:
                return round(d, 2)
        return DEFAULT_L3_TO_L4

    # ── 8. rules the payers taught us ──
    def payer_rules(self):
        rules = Counter()
        detail = {}
        for r in self.remits:
            for l in r.lines:
                if not _denied(l):
                    continue
                carc = _denial_carc(l.adjustments)
                key = ((r.payer or "?").title(), l.code, carc, "+".join(sorted(l.mods)) or "no modifier")
                rules[key] += 1
                detail[key] = CARC.get(carc, carc)
        self.rules = [{"payer": p, "code": c, "carc": k, "mods": m, "count": n, "reason": detail[(p, c, k, m)],
                       "check": _rule_text(p, c, k, m)} for (p, c, k, m), n in rules.most_common(25) if n >= 2]

    def patient_balances(self):
        self.patient_owed = round(sum(r.patient_resp for r in self.remits), 2)

    def run(self) -> "Analysis":
        self.levels = {"counts": {}, "total": 0, "by_provider": {}}
        self.rules = []
        for step in (self.denials, self.underpayments, self.low_prices, self.missed_charges, self.no_response,
                     self.coding_risks, self.visit_levels, self.payer_rules, self.patient_balances):
            step()
        self.findings.sort(key=lambda f: (-f.dollars, f.category))
        return self

    # ── totals ──
    def summary(self) -> dict:
        found = sum(f.dollars for f in self.findings if f.counted and f.confidence in ("high", "medium"))
        possible = sum(f.dollars for f in self.findings
                       if not (f.counted and f.confidence in ("high", "medium")) and f.category not in ("lost", "coding"))
        lost = sum(f.dollars for f in self.findings if f.category == "lost")
        charges = sum(c.charge or sum(l.charge for l in c.lines) for c in self.claims.values()) or sum(r.charge for r in self.remits)
        paid = sum(r.paid for r in self.remits)
        factor = 365 / self.span_days if self.span_days else 0
        cats = defaultdict(lambda: {"count": 0, "dollars": 0.0})
        for f in self.findings:
            cats[f.category]["count"] += 1
            cats[f.category]["dollars"] += f.dollars
        denied_lines = sum(1 for r in self.remits for l in r.lines if _denied(l))
        all_lines = sum(len(r.lines) for r in self.remits)
        return {
            "period": {"start": self.start, "end": self.end, "days": self.span_days},
            "claims": len(self.claims), "payments": len(self.remits),
            "visits": len({cid for cid, *_ in self._visits()}),
            "charges": round(charges, 2), "paid": round(paid, 2),
            "found": round(found, 2), "found_annual": round(found * factor, 2),
            "possible": round(possible, 2), "possible_annual": round(possible * factor, 2),
            "lost": round(lost, 2), "lost_annual": round(lost * factor, 2),
            "patient_owed": self.patient_owed,
            "denial_rate": round(denied_lines / all_lines, 4) if all_lines else None,
            "categories": {k: {"count": v["count"], "dollars": round(v["dollars"], 2)} for k, v in cats.items()},
            "levels": self.levels, "rules": self.rules, "files": self.ds.files,
        }

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "findings": [asdict(f) for f in self.findings]}


def _group_of(adjs: List[Adj], carc: str) -> str:
    return next((a.group for a in adjs if a.carc == carc), "CO")


def _rule_text(payer: str, code: str, carc: str, mods: str) -> str:
    if carc in ("97", "234", "236", "B15"):
        return f"Before sending {code} to {payer}: if it's with a visit or another procedure, check modifier 25 on the visit or 59/XU on the procedure."
    if carc == "4":
        return f"Before sending {code} to {payer}: check the modifier ({mods} was denied)."
    if carc in ("11", "167", "50"):
        return f"Before sending {code} to {payer}: check the diagnosis supports it; {payer} has denied it for medical necessity."
    if carc in ("31", "27", "26", "22", "109", "177", "200"):
        return f"Before any visit for {payer}: run eligibility the same day and confirm the member ID and primary payer."
    if carc in ("197", "15"):
        return f"Before {code} for {payer}: get the authorization number on the claim."
    if carc == "16":
        return f"Before sending {code} to {payer}: read the remark codes on past denials; a required field is missing."
    if carc == "18":
        return f"Before resending {code} to {payer}: use a corrected-claim frequency code (7), not a new claim."
    if carc in ("96", "204"):
        return f"Before {code} for a {payer} patient: confirm coverage or get a signed waiver so the patient can be billed."
    return f"Before sending {code} to {payer}: check for reason {carc} ({CARC.get(carc, 'see remark')})."


def _mode(vals: List[float]) -> float:
    c = Counter(round(v, 2) for v in vals)
    top = max(c.values())
    return max(v for v, n in c.items() if n == top)


def analyze(files: Iterable[Tuple[str, bytes]], today: Optional[dt.date] = None) -> Analysis:
    ds = Dataset()
    for name, data in files:
        ds.add(name, data)
    return Analysis(ds, today).run()


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  The report
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

SECTIONS = [
    ("denial", "Denials you can still fix or appeal", "Each one has the reason, the fix and the deadline. Work the soonest deadlines first."),
    ("underpaid", "Underpaid lines", "Paid below the rate the same payer usually pays you for the same code. No contract needed: the payer's own payments are the proof."),
    ("missed", "Charges that were never billed", "Services the claim shows were done, or add-ons your payers already pay you for, that are missing."),
    ("no_response", "Claims with no answer", "Sent, but no payment or denial came back. Check status before the filing limit passes."),
    ("low_price", "Prices set below what payers pay", "Payers pay the lesser of your charge or their rate. A low charge caps what you can be paid."),
    ("levels", "Visit levels", "How your visit levels compare with urgent care benchmarks. A pattern to review, not a list of errors."),
    ("rate_drop", "Rate changes", "A payer's usual rate for a code went down during the period."),
    ("coding", "Coding fixes that prevent denials", "Problems on claims that cost money when they're denied. Fix the template once."),
    ("lost", "Money lost for good", "Past the deadline or filed late. Not recoverable: this is what prevention is worth."),
]

STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--muted:#5b6472;--line:#e3e6eb;--accent:#0f6b5c;--accent-soft:#e3f2ef;
--warn:#a35a00;--warn-soft:#fff2df;--bad:#a12a2a;--bad-soft:#fde8e8;--chip:#eef0f3}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1216;--card:#171b21;--ink:#e8ebef;--muted:#9aa3af;
--line:#2a3039;--accent:#4fc3a8;--accent-soft:#16312b;--warn:#f0b35a;--warn-soft:#33270f;--bad:#f08080;--bad-soft:#3a1a1a;--chip:#222831}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:19px;margin:36px 0 4px}p.sub{color:var(--muted);margin:0 0 14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card .k{color:var(--muted);font-size:13px}.card .v{font-size:28px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:2px}
.card .s{color:var(--muted);font-size:13px}.card.hero{background:var(--accent-soft);border-color:transparent}.card.hero .v{color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--chip);font-weight:600;font-size:12.5px;color:var(--muted)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tr:last-child td{border-bottom:0}
.wrap{overflow-x:auto;border-radius:12px}.chip{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;background:var(--chip);white-space:nowrap}
.high{background:var(--accent-soft);color:var(--accent)}.medium{background:var(--warn-soft);color:var(--warn)}.low{background:var(--chip);color:var(--muted)}
.late{color:var(--bad);font-weight:600}.why{color:var(--muted);font-size:12.5px;margin-top:3px}
.bar{display:flex;align-items:center;gap:8px;margin:4px 0}.bar span.l{width:60px;color:var(--muted);font-size:13px}
.bar .t{flex:1;background:var(--chip);border-radius:6px;height:14px;overflow:hidden}.bar .f{background:var(--accent);height:100%}
.bar span.n{width:90px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px}
.note{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:var(--muted);font-size:13px;margin-top:28px}
button{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer}
.more{color:var(--muted);font-size:13px;margin-top:6px}
@media print{button{display:none}body{background:#fff}.card,table{break-inside:avoid}}
"""


def _m(v: float) -> str:
    if not v:
        return "$0"
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


def render_html(a: Analysis, clinic: str = "", sample: bool = False, rows_per_section: int = 40) -> str:
    s = a.summary()
    e = html.escape
    out = [f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Lost Money Report</title><style>{STYLE}</style></head><body><main>"]
    out.append(f"<h1>Lost Money Report{(' — ' + e(clinic)) if clinic else ''}</h1>")
    period = f"{s['period']['start']} to {s['period']['end']} ({s['period']['days']} days)" if s["period"]["start"] else "no dates found"
    out.append(f"<p class=sub>{s['visits']:,} visits · {s['payments']:,} payer answers · {e(period)} · made by Althais on {a.today.isoformat()}"
               + (" · <b>SAMPLE DATA, not a real clinic</b>" if sample else "") + "</p>")
    out.append("<div class=cards>"
               f"<div class='card hero'><div class=k>Money found</div><div class=v>{_m(s['found'])}</div><div class=s>about {_m(s['found_annual'])} a year at this pace</div></div>"
               f"<div class=card><div class=k>Possible, needs chart review</div><div class=v>{_m(s['possible'])}</div><div class=s>about {_m(s['possible_annual'])} a year</div></div>"
               f"<div class=card><div class=k>Lost for good</div><div class=v>{_m(s['lost'])}</div><div class=s>what prevention is worth</div></div>"
               f"<div class=card><div class=k>Denial rate (lines)</div><div class=v>{(s['denial_rate'] or 0):.1%}</div><div class=s>{_m(s['paid'])} paid on {_m(s['charges'])} billed</div></div>"
               "</div>")
    # where the money is
    rows = []
    for key, title, _ in SECTIONS:
        c = s["categories"].get(key)
        if c:
            rows.append(f"<tr><td>{e(title)}</td><td class=n>{c['count']:,}</td><td class=n>{_m(c['dollars'])}</td></tr>")
    if rows:
        out.append("<h2>Where the money is</h2><div class=wrap><table><tr><th>What</th><th class=n>Items</th><th class=n>Dollars</th></tr>" + "".join(rows) + "</table></div>")
    out.append("<p class=more><button onclick='dl()'>Download the worklist (CSV)</button> &nbsp; <button onclick='print()'>Print or save as PDF</button></p>")
    for key, title, blurb in SECTIONS:
        items = [f for f in a.findings if f.category == key]
        if not items:
            continue
        if key == "denial":
            items.sort(key=lambda f: (f.days_left if f.days_left is not None else 9999, -f.dollars))
        out.append(f"<h2>{e(title)}</h2><p class=sub>{e(blurb)}</p>")
        if key == "levels" and a.levels.get("total"):
            out.append(_levels_html(a))
        out.append("<div class=wrap><table><tr><th>Item</th><th>Claim</th><th>Payer</th><th>Date</th><th>What to do</th>"
                   + ("<th>Deadline</th>" if key == "denial" else "") + "<th class=n>Dollars</th></tr>")
        for f in items[:rows_per_section]:
            dl = ""
            if key == "denial":
                dl = (f"<td style='white-space:nowrap' class={'late' if (f.days_left or 99) < 15 else ''}>{e(f.deadline)}<br><span class=why>{f.days_left} days left</span></td>"
                      if f.deadline else "<td>check</td>")
            claim = e(f.claim_id) + (f"<br><span class=why>payer # {e(f.payer_claim_id)}</span>" if f.payer_claim_id else "")
            out.append(f"<tr><td><b>{e(f.title)}</b> <span class='chip {f.confidence}'>{f.confidence}</span><div class=why>{e(f.why)}</div></td>"
                       f"<td>{claim}</td><td>{e(f.payer)}</td><td style='white-space:nowrap'>{e(f.dos)}</td><td>{e(f.action)}</td>{dl}<td class=n>{_m(f.dollars) if f.dollars else '—'}</td></tr>")
        out.append("</table></div>")
        if len(items) > rows_per_section:
            out.append(f"<p class=more>{len(items) - rows_per_section} more in the CSV worklist.</p>")
    if a.rules:
        out.append("<h2>What your payers taught us</h2><p class=sub>Denial patterns repeated in your files, written as checks to run before the next claim goes out. Althais runs these automatically on every claim.</p>")
        out.append("<div class=wrap><table><tr><th>Payer</th><th>Code</th><th>Reason</th><th class=n>Times</th><th>Check before sending</th></tr>")
        for r in a.rules:
            out.append(f"<tr><td>{e(r['payer'])}</td><td>{e(r['code'])} <span class=why>{e(r['mods'])}</span></td><td>{e(r['carc'])}: {e(r['reason'])}</td>"
                       f"<td class=n>{r['count']}</td><td>{e(r['check'])}</td></tr>")
        out.append("</table></div>")
    if s["patient_owed"]:
        out.append(f"<h2>Patient balances</h2><p class=sub>Payers assigned {_m(s['patient_owed'])} to patients (copays, coinsurance, deductibles) in these files. "
                   "Not lost, but only collected if someone asks: collecting at the front desk on the day of the visit is the cheapest way.</p>")
    fl = s["files"]
    shown = fl if len(fl) <= 12 else [f for f in fl if f["error"]][:40]
    files = "".join(f"<li>{e(f['name'])}: {e(f['kind'] or '')} {('— ' + str(f['count']) + ' read') if f['count'] else ''} {('— ' + e(f['error'])) if f['error'] else ''}</li>" for f in shown)
    if len(fl) > 12:
        kinds = Counter(f["kind"] or "unreadable" for f in fl)
        files = "".join(f"<li>{n} file(s): {e(k)}</li>" for k, n in kinds.most_common()) + (("<li>Problems:</li>" + files) if files else "")
    out.append("<div class=note><b>How this was worked out.</b> Expected amounts come from what each payer has paid this clinic for the same code, "
               "so no contracts are needed; where a payer has no history for a code, the median of the clinic's other payers is used. "
               "<b>Money found</b> counts high- and medium-confidence items. <b>Possible</b> needs a person to confirm (for visit levels, the note has to support the higher level: "
               "never recode without it). Appeal windows are typical floors (Medicare 120 days, Medicare Advantage 60, Medicaid 90, commercial 180); check each contract. "
               "Yearly figures scale this period to 12 months. No patient names are shown, and the files aren't stored."
               f"<br><br>Files read:<ul>{files}</ul></div>")
    out.append("<script>const W=" + json.dumps(_worklist(a)).replace("</", "<\\/") + ";"
               "function dl(){const h=Object.keys(W[0]||{});const q=v=>'\"'+String(v??'').replace(/\"/g,'\"\"')+'\"';"
               "const t=[h.join(',')].concat(W.map(r=>h.map(k=>q(r[k])).join(','))).join('\\n');"
               "const u=URL.createObjectURL(new Blob([t],{type:'text/csv'}));const x=document.createElement('a');x.href=u;x.download='lost-money-worklist.csv';x.click()}"
               "</script></main></body></html>")
    return "".join(out)


def _levels_html(a: Analysis) -> str:
    c = a.levels["counts"]
    total = a.levels["total"] or 1
    rows = []
    for code in EM_NEW + EM_EST[1:]:
        n = c.get(code, 0)
        if n:
            rows.append(f"<div class=bar><span class=l>{code}</span><div class=t><div class=f style='width:{n / total * 100:.1f}%'></div></div><span class=n>{n:,} · {n / total:.0%}</span></div>")
    return "<div class=card style='margin-bottom:12px'>" + "".join(rows) + "</div>"


def _cell(v):
    """Spreadsheet-safe text: a value from a file that starts like a formula is kept as text."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def _worklist(a: Analysis) -> List[dict]:
    names = {k: t for k, t, _ in SECTIONS}
    return [{k: _cell(v) for k, v in {"section": names.get(f.category, f.category), "item": f.title, "confidence": f.confidence, "dollars": round(f.dollars, 2),
             "counted_in_found": f.counted and f.confidence in ("high", "medium"), "claim_id": f.claim_id, "payer_claim_id": f.payer_claim_id,
             "payer": f.payer, "date_of_service": f.dos, "code": f.code, "deadline": f.deadline, "days_left": f.days_left,
             "action": f.action, "why": f.why}.items()} for f in a.findings]


def worklist_csv(a: Analysis) -> str:
    rows = _worklist(a)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Sample data (made up), for demos and tests
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _isa(kind: str, body: List[str]) -> str:
    gs = {"835": "HP", "837": "HC"}[kind]
    ver = {"835": "005010X221A1", "837": "005010X222A1"}[kind]
    segs = [f"ST*{kind}*0001*{ver}"] + body
    segs.append(f"SE*{len(segs) + 1}*0001")
    return ("ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *261101*1200*^*00501*000000001*0*P*:~"
            f"GS*{gs}*SENDER*RECEIVER*20261101*1200*1*X*{ver}~" + "~".join(segs) + "~GE*1*1~IEA*1*000000001~")


PROVS = {"SMITH": ("ALEX", "1111111112"), "PATEL": ("RIYA", "1111111120"), "LEE": ("JORDAN", "1111111138")}   # made up


def build_837(claims: List[dict]) -> str:
    body = ["BHT*0019*00*1*20261101*1200*CH", "NM1*41*2*SAMPLE URGENT CARE*****46*123", "NM1*40*2*CLAIMMD*****46*CLAIMMD",
            "HL*1**20*1", "NM1*85*2*SAMPLE URGENT CARE*****XX*1111111112"]
    hl = 1
    for c in claims:
        hl += 1
        body += [f"HL*{hl}*1*22*0", f"SBR*P*18*******{c.get('ind', 'CI')}", f"NM1*IL*1*DOE*PAT****MI*M{c['id']}",
                 f"NM1*PR*2*{c['payer']}*****PI*{c.get('payer_id', '00001')}",
                 f"CLM*{c['id']}*{sum(l[2] for l in c['lines']):.2f}***20:B:1*Y*A*Y*Y", "HI*ABK:J029",
                 f"NM1*82*1*{c.get('prov', 'SMITH')}*{PROVS.get(c.get('prov', 'SMITH'), ('ALEX', '1111111112'))[0]}****XX*{PROVS.get(c.get('prov', 'SMITH'), ('ALEX', '1111111112'))[1]}"]
        for i, (code, mods, charge, units) in enumerate(c["lines"], 1):
            body += [f"LX*{i}", "SV1*HC:" + ":".join([code] + mods) + f"*{charge:.2f}*UN*{units}***1", f"DTP*472*D8*{c['dos'].replace('-', '')}"]
    return _isa("837", body)


def build_835(payer: str, date: str, claims: List[dict], ind: str = "CI") -> str:
    body = [f"BPR*I*{sum(c['paid'] for c in claims):.2f}*C*ACH*CCP*01*999999999*DA*123*1234567890**01*999999999*DA*456*{date.replace('-', '')}",
            f"TRN*1*CHK{date.replace('-', '')}*1234567890", f"N1*PR*{payer}", "REF*2U*00001", "N1*PE*SAMPLE URGENT CARE*XX*1111111112", "LX*1"]
    for c in claims:
        status = "4" if c["paid"] <= 0 and not c.get("pr") else "1"
        body += [f"CLP*{c['id']}*{status}*{c['charge']:.2f}*{c['paid']:.2f}*{c.get('pr', 0):.2f}*{c.get('ind', ind)}*ICN{c['id']}*11*1",
                 "NM1*QC*1*DOE*PAT****MI*M" + c["id"], f"NM1*82*1*{c.get('prov', 'SMITH')}*{PROVS.get(c.get('prov', 'SMITH'), ('ALEX', '1111111112'))[0]}****XX*{PROVS.get(c.get('prov', 'SMITH'), ('ALEX', '1111111112'))[1]}"]
        for code, mods, charge, paid, allowed, adjs, units in c["lines"]:
            body.append("SVC*HC:" + ":".join([code] + mods) + f"*{charge:.2f}*{paid:.2f}**{units}")
            body.append(f"DTM*472*{c['dos'].replace('-', '')}")
            for grp, carc, amt in adjs:
                body.append(f"CAS*{grp}*{carc}*{amt:.2f}")
            if allowed is not None:
                body.append(f"AMT*B6*{allowed:.2f}")
    return _isa("835", body)


def sample_files(seed: int = 7) -> List[Tuple[str, bytes]]:
    """Ninety days of a made-up single-site urgent care: four payers, the usual mistakes. Deterministic."""
    import random
    rnd = random.Random(seed)
    payers = {"AETNA": ("CI", {"99213": 92.0, "99214": 131.0, "99203": 118.0, "99204": 172.0, "87880": 16.5, "96372": 28.0, "J1885": 6.2,
                               "12002": 160.0, "99051": 24.0, "94640": 22.0, "J7613": 1.9, "36415": 3.5, "85025": 9.0}),
              "BLUE CROSS BLUE SHIELD OF TEXAS": ("BL", {"99213": 88.0, "99214": 124.0, "99203": 110.0, "99204": 160.0, "87880": 15.0,
                                                         "96372": 25.0, "J1885": 5.8, "12002": 150.0, "99051": 20.0, "94640": 20.0, "J7613": 1.5, "36415": 3.0, "85025": 8.0}),
              "UNITEDHEALTHCARE": ("CI", {"99213": 90.0, "99214": 128.0, "99203": 114.0, "99204": 166.0, "87880": 16.0, "96372": 27.0, "J1885": 6.0,
                                          "12002": 155.0, "94640": 21.0, "J7613": 1.7, "36415": 3.2, "85025": 8.5}),
              "MEDICARE PART B": ("MB", {"99213": 89.5, "99214": 126.0, "99203": 113.0, "99204": 170.0, "87880": 16.0, "96372": 14.5, "J1885": 4.1,
                                         "12002": 130.0, "94640": 19.0, "J7613": 0.9, "36415": 3.0, "85025": 7.8})}
    charges = {"99213": 145.0, "99214": 210.0, "99203": 185.0, "99204": 260.0, "87880": 45.0, "96372": 45.0, "J1885": 25.0,
               "12002": 140.0, "99051": 50.0, "94640": 60.0, "J7613": 15.0, "36415": 15.0, "85025": 35.0}   # 12002 priced below payers
    start = dt.date(2026, 7, 1)
    claims837, remits = [], defaultdict(list)
    for i in range(1, 241):
        payer = rnd.choices(list(payers), weights=[30, 30, 25, 15])[0]
        ind, rates = payers[payer]
        dos = start + dt.timedelta(days=rnd.randrange(0, 90))
        prov = rnd.choice(["SMITH", "SMITH", "PATEL", "LEE"])
        l3 = 0.75 if prov == "PATEL" else 0.42
        new = rnd.random() < 0.3
        em = ("99203" if rnd.random() < l3 else "99204") if new else ("99213" if rnd.random() < l3 else "99214")
        lines = [[em, [], 1]]
        extra = rnd.random()
        if extra < 0.35:
            lines.append(["87880", ["QW"] if ind == "MB" and rnd.random() < 0.6 else [], 1])
        elif extra < 0.47:
            lines += [["96372", [], 1]] + ([["J1885", [], 2]] if rnd.random() < 0.6 else [])
            lines[0][1] = ["25"] if rnd.random() < 0.6 else []
        elif extra < 0.53:
            lines += [["12002", [], 1]]
            lines[0][1] = ["25"]
        elif extra < 0.60:
            lines += [["94640", [], 1], ["J7613", [], 1]] if rnd.random() < 0.6 else [["J7613", [], 1]]
        elif extra < 0.66:
            lines += [["85025", [], 1]] + ([["36415", [], 1]] if rnd.random() < 0.4 else [])
        if dos.weekday() >= 5 and payer in ("AETNA", "BLUE CROSS BLUE SHIELD OF TEXAS") and rnd.random() < 0.4:
            lines.append(["99051", [], 1])
        cid = f"UC{i:05d}"
        claims837.append({"id": cid, "payer": payer, "ind": ind, "dos": dos.isoformat(), "prov": prov,
                          "lines": [(c, m, charges[c] * u, u) for c, m, u in lines]})
        if rnd.random() < 0.06:
            continue      # never answered
        pay_date = dos + dt.timedelta(days=rnd.randrange(14, 35))
        rl, paid_total, pr_total = [], 0.0, 0.0
        for code, mods, units in lines:
            charge = charges[code] * units
            allowed = rates.get(code, 0) * units
            r = rnd.random()
            if code == "99051" and payer == "UNITEDHEALTHCARE":
                rl.append((code, mods, charge, 0.0, None, [("CO", "96", charge)], units))
                continue
            if code == em and len(lines) > 1 and lines[1][0] in ("96372", "12002") and not mods and r < 0.7:
                rl.append((code, mods, charge, 0.0, None, [("CO", "97", charge)], units))
                continue
            if code == "87880" and ind == "MB" and "QW" not in mods:
                rl.append((code, mods, charge, 0.0, None, [("CO", "4", charge)], units))
                continue
            if r < 0.03:
                carc = rnd.choice(["16", "31", "50", "29", "197"])
                rl.append((code, mods, charge, 0.0, None, [("CO", carc, charge)], units))
                continue
            allowed = min(allowed, charge)      # payers pay the lesser of the charge or their rate (12002 is priced too low)
            if r > 0.97 and code in ("99214", "99213"):
                allowed = round(allowed * 0.85, 2)    # an underpayment
            pr = round(min(allowed, rnd.choice([0, 0, 25.0, 35.0])) if code == em else 0, 2)
            paid = round(allowed - pr, 2)
            if ind == "MB":
                paid = round(paid * 0.98, 2)
            adjs = []
            if charge - allowed > 0.004:
                adjs.append(("CO", "45", round(charge - allowed, 2)))
            if pr:
                adjs.append(("PR", "3", pr))
            if ind == "MB" and allowed:
                adjs.append(("CO", "253", round((allowed - pr) * 0.02, 2)))
            rl.append((code, mods, charge, paid, allowed, adjs, units))
            paid_total += paid
            pr_total += pr
        remits[(payer, pay_date.isoformat(), ind)].append({"id": cid, "dos": dos.isoformat(), "prov": prov, "charge": sum(x[2] for x in rl),
                                                           "paid": round(paid_total, 2), "pr": round(pr_total, 2), "ind": ind, "lines": rl})
    files = [("sample-claims.837", build_837(claims837).encode())]
    for (payer, date, ind), cls in sorted(remits.items()):
        files.append((f"era-{payer.split()[0].lower()}-{date}.835", build_835(payer, date, cls, ind).encode()))
    return files


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  In the app
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

try:
    from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    _HAS_FASTAPI = True
except ImportError:      # the command line works without the web stack
    _HAS_FASTAPI = False

if _HAS_FASTAPI:
    router = APIRouter()

    def _page(action: str, who: str) -> str:
        return f"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Lost Money Report</title><style>{STYLE}
.drop{{border:2px dashed var(--line);border-radius:14px;padding:36px 16px;text-align:center;background:var(--card);cursor:pointer}}
.drop.on{{border-color:var(--accent);background:var(--accent-soft)}} input[type=text]{{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);width:100%;max-width:360px}}
#list{{color:var(--muted);font-size:13px;margin:10px 0}} #err{{color:var(--bad)}}</style></head><body><main>
<h1>Lost Money Report</h1><p class=sub>{html.escape(who)}</p>
<div class=card><ol style="margin:0 0 0 18px;padding:0;color:var(--muted)">
<li>Export 90 days (or more) of <b>payment files (835 / ERA)</b> from your clearinghouse or billing system.</li>
<li>Add the <b>claim files (837)</b> for the same period if you can, or a <b>claims export (CSV)</b> with code, charge, payer, date and payment columns.</li>
<li>Drop them here (a zip works). Nothing is saved: the files are read once to build the report.</li></ol></div>
<form id=f style="margin-top:16px"><p><input type=text name=clinic placeholder="Clinic name (optional, shown on the report)"></p>
<div class=drop id=drop>Drop files here, or click to choose<input id=pick type=file name=files multiple hidden></div>
<div id=list></div><button id=go type=submit>Build the report</button> <span id=err></span></form>
<script>
const d=document.getElementById('drop'),p=document.getElementById('pick'),L=document.getElementById('list');let F=[];
const show=()=>L.textContent=F.length?F.length+' file(s): '+F.map(f=>f.name).slice(0,8).join(', ')+(F.length>8?'…':''):'';
d.onclick=()=>p.click();p.onchange=()=>{{F=[...p.files];show()}};
['dragover','dragenter'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.add('on')}}));
['dragleave','drop'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.remove('on')}}));
d.addEventListener('drop',x=>{{F=[...x.dataTransfer.files];show()}});
document.getElementById('f').onsubmit=async e=>{{e.preventDefault();const E=document.getElementById('err');E.textContent='';
if(!F.length){{E.textContent='Choose some files first.';return}}const b=document.getElementById('go');b.disabled=true;b.textContent='Reading…';
const fd=new FormData();fd.append('clinic',e.target.clinic.value);F.forEach(f=>fd.append('files',f,f.name));
try{{const r=await fetch('{action}',{{method:'POST',body:fd}});const t=await r.text();
if(!r.ok){{let m=t;try{{m=JSON.parse(t).error||t}}catch(_){{}};throw new Error(m)}}
document.open();document.write(t);document.close()}}catch(x){{E.textContent=x.message;b.disabled=false;b.textContent='Build the report'}}}};
</script></main></body></html>"""

    async def _read(files: List[UploadFile]) -> List[Tuple[str, bytes]]:
        out, total = [], 0
        for f in files[:500]:
            data = await f.read(MAX_FILE + 1)
            total += len(data)
            if total > MAX_TOTAL:
                raise ValueError("More than 200 MB in one go: split the upload.")
            out.append((os.path.basename(f.filename or "file"), data))
        return out

    def _respond(pairs, clinic: str, fmt: str):
        a = analyze(pairs)
        if not a.ds.claims and not a.ds.remits:
            errs = "; ".join(f"{f['name']}: {f['error']}" for f in a.ds.files if f["error"]) or "no 835, 837 or CSV data found"
            return JSONResponse({"error": f"Nothing to analyse ({errs})."}, status_code=400)
        if fmt == "json":
            return JSONResponse(a.to_dict())
        if fmt == "csv":
            from fastapi.responses import Response
            return Response(worklist_csv(a), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=lost-money-worklist.csv"})
        return HTMLResponse(render_html(a, clinic=clinic[:80]), headers={"Cache-Control": "no-store"})

    def _db():
        from auth import get_db
        yield from get_db()

    def _user_level(request: Request, db) -> Tuple[object, bool]:
        from auth import current_user, ROLE_LEVELS
        u = current_user(request, db)
        return u, bool(u) and ROLE_LEVELS.get(u.role or "viewer", 0) >= ROLE_LEVELS["biller"]

    def _biller(request: Request, db=Depends(_db)):
        from fastapi import HTTPException
        u, ok = _user_level(request, db)
        if not u:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not ok:
            raise HTTPException(status_code=403, detail="Billers and admins only.")
        return u

    def _admin(request: Request):
        from auth import require_admin
        return require_admin(request)

    @router.get("/lost-money", response_class=HTMLResponse)
    def lost_money_page(request: Request, db=Depends(_db)):
        u, ok = _user_level(request, db)
        if not u:
            return RedirectResponse("/login?next=/lost-money", status_code=303)
        if not ok:
            return HTMLResponse("Billers and admins only.", status_code=403)
        return HTMLResponse(_page("/api/lost-money/report", "Upload your payment and claim files; see the money you're owed, claim by claim."),
                            headers={"Cache-Control": "no-store"})

    @router.post("/api/lost-money/report")
    async def lost_money_report(files: List[UploadFile] = File(...), clinic: str = Form(""), fmt: str = Form("html"), user=Depends(_biller)):
        try:
            pairs = await _read(files)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=413)
        return _respond(pairs, clinic, fmt)

    @router.get("/admin/lost-money", response_class=HTMLResponse)
    def admin_lost_money_page(request: Request):
        from auth import current_admin
        if not current_admin(request):
            return RedirectResponse("/admin", status_code=303)
        return HTMLResponse(_page("/api/admin/lost-money/report", "Althais staff: a prospect's files, only under a signed BAA. Nothing is stored."),
                            headers={"Cache-Control": "no-store"})

    @router.post("/api/admin/lost-money/report")
    async def admin_lost_money_report(files: List[UploadFile] = File(...), clinic: str = Form(""), fmt: str = Form("html"), ok=Depends(_admin)):
        try:
            pairs = await _read(files)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=413)
        return _respond(pairs, clinic, fmt)

    @router.get("/admin/lost-money/sample", response_class=HTMLResponse)
    def admin_lost_money_sample(request: Request):
        from auth import current_admin
        if not current_admin(request):
            return RedirectResponse("/admin", status_code=303)
        return HTMLResponse(render_html(analyze(sample_files(), today=dt.date(2026, 11, 1)), clinic="Sample Urgent Care", sample=True))


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Command line
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _gather(paths: List[str]) -> List[Tuple[str, bytes]]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in sorted(names):
                    if not n.startswith("."):
                        with open(os.path.join(root, n), "rb") as fh:
                            out.append((os.path.relpath(os.path.join(root, n), p), fh.read()))
        else:
            with open(p, "rb") as fh:
                out.append((os.path.basename(p), fh.read()))
    return out


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build a Lost Money Report from 835/837/CSV files.")
    ap.add_argument("paths", nargs="*", help="files or folders (zips work)")
    ap.add_argument("-o", "--out", default="lost-money-report.html")
    ap.add_argument("--clinic", default="")
    ap.add_argument("--sample", action="store_true", help="use made-up sample data")
    ap.add_argument("--today", default="", help="pretend today is YYYY-MM-DD (deadlines are counted from it)")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.today) if args.today else None
    if args.sample:
        pairs, clinic = sample_files(), args.clinic or "Sample Urgent Care"
        today = today or dt.date(2026, 11, 1)
    else:
        if not args.paths:
            ap.error("give files or folders, or --sample")
        pairs, clinic = _gather(args.paths), args.clinic
    a = analyze(pairs, today)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_html(a, clinic=clinic, sample=args.sample))
    base = os.path.splitext(args.out)[0]
    with open(base + "-worklist.csv", "w", encoding="utf-8", newline="") as fh:
        fh.write(worklist_csv(a))
    s = a.summary()
    print(f"{s['visits']} visits, {s['payments']} payer answers, {s['period']['start']} to {s['period']['end']}")
    print(f"Money found: ${s['found']:,.2f} (about ${s['found_annual']:,.0f} a year)")
    print(f"Possible, needs review: ${s['possible']:,.2f}   Lost for good: ${s['lost']:,.2f}")
    for k, t, _ in SECTIONS:
        c = s["categories"].get(k)
        if c:
            print(f"  {t}: {c['count']} items, ${c['dollars']:,.2f}")
    for f in s["files"]:
        if f["error"]:
            print(f"  ! {f['name']}: {f['error']}")
    print(f"Wrote {args.out} and {base}-worklist.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
