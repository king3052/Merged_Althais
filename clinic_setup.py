"""
clinic_setup.py: what a clinic tells Althais about itself so the Lost Money Report uses its real numbers, plus the
checklist that takes a new clinic from signed to working.

  - Appeal windows and filing limits, per payer (or per kind of payer). Without them the report estimates from the
    payer type and says so on every deadline.
  - Contracted rates (a CSV of payer, code, modifier, rate). With them, a payer that underpays on every claim is
    caught: comparing a payer only with its own usual rate never can.
  - Site names for each billing NPI, so a clinic with several locations sees which one is losing money.
  - When the business associate agreement was signed, and by whom. No clinic data should be taken before it.

Stored in OrgSettings (category "clinic_setup"). Billers and admins of the clinic edit it at /clinic-setup; the BAA
record is the clinic admin's (or Althais's, from /admin).
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import re
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

import lost_money as lm
from auth import OrgSettings, ROLE_LEVELS, User, current_user, get_db

router = APIRouter()

CATEGORY = "clinic_setup"
TYPES = ("medicare", "medicare_advantage", "medicaid", "commercial")
TYPE_NAMES = {"medicare": "Medicare", "medicare_advantage": "Medicare Advantage", "medicaid": "Medicaid", "commercial": "Commercial"}
MAX_DAYS = 3 * 365
MAX_RATES = 20000


def _now() -> dt.datetime:
    return dt.datetime.utcnow().replace(microsecond=0)


# ── what's stored ──

def _row(db, org):
    return db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))


def load(db, org: str) -> dict:
    row = _row(db, org)
    try:
        return json.loads(row.data or "{}") if row else {}
    except ValueError:
        return {}


def save(db, org: str, doc: dict) -> None:
    row = _row(db, org)
    if row is None:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    row.data = json.dumps(doc)
    db.flush()


def terms(db, org: str) -> lm.ClinicTerms:
    """The clinic's settings in the form the report uses."""
    d = load(db, org)
    rates: Dict[Tuple[str, str, Tuple[str, ...]], float] = {}
    for payer, table in (d.get("contracts") or {}).items():
        for key, rate in (table or {}).items():
            code, _, mods = key.partition("|")
            rates[(lm._payer_key(payer), code, tuple(m for m in mods.split("+") if m))] = float(rate)
    return lm.ClinicTerms(appeal_days={k: int(v) for k, v in (d.get("appeal") or {}).items()},
                          filing_days={k: int(v) for k, v in (d.get("filing") or {}).items()},
                          rates=rates, sites=dict(d.get("sites") or {}))


# ── checking what's entered ──

def clean_days(table) -> Tuple[Dict[str, int], List[str]]:
    """{payer name or "type:<kind>": days} from what the page sent. Blank means "use the estimate"."""
    out, errors = {}, []
    for k, v in (table or {}).items():
        name = re.sub(r"\s+", " ", str(k or "")).strip()[:120]
        if not name:
            continue
        if name.lower().startswith("type:"):
            kind = name[5:].strip().lower()
            if kind not in TYPES:
                errors.append(f"Unknown payer type: {kind}")
                continue
            name = "type:" + kind
        else:
            name = lm._payer_key(name)
        if v in (None, ""):
            continue
        try:
            days = int(str(v).strip())
        except ValueError:
            errors.append(f"{name}: days must be a whole number")
            continue
        if not 1 <= days <= MAX_DAYS:
            errors.append(f"{name}: days must be between 1 and {MAX_DAYS}")
            continue
        out[name] = days
    return out, errors


_CODE = re.compile(r"^[A-Z0-9]\d{3}[A-Z0-9]$")
_HEAD = {"payer": ("payer", "insurer", "insurance", "plan", "carrier", "payer name"),
         "code": ("code", "cpt", "hcpcs", "cpt/hcpcs", "procedure", "procedure code", "cpt code", "service code"),
         "mods": ("modifier", "modifiers", "mod", "mods", "modifier 1"),
         "rate": ("rate", "allowed", "allowable", "allowed amount", "contract rate", "contracted rate", "fee", "amount", "fee schedule", "price")}


