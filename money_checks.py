"""
money_checks.py: checks run on a claim before it's sent, from inside any EHR (the browser extension), that catch money
the claim would otherwise lose. They come from two places:

1. The clinic's own payer memory, learned from its Lost Money Reports (learn() runs on every report a clinic runs):
   - what each payer usually pays the clinic per code (so the panel can say what the claim should bring in)
   - the add-on codes each payer pays the clinic (99051 on weekends, S9088): flagged when missing
   - each payer's repeated denials of a code with a given set of modifiers: flagged before the same claim goes out
2. Rules that hold everywhere: deleted codes, consult codes to Medicare, QW on waived tests for government payers,
   modifier 25 on a visit billed with a procedure, drug and injection pairs, nebulizer pairs, venipuncture.

Plus the Althais network: a denial pattern seen at two or more clinics for the same payer is shown to every clinic
(only the pattern: payer, code, modifiers, reason. Never amounts, rates or anything about another clinic).

Stored in org_settings: category "payer_memory" per clinic, and the network under the org key "__network__"
(clinics there are one-way hashes). No patient data is learned or stored here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from sqlalchemy import select

import lost_money as lm

CATEGORY = "payer_memory"
NETWORK = "__network__"
MIN_DENIALS = 2          # a payer has to deny the same thing this many times before it's a pattern
MIN_CLINICS = 2          # network patterns need this many clinics


def _norm(payer: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (payer or "").lower())


def _row(db, org: str):
    from auth import OrgSettings
    return db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))


def _load(db, org: str) -> dict:
    r = _row(db, org)
    try:
        return json.loads(r.data or "{}") if r else {}
    except Exception:
        return {}


def _store(db, org: str, doc: dict):
    from auth import OrgSettings
    r = _row(db, org)
    if r is None:
        r = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(r)
    r.data = json.dumps(doc)


def _mods_key(mods) -> str:
    return "+".join(sorted(m for m in mods if m)) or "-"


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Learning, from a Lost Money Report's analysis
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def learn(db, org: str, a: "lm.Analysis") -> dict:
    """Update the clinic's payer memory from the payments in a report. Payers in this report replace what was known about
    them (re-uploading the same files never double counts); payers not in it are kept."""
    paid: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    denials: Dict[str, Counter] = defaultdict(Counter)
    names: Dict[str, str] = {}
    for r in a.remits:
        p = _norm(r.payer)
        if not p:
            continue
        names[p] = r.payer
        for l in r.lines:
            if lm._denied(l):
                carc = lm._denial_carc(l.adjustments + r.adjustments)
                if carc and lm.CARC_KIND.get(carc) not in ("timely", "duplicate"):
                    denials[p][f"{l.code}|{carc}|{_mods_key(l.mods)}"] += 1
                continue
            al = lm._allowed(l)
            if al and al > 0 and not any(x.carc == "59" for x in l.adjustments):
                paid[p][l.code].append(al / (l.units or 1))
    doc = _load(db, org)
    payers = doc.setdefault("payers", {})
    for p in set(paid) | set(denials):
        payers[p] = {"name": names.get(p, p).title(),
                     "rates": {code: {"rate": round(lm._mode(v), 2), "n": len(v)} for code, v in paid[p].items()},
                     "denials": {k: n for k, n in denials[p].items()},
                     "updated": dt.date.today().isoformat()}
    doc["updated"] = dt.date.today().isoformat()
    _store(db, org, doc)
    try:            # weekly paid/denied counts for payer change alerts (network.py)
        import network
        network.record_signals(db, org, a)
    except Exception as e:
        print(f"[NETWORK] signals: {type(e).__name__}")
    # the network: patterns only, clinics hashed
    net = _load(db, NETWORK)
    pats = net.setdefault("patterns", {})
    me = hashlib.sha256(("althais-network|" + org).encode()).hexdigest()[:16]
    for p, cnt in denials.items():
        for k, n in cnt.items():
            if n >= MIN_DENIALS:
                key = f"{p}|{k}"
                entry = pats.setdefault(key, {"name": names.get(p, p).title(), "clinics": []})
                if me not in entry["clinics"]:
                    entry["clinics"] = (entry["clinics"] + [me])[-50:]
    _store(db, NETWORK, net)
    return {"payers": len(set(paid) | set(denials)), "patterns": sum(1 for c in denials.values() for n in c.values() if n >= MIN_DENIALS)}


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
#  Checking one claim
# ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _match_payer(payers: dict, payer: str) -> Optional[str]:
    """The remembered payer this claim's payer is: same name, or one name is the start of the other ("Aetna" / "Aetna PPO")."""
    want = _norm(payer)
    if not want:
        return None
    if want in payers:
        return want
    hits = [p for p in payers if len(min(p, want, key=len)) >= 4 and (p.startswith(want) or want.startswith(p))]
    return max(hits, key=len) if hits else None