def parse_contracts(text: str, payer: str = "") -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """A fee schedule as CSV -> ({payer: {"CODE|MOD+MOD": rate}}, problems). Columns found by name: payer (or the
    payer chosen on the page), code, modifier (optional), rate. Only modifiers that change the price are kept."""
    errors: List[str] = []
    text = (text or "").lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return {}, ["The file is empty."]
    head = [re.sub(r"\s+", " ", h).strip().lower() for h in rows[0]]
    col = {}
    for want, names in _HEAD.items():
        col[want] = next((i for i, h in enumerate(head) if h in names), None)
    if col["code"] is None or col["rate"] is None:
        return {}, ["The first row needs column names, including a code column (CPT or HCPCS) and a rate column (contracted or allowed amount)."]
    if col["payer"] is None and not payer.strip():
        return {}, ["The file has no payer column: choose which payer this fee schedule is for."]
    out: Dict[str, Dict[str, float]] = {}
    n = 0
    for i, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        get = lambda k: (r[col[k]].strip() if col[k] is not None and col[k] < len(r) else "")
        p = lm._payer_key(get("payer") or payer)
        code = get("code").upper().replace(" ", "")
        raw = get("rate").replace("$", "").replace(",", "")
        if not p:
            errors.append(f"Row {i}: no payer")
            continue
        if not _CODE.match(code):
            errors.append(f"Row {i}: '{code}' isn't a CPT or HCPCS code")
            continue
        try:
            rate = round(float(raw), 2)
        except ValueError:
            errors.append(f"Row {i}: '{raw}' isn't an amount")
            continue
        if not 0 < rate < 100000:
            errors.append(f"Row {i}: the rate must be more than $0")
            continue
        mods = sorted({m for m in re.split(r"[\s,;:+/|-]+", get("mods").upper()) if m in lm.PRICE_MODS})
        out.setdefault(p, {})[code + "|" + "+".join(mods)] = rate
        n += 1
        if n >= MAX_RATES:
            errors.append(f"Only the first {MAX_RATES:,} rates were read.")
            break
    if not out and not errors:
        errors.append("No rates found in the file.")
    return out, errors[:50]


# ── what the clinic's files say (payers and billing NPIs seen) ──

def seen(db, org: str, limit: int = 150) -> dict:
    """Payers (with their kind) and billing NPIs in the clinic's kept payment files, most frequent first."""
    import crypto_box
    import monitor
    payers: Dict[str, Dict] = {}
    npis: Dict[str, int] = {}
    files = db.scalars(select(monitor.PaymentFile).where(monitor.PaymentFile.org_key == org, monitor.PaymentFile.kind == "835")
                       .order_by(monitor.PaymentFile.id.desc()).limit(limit)).all()
    for f in files:
        try:
            text = crypto_box.open_text(f.sealed, "payfile", "PHI_ENC_KEY")
        except Exception:
            continue
        for ix in lm.interchanges(text):
            try:
                for r in lm.parse_835(ix):
                    p = lm._payer_key(r.payer)
                    if p:
                        e = payers.setdefault(p, {"name": p, "type": r.payer_type, "claims": 0})
                        e["claims"] += 1
                    if r.payee_npi:
                        npis[r.payee_npi] = npis.get(r.payee_npi, 0) + 1
            except (ValueError, IndexError):
                continue
    return {"payers": sorted(payers.values(), key=lambda x: -x["claims"]),
            "npis": [{"npi": k, "claims": v} for k, v in sorted(npis.items(), key=lambda kv: -kv[1])]}


# ── the checklist ──

def _members(db, org: str) -> List[User]:
    from auth import _doc_org_key
    return [u for u in db.scalars(select(User).where(User.organization == org)) if _doc_org_key(u) == org]


def checklist(db, org: str, payers_seen: Optional[list] = None) -> List[dict]:
    """The steps from a signed clinic to one that's working, each with done / not done and what to do next."""
    import monitor
    import payment_sources
    d = load(db, org)
    steps = []

    def step(key, title, done, how, required=True):
        steps.append({"key": key, "title": title, "done": bool(done), "how": how, "required": required})

    baa = d.get("baa") or {}
    step("baa", "Business associate agreement signed", baa.get("signedAt"),
         f"Signed {baa.get('signedAt')} by {baa.get('signer')}." if baa.get("signedAt") else
         "Sign the BAA with Althais before sharing any payment or claim files, then record it below.")
    members = _members(db, org)
    billers = [u for u in members if (u.role or "") in ("biller", "admin")]
    step("team", "Your biller has an account", len(billers) >= 2 or any((u.role or "") == "biller" for u in members),
         "Invite whoever works denials (your biller or billing company) from Team, with the Biller role.")
    need = [u for u in billers if (u.active if u.active is not None else 1)]
    mfa_ok = bool(need) and all(getattr(u, "mfa_enabled", 0) for u in need)
    step("mfa", "Two-step login on for admins and billers", mfa_ok,
         "Each admin and biller turns on two-step login (authenticator app) the next time they sign in." if not mfa_ok else "Everyone who can see payment data uses two-step login.")
    ps = payment_sources.status(db, org)
    files = db.scalar(select(func.count(monitor.PaymentFile.id)).where(monitor.PaymentFile.org_key == org)) or 0
    step("files", "Payment files coming in", ps.get("connected") or files,
         "Connected to Claim.MD: new payment files arrive every hour." if ps.get("connected") else
         f"{files} file(s) uploaded." if files else
         "Connect your Claim.MD account on the Lost Money page, or upload 90 days of 835 payment files from your clearinghouse.")
    st = monitor.status(db, org)
    step("report", "First Lost Money Report built", st.get("lastRun"),
         f"Last built {st.get('lastRun', '')[:10]}." if st.get("lastRun") else "Build it on the Lost Money page once files are in.")
    payers_seen = payers_seen if payers_seen is not None else []
    appeal = {lm._payer_key(k) if not k.startswith("type:") else k for k in (d.get("appeal") or {})}
    top = [p["name"] for p in payers_seen[:5]]
    covered = [p for p in top if p in appeal or f"type:{next((x['type'] for x in payers_seen if x['name'] == p), '')}" in appeal]
    step("deadlines", "Appeal windows set for your main payers", (top and len(covered) == len(top)) or d.get("deadlinesConfirmedAt"),
         "Set." if (top and len(covered) == len(top)) else
         f"Enter the appeal window from each contract for {', '.join(p.title() for p in top if p not in covered) or 'your payers'}. Until then the report estimates them.")
    step("contracts", "Contracted rates uploaded", d.get("contracts"),
         f"{_n(sum(len(v) for v in (d.get('contracts') or {}).values()), 'rate')} for {_n(len(d.get('contracts') or {}), 'payer')}." if d.get("contracts") else
         "Upload each payer's fee schedule (CSV: code, modifier, rate). It's how underpayments on every claim get caught.", required=False)
    try:
        import cpt_notice
        pending = [u.email for u in billers if not cpt_notice.accepted(db, org, u.email)]
        step("cpt", "CPT license notice accepted", not pending,
             "Everyone who sees codes has accepted it." if not pending else f"Shown at next sign-in to {len(pending)} person(s).", required=False)
    except Exception:
        pass
    return steps