def _mods_by_code(c: dict) -> Dict[str, List[str]]:
    """Modifiers per procedure code. Screens that only list claim-level modifiers put them on the first code that can carry them."""
    out: Dict[str, List[str]] = {code: [] for code in c.get("cpt") or []}
    lm_ = c.get("line_mods") or []
    if lm_:
        for x in lm_:
            out.setdefault(x["code"], []).append(x["mod"])
        return out
    for mod in c.get("modifiers") or []:
        if mod == "25":
            target = next((x for x in out if x in lm.EM_ALL), None)
        elif mod == "QW":
            target = next((x for x in out if x in lm.QW_TESTS), None)
        else:
            target = next((x for x in out if x not in lm.EM_ALL), None)
        if target:
            out[target].append(mod)
    return out


def check(db, org: str, c: dict) -> dict:
    """Money checks for one claim: {"flags": [...], "expected": dollars or None, "at_stake": dollars}.
    c: the extension's clean claim (cpt, modifiers, line_mods, payer, dos, amount)."""
    flags: List[dict] = []
    cpts = [x for x in c.get("cpt") or [] if x]
    if not cpts:
        return {"flags": [], "expected": None, "at_stake": 0.0}
    mods = _mods_by_code(c)
    codes = set(cpts)
    payer = c.get("payer") or ""
    ptype = lm._payer_type("", payer)
    doc = _load(db, org)
    pkey = _match_payer(doc.get("payers") or {}, payer)
    mem = (doc.get("payers") or {}).get(pkey) if pkey else None
    pname = (mem or {}).get("name") or payer or "This payer"
    rates = (mem or {}).get("rates") or {}

    def rate(code, fallback=None):
        return (rates.get(code) or {}).get("rate", fallback)

    def add(sev, key, msg, dollars=0.0, fix=""):
        flags.append({"severity": sev, "code": "money_" + key, "message": msg, "dollars": round(dollars or 0, 2), "fix": fix})

    em = [x for x in cpts if x in lm.EM_ALL]
    # codes that can't be paid
    for code in cpts:
        if code in lm.DELETED:
            add("error", "deleted", f"{code} isn't a valid code any more ({lm.DELETED[code]}). Payers deny it outright.")
        if code in lm.CONSULTS and ptype in ("medicare", "medicare_advantage"):
            add("error", "consult", f"{code} is a consult code. Medicare stopped paying consults in 2010: bill the office visit level (99202-99215) instead.")
        if code in lm.QW_TESTS and "QW" not in mods.get(code, []) and ptype in ("medicare", "medicare_advantage", "medicaid"):
            add("warn", "qw", f"{code} ({lm.QW_TESTS[code]}) needs modifier QW for {payer or 'this payer'} as a CLIA-waived test.", rate(code, 0), "Add QW to " + code)
    # a visit with a procedure
    procs = sorted(x for x in codes if lm._proc_needs_25(x))
    if em and procs and not any("25" in mods.get(x, []) for x in em):
        add("warn", "mod25", f"{em[0]} is billed with {', '.join(procs[:3])} and has no modifier 25. Without it most payers fold the visit into the procedure. "
            "Add 25 if the note shows a separate, significant problem was evaluated.", rate(em[0], 0), f"Add 25 to {em[0]}")
    # pairs that go together
    drugs = {x for x in codes if x.startswith("J") and not lm.INHALATION_DRUG.match(x) and not x.startswith("J8")}
    inhaled = {x for x in codes if lm.INHALATION_DRUG.match(x)}
    if codes & lm.DRUG_ADMIN and not drugs and not any(x.startswith(("Q", "90")) and x not in lm.DRUG_ADMIN for x in codes):
        adm = sorted(codes & lm.DRUG_ADMIN)[0]
        add("warn", "no_drug", f"{adm} (the injection) is billed without the drug. Add the drug's J-code and units from the note (e.g. J1885, J0696, J1100).")
    if drugs and not codes & lm.DRUG_ADMIN:
        add("warn", "no_admin", f"{', '.join(sorted(drugs))} is billed without an administration code. Add 96372 (or the IV push code) if the clinic gave it.",
            rate("96372", 0), "Add 96372")
    if inhaled and not codes & lm.NEB_ADMIN:
        add("warn", "no_neb", f"{', '.join(sorted(inhaled))} is billed without the nebulizer treatment. Add 94640.", rate("94640", 0), "Add 94640")
    if codes & lm.BLOOD_LABS and not codes & lm.VENIPUNCTURE:
        add("info", "no_draw", "A blood test is billed without 36415. Add it if the clinic drew the blood.", rate("36415", 0), "Add 36415")
    # add-ons this payer pays the clinic
    if em and mem:
        for addon, what in {"99051": "after-hours, weekend and holiday visits", "S9088": "urgent care center visits"}.items():
            r = rates.get(addon)
            if addon in codes or not r or r.get("n", 0) < 2:
                continue
            if addon == "99051":
                try:
                    if dt.date.fromisoformat(c.get("dos") or "").weekday() < 5:
                        continue
                except ValueError:
                    continue
            add("warn", "addon", f"{pname} has paid you for {addon} ({what}) {r['n']} times, about ${r['rate']:,.2f} each. "
                f"{'This visit is on a weekend. ' if addon == '99051' else ''}Add it if the visit qualifies.", r["rate"], f"Add {addon}")
    # this payer's own denial patterns
    own = set()
    for k, n in ((mem or {}).get("denials") or {}).items():
        code, carc, mk = (k.split("|") + ["", "", ""])[:3]
        if code in codes and n >= MIN_DENIALS and mk == _mods_key(mods.get(code, [])):
            own.add((code, carc, mk))
            add("warn", "payer_pattern", f"{pname} denied {code} {'with ' + mk.replace('+', ', ') if mk != '-' else 'with no modifier'} {n} times in your payment files "
                f"(reason {carc}: {lm.CARC.get(carc, 'see the remark')}). " + lm._rule_text(pname, code, carc, mk if mk != '-' else 'no modifier'),
                rate(code, 0))
    # the network
    net = (_load(db, NETWORK).get("patterns") or {})
    want = _norm(payer)
    if want:
        for key, entry in net.items():
            p, code, carc, mk = (key.split("|") + ["", "", "", ""])[:4]
            if not (p == want or (len(min(p, want, key=len)) >= 4 and (p.startswith(want) or want.startswith(p)))):
                continue
            if code in codes and mk == _mods_key(mods.get(code, [])) and (code, carc, mk) not in own and len(entry.get("clinics") or []) >= MIN_CLINICS:
                add("info", "network", f"Across {len(entry['clinics'])} Althais clinics, {entry.get('name') or payer} has denied {code} "
                    f"{'with ' + mk.replace('+', ', ') if mk != '-' else 'with no modifier'} (reason {carc}: {lm.CARC.get(carc, 'see the remark')}). "
                    + lm._rule_text(entry.get("name") or payer, code, carc, mk if mk != '-' else 'no modifier'))
    # payer changes the Althais network is seeing right now
    try:
        import network
        for al in network.alerts_for(db, org, [payer]):
            if al["code"] in codes and (al["payer"] == _norm(payer) or _match_payer({al["payer"]: 1}, payer)) and al["mods"] == _mods_key(mods.get(al["code"], [])):
                add("warn", "payer_change", "Heads up: " + al["message"] + " " + lm._rule_text(al.get("payerName") or payer, al["code"], al["carc"], al["mods"] if al["mods"] != "-" else "no modifier"),
                    rate(al["code"], 0))
    except Exception:
        pass
    expected = None
    if mem and all(x in rates for x in cpts):
        expected = round(sum(rates[x]["rate"] for x in cpts), 2)
    at_stake = round(sum(f["dollars"] for f in flags if f["severity"] in ("warn", "error")), 2)
    return {"flags": flags, "expected": expected, "at_stake": at_stake, "payer_known": bool(mem), "payer_name": pname if mem else ""}