def _n(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# ── routes ──

def _org(request: Request, db, admin_only: bool = False):
    import billing_activation as ba
    u = current_user(request, db)
    if not u:
        return None, None, JSONResponse({"error": "Not signed in."}, status_code=401)
    need = "admin" if admin_only else "biller"
    if ROLE_LEVELS.get(u.role or "viewer", 0) < ROLE_LEVELS[need]:
        return None, None, JSONResponse({"error": "Admins only." if admin_only else "Billers and admins only."}, status_code=403)
    org = ba.org_of(u)
    if org.startswith("user:"):
        return None, None, JSONResponse({"error": "Join or create a clinic first."}, status_code=400)
    return u, org, None


def _view(db, org: str) -> dict:
    d = load(db, org)
    s = seen(db, org)
    contracts = d.get("contracts") or {}
    return {"appeal": d.get("appeal") or {}, "filing": d.get("filing") or {}, "sites": d.get("sites") or {},
            "contracts": [{"payer": p, "rates": len(v)} for p, v in sorted(contracts.items())],
            "contractFiles": (d.get("contractFiles") or [])[-10:], "baa": d.get("baa") or {},
            "defaults": {"appeal": lm.APPEAL_DAYS, "filing": lm.FILING_DAYS}, "types": TYPE_NAMES,
            "payersSeen": s["payers"], "npisSeen": s["npis"], "checklist": checklist(db, org, s["payers"])}


def _audit(request, u, org, what, detail):
    try:
        import audit
        audit.record(user=u, org_key=org, action="update", what=what, method=request.method, path=request.url.path,
                     status=200, ip=audit.client_ip(request), detail=detail[:900])
    except Exception:
        pass


@router.get("/api/clinic-setup")
def api_get(request: Request, db=Depends(get_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    return _view(db, org)


@router.post("/api/clinic-setup/deadlines")
async def api_deadlines(request: Request, db=Depends(get_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    body = await request.json()
    appeal, e1 = clean_days(body.get("appeal"))
    filing, e2 = clean_days(body.get("filing"))
    if e1 or e2:
        return JSONResponse({"error": "; ".join(e1 + e2)}, status_code=400)
    d = load(db, org)
    d["appeal"], d["filing"] = appeal, filing
    if body.get("confirm"):
        d["deadlinesConfirmedAt"], d["deadlinesConfirmedBy"] = _now().isoformat() + "Z", u.email
    save(db, org, d)
    db.commit()
    _audit(request, u, org, "clinic_setup", f"Appeal windows {appeal}; filing limits {filing}")
    return _view(db, org)


@router.post("/api/clinic-setup/contracts")
async def api_contracts(request: Request, file: UploadFile = File(...), payer: str = Form(""), replace: str = Form("1"), db=Depends(get_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        return JSONResponse({"error": "Larger than 5 MB: split the fee schedule."}, status_code=413)
    rates, problems = parse_contracts(lm._decode(data), payer)
    if not rates:
        return JSONResponse({"error": "; ".join(problems) or "No rates found."}, status_code=400)
    d = load(db, org)
    table = d.setdefault("contracts", {})
    for p, v in rates.items():
        if replace == "1" or p not in table:
            table[p] = v
        else:
            table[p].update(v)
    d.setdefault("contractFiles", []).append({"name": (file.filename or "file")[:120], "payers": sorted(rates), "rates": sum(len(v) for v in rates.values()),
                                              "at": _now().isoformat() + "Z", "by": u.email})
    d["contractFiles"] = d["contractFiles"][-25:]
    save(db, org, d)
    db.commit()
    _audit(request, u, org, "clinic_setup", f"Contract rates uploaded: {sum(len(v) for v in rates.values())} for {', '.join(sorted(rates))}")
    return dict(_view(db, org), problems=problems)


@router.post("/api/clinic-setup/contracts/remove")
async def api_contracts_remove(request: Request, db=Depends(get_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    payer = lm._payer_key((await request.json()).get("payer") or "")
    d = load(db, org)
    if payer in (d.get("contracts") or {}):
        del d["contracts"][payer]
        save(db, org, d)
        db.commit()
        _audit(request, u, org, "clinic_setup", f"Contract rates removed for {payer}")
    return _view(db, org)


@router.post("/api/clinic-setup/sites")
async def api_sites(request: Request, db=Depends(get_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    raw = (await request.json()).get("sites") or {}
    sites = {}
    for npi, name in raw.items():
        npi = re.sub(r"\D", "", str(npi))
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if len(npi) == 10 and name:
            sites[npi] = name
    d = load(db, org)
    d["sites"] = sites
    save(db, org, d)
    db.commit()
    _audit(request, u, org, "clinic_setup", f"Site names {sites}")
    return _view(db, org)


@router.post("/api/clinic-setup/baa")
async def api_baa(request: Request, db=Depends(get_db)):
    """The clinic admin records the signed BAA: when, and who signed for the clinic."""
    u, org, err = _org(request, db, admin_only=True)
    if err:
        return err
    body = await request.json()
    signer = re.sub(r"\s+", " ", str(body.get("signer") or "")).strip()[:120]
    try:
        when = dt.date.fromisoformat(str(body.get("signedAt") or ""))
    except ValueError:
        return JSONResponse({"error": "Enter the date it was signed."}, status_code=400)
    if not signer:
        return JSONResponse({"error": "Enter who signed for the clinic."}, status_code=400)
    if when > dt.date.today():
        return JSONResponse({"error": "The date can't be in the future."}, status_code=400)
    d = load(db, org)
    d["baa"] = {"signedAt": when.isoformat(), "signer": signer, "recordedBy": u.email, "recordedAt": _now().isoformat() + "Z"}
    save(db, org, d)
    db.commit()
    _audit(request, u, org, "clinic_setup", f"BAA recorded: signed {when} by {signer}")
    return _view(db, org)


@router.get("/clinic-setup", response_class=HTMLResponse)
def page(request: Request, db=Depends(get_db)):
    u = current_user(request, db)
    if not u:
        return RedirectResponse("/login?next=/clinic-setup", status_code=303)
    if ROLE_LEVELS.get(u.role or "viewer", 0) < ROLE_LEVELS["biller"]:
        return HTMLResponse("Billers and admins only.", status_code=403)
    return HTMLResponse(PAGE.replace("__ADMIN__", "true" if (u.role or "") == "admin" else "false"), headers={"Cache-Control": "no-store"})


PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Clinic setup</title><style>""" + lm.STYLE + """
input,select{font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:3px;height:28px;background:var(--card);color:var(--ink)}
input.d{width:80px;text-align:right}.ok{color:var(--good);font-weight:600}.no{color:var(--warn);font-weight:600}
.step td:first-child{width:28px}.msg{color:var(--muted);margin-left:8px}.err{color:var(--bad)}
</style></head><body><main>
<h1>Clinic setup</h1><p class=sub>What Althais needs from your clinic to work from your real numbers. Changes apply to the next report.</p>
<h2>Checklist</h2><div class=wrap><table id=steps></table></div>
<h2>Business associate agreement</h2>
<p class=sub>Althais must not receive payment or claim files before the BAA is signed.</p>
<div class=card id=baa></div>
<h2>Appeal windows and filing limits</h2>
<p class=sub>Days your contracts give you to appeal or correct a claim after the payment, and to file after the visit. Blank uses the
estimate shown (and the report marks those deadlines as estimates). Payers below are the ones in your payment files.</p>
<div class=wrap><table id=days></table></div>
<p><button id=saveDays>Save windows</button> <label><input type=checkbox id=confirm> I've checked these against our contracts</label> <span class=msg id=daysMsg></span></p>
<h2>Contracted rates</h2>
<p class=sub>A CSV per payer: columns <b>code</b>, <b>rate</b> (contracted allowed amount), optional <b>modifier</b> and <b>payer</b>.
With rates, Althais flags every line paid under contract, even when the payer underpays every time.</p>
<div class=wrap><table id=rates></table></div>
<form id=up style="margin-top:10px"><input type=file name=file accept=".csv,.txt,.tsv" required>
<select name=payer id=payerPick><option value="">Payer is in the file</option></select>
<label><input type=checkbox name=replace value=1 checked> Replace that payer's rates</label> <button>Upload rates</button> <span class=msg id=upMsg></span></form>
<h2>Sites</h2><p class=sub>Name each billing NPI in your payment files, so the report shows which location the money is at.</p>
<div class=wrap><table id=sites></table></div><p><button id=saveSites>Save names</button> <span class=msg id=sitesMsg></span></p>
<p class=more>Questions? kyankarla@althais.com</p>
<script>
const ADMIN=__ADMIN__;let V={};const $=i=>document.getElementById(i);const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function j(u,o){const r=await fetch(u,o);const t=await r.json().catch(()=>({error:'Something went wrong.'}));if(!r.ok)throw new Error(t.error||t.detail||'Something went wrong.');return t}
function draw(v){V=v;
 $('steps').innerHTML='<tr><th></th><th>Step</th><th>Status</th></tr>'+v.checklist.map(s=>`<tr class=step><td class=${s.done?'ok':'no'}>${s.done?'✓':'○'}</td><td><b>${e(s.title)}</b>${s.required?'':' <span class=chip>recommended</span>'}</td><td>${e(s.how)}</td></tr>`).join('');
 const b=v.baa||{};$('baa').innerHTML=b.signedAt?`Signed <b>${e(b.signedAt)}</b> by <b>${e(b.signer)}</b> (recorded by ${e(b.recordedBy)}).`:
  (ADMIN?`Not recorded yet. Date signed <input type=date id=baaDate> Signed by <input id=baaSigner placeholder="Name, title"> <button id=baaSave>Record</button> <span class=msg id=baaMsg></span>`:'Not recorded yet. Your clinic admin records it here once signed.');
 if(!b.signedAt&&ADMIN)$('baaSave').onclick=async()=>{try{draw(await j('/api/clinic-setup/baa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({signedAt:$('baaDate').value,signer:$('baaSigner').value})}))}catch(x){$('baaMsg').innerHTML='<span class=err>'+e(x.message)+'</span>'}};
 const rows=[...Object.keys(v.types).map(t=>({key:'type:'+t,label:'Any '+v.types[t]+' payer',type:t,def:true})),...v.payersSeen.map(p=>({key:p.name,label:p.name,type:p.type,claims:p.claims}))];
 const known=new Set(rows.map(r=>r.key));Object.keys(v.appeal).concat(Object.keys(v.filing)).forEach(k=>{if(!known.has(k)){known.add(k);rows.push({key:k,label:k,type:''})}});
 $('days').innerHTML='<tr><th>Payer</th><th>Kind</th><th class=n>Claims seen</th><th class=n>Appeal window (days)</th><th class=n>Filing limit (days)</th></tr>'+rows.map(r=>`<tr><td>${e(r.label)}</td><td>${e(v.types[r.type]||'')}</td><td class=n>${r.claims||''}</td>
 <td class=n><input class=d data-k="${e(r.key)}" data-t=appeal value="${v.appeal[r.key]??''}" placeholder="${r.type?v.defaults.appeal[r.type]:''}"></td>
 <td class=n><input class=d data-k="${e(r.key)}" data-t=filing value="${v.filing[r.key]??''}" placeholder="${r.type?v.defaults.filing[r.type]:''}"></td></tr>`).join('');
 $('rates').innerHTML=v.contracts.length?'<tr><th>Payer</th><th class=n>Rates</th><th></th></tr>'+v.contracts.map(c=>`<tr><td>${e(c.payer)}</td><td class=n>${c.rates.toLocaleString()}</td><td><button data-rm="${e(c.payer)}">Remove</button></td></tr>`).join(''):'<tr><td>No contracted rates yet.</td></tr>';
 document.querySelectorAll('[data-rm]').forEach(x=>x.onclick=async()=>{if(!confirm('Remove the rates for '+x.dataset.rm+'?'))return;draw(await j('/api/clinic-setup/contracts/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({payer:x.dataset.rm})}))});
 $('payerPick').innerHTML='<option value="">Payer is in the file</option>'+v.payersSeen.map(p=>`<option>${e(p.name)}</option>`).join('');
 const npis=[...new Set([...v.npisSeen.map(n=>n.npi),...Object.keys(v.sites)])];
 $('sites').innerHTML=npis.length?'<tr><th>Billing NPI</th><th class=n>Claims seen</th><th>Name</th></tr>'+npis.map(n=>`<tr><td>${e(n)}</td><td class=n>${(v.npisSeen.find(x=>x.npi==n)||{}).claims||''}</td><td><input data-npi="${e(n)}" value="${e(v.sites[n]||'')}" placeholder="e.g. Kent Street"></td></tr>`).join(''):'<tr><td>No payment files yet.</td></tr>';}
$('saveDays').onclick=async()=>{const a={},f={};document.querySelectorAll('input.d').forEach(i=>{if(i.value.trim())(i.dataset.t=='appeal'?a:f)[i.dataset.k]=i.value.trim()});
 try{draw(await j('/api/clinic-setup/deadlines',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({appeal:a,filing:f,confirm:$('confirm').checked})}));$('daysMsg').textContent='Saved.'}catch(x){$('daysMsg').innerHTML='<span class=err>'+e(x.message)+'</span>'}};
$('up').onsubmit=async ev=>{ev.preventDefault();const fd=new FormData(ev.target);if(!ev.target.replace.checked)fd.set('replace','0');
 try{const v=await j('/api/clinic-setup/contracts',{method:'POST',body:fd});draw(v);$('upMsg').textContent='Uploaded.'+(v.problems&&v.problems.length?' Skipped: '+v.problems.slice(0,5).join('; '):'')}catch(x){$('upMsg').innerHTML='<span class=err>'+e(x.message)+'</span>'}};
$('saveSites').onclick=async()=>{const s={};document.querySelectorAll('[data-npi]').forEach(i=>{if(i.value.trim())s[i.dataset.npi]=i.value.trim()});
 try{draw(await j('/api/clinic-setup/sites',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sites:s})}));$('sitesMsg').textContent='Saved.'}catch(x){$('sitesMsg').innerHTML='<span class=err>'+e(x.message)+'</span>'}};
j('/api/clinic-setup').then(draw).catch(x=>document.querySelector('main').insertAdjacentHTML('beforeend','<p class=err>'+e(x.message)+'</p>'));
</script></main></body></html>"""
