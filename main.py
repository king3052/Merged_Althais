import os, re
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from groq import Groq
import json
from ncci_checker import check_claim_ncci_flags

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# llama-3.3-70b-versatile was deprecated by Groq (announced June 17, 2026) —
# this is Groq's own recommended replacement. Kept as a single constant
# rather than hardcoded in every call so the next migration is a one-line
# change instead of a grep-and-replace across the file.
GROQ_MODEL = "openai/gpt-oss-120b"

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/videos", StaticFiles(directory="public/videos"), name="videos")

templates = Jinja2Templates(directory="templates")

from auth import (
    router as auth_router, current_user, require_user, COOKIE_NAME,
    current_admin, require_biller, require_admin_role,
    get_db, Base, engine, _org_namespace, OrgPatient, OrgClaim,
)
from sqlalchemy.orm import Session
from sqlalchemy import select as sa_select
import datetime as dt
from datetime import timezone
from marketing_data import SOLUTION_SEGMENTS, RESOURCE_CATEGORIES, RESOURCE_ARTICLES
from coding_rules import resolve_time_based_codes, is_governed_code
from code_validation import validate_codes
app.include_router(auth_router)


# ── Marketing / product site (Jinja2, shared nav+footer via base.html) ───────
def _marketing_ctx(user, active_page: str) -> dict:
    return {"user": user, "active_page": active_page}


@app.get("/product")
async def product_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "product.html", _marketing_ctx(user, "product"))


@app.get("/solutions")
async def solutions_page(request: Request, user=Depends(current_user)):
    ctx = _marketing_ctx(user, "solutions")
    ctx["segments"] = SOLUTION_SEGMENTS
    return templates.TemplateResponse(request, "solutions.html", ctx)


@app.get("/how-it-works")
async def how_it_works_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "how_it_works.html", _marketing_ctx(user, "how-it-works"))


@app.get("/pricing")
async def pricing_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "pricing.html", _marketing_ctx(user, "pricing"))


@app.get("/resources")
async def resources_page(request: Request, user=Depends(current_user)):
    ctx = _marketing_ctx(user, "resources")
    ctx["categories"] = RESOURCE_CATEGORIES
    ctx["articles"] = [a for a in RESOURCE_ARTICLES if not a.get("featured")]
    ctx["featured"] = next(a for a in RESOURCE_ARTICLES if a.get("featured"))
    return templates.TemplateResponse(request, "resources.html", ctx)


@app.get("/about")
async def about_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "about.html", _marketing_ctx(user, "about"))


@app.get("/legal/privacy")
async def privacy_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "privacy.html", _marketing_ctx(user, ""))


@app.get("/legal/terms")
async def terms_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(request, "terms.html", _marketing_ctx(user, ""))


# ── Password reset pages ──────────────────────────────────────────────────
@app.get("/forgot-password")
async def forgot_password_page():
    with open("templates/forgot_password.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/reset-password")
async def reset_password_page():
    with open("templates/reset_password.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/demo")
async def demo_page():
    with open("templates/demo.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ── Admin pages ──────────────────────────────────────────────────────────────
@app.get("/admin/login")
async def admin_login_page():
    with open("templates/admin_login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/admin")
async def admin_page(request: Request):
    # Not an admin → bounce to admin login
    if not current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    with open("templates/admin_dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/logout")
async def logout():
    """Clear the session cookie and serve the signed-out page."""
    with open("templates/logout.html", "r", encoding="utf-8") as f:
        content = f.read()
    resp = HTMLResponse(content=content)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/logout")
async def logout_get():
    """Handle direct GET visits to /logout (e.g. typing the URL)."""
    with open("templates/logout.html", "r", encoding="utf-8") as f:
        content = f.read()
    resp = HTMLResponse(content=content)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def _org_namespace(user) -> str:
    """
    Build a safe localStorage namespace from the user's organization.
    Falls back to their email if no org is set.
    e.g. "Mercy Cardiology" -> "org_mercy_cardiology"
         "doc@clinic.com"   -> "org_doc_clinic_com"
    """
    base = (user.organization or user.email or "unknown").strip().lower()
    safe = re.sub(r'[^a-z0-9]+', '_', base).strip('_')
    return f"org_{safe}"


def _render_emr(user) -> HTMLResponse:
    """Read dashboard.html (the clinical EMR workspace) and stamp the org namespace into it."""
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()
    ns = _org_namespace(user)

    # 1. Replace the template placeholder (works when dashboard has {{ORG_NS}})
    html = html.replace("{{ORG_NS}}", ns)

    # 2. Also inject the namespace directly into the JS so it works even if
    #    the template placeholder is missing (e.g. after a partial file upload).
    #    We patch the _NS constant to always use the correct org namespace.
    ns_patch = f"""
<script>
  // Server-injected namespace patch — overrides any _NS fallback in the dashboard JS.
  window.__ALTHAIS_NS__ = "{ns}";
  // User role — controls which UI elements are shown/hidden
  window.__ALTHAIS_ROLE__ = "{user.role or 'admin'}";
  // Links this login to a provider identity (e.g. "Dr. R. Patel") used
  // throughout appointments/claims data — blank for accounts that aren't a
  // specific provider. Lets Althea's "what's my schedule" filter to just
  // this person instead of showing everyone's.
  window.__ALTHAIS_PROVIDER_NAME__ = {json.dumps(user.provider_name or "")};
  // Also override localStorage to intercept any local:: keys and redirect them
  // to the correct org namespace so existing data migrates automatically.
  (function() {{
    var _real = window.localStorage;
    var _ns = "{ns}::";
    var _old = "local::";
    function _fix(k) {{ return (typeof k === "string" && k.startsWith(_old)) ? _ns + k.slice(_old.length) : k; }}
    var _proxy = {{
      getItem: function(k) {{
        var v = _real.getItem(_fix(k));
        if (v === null) v = _real.getItem(k);
        return v;
      }},
      setItem: function(k, v) {{ _real.setItem(_fix(k), v); }},
      removeItem: function(k) {{ _real.removeItem(_fix(k)); _real.removeItem(k); }},
      clear: _real.clear.bind(_real),
      key: _real.key.bind(_real),
      get length() {{ return _real.length; }}
    }};
    try {{ Object.defineProperty(window, 'localStorage', {{ get: function() {{ return _proxy; }}, configurable: true }}); }} catch(e) {{}}
  }})();
  // Role helpers available globally in the dashboard
  window.__ALTHAIS_CAN__ = {{
    edit:   ['admin','biller'].includes(window.__ALTHAIS_ROLE__),
    submit: ['admin','biller'].includes(window.__ALTHAIS_ROLE__),
    manage: window.__ALTHAIS_ROLE__ === 'admin',
  }};
</script>"""

    # Inject right after <head> so it runs before everything else
    html = html.replace("<head>", "<head>" + ns_patch, 1)

    # Stack Althais's top-level workspace switcher (Overview/EMR/Revenue/Staff)
    # above the EMR's own header, so a provider deep in a patient chart can
    # still jump straight to another workspace without backing out first.
    workspace_bar = """
<div class="bg-med-700 text-white" style="border-bottom:1px solid rgba(255,255,255,0.15);">
  <div class="flex items-center h-10 px-3">
    <span class="text-[13px] font-semibold tracking-[0.18em] mr-6 opacity-90 select-none">ALTHAIS</span>
    <nav class="flex items-center h-full overflow-x-auto">
      <a href="/overview" class="althais-ptab">Overview</a>
      <a href="/emr" class="althais-ptab althais-ptab-active">EMR</a>
      <a href="/revenue/claims" class="althais-ptab">Revenue</a>
      <a href="/staff/team" class="althais-ptab">Staff</a>
    </nav>
  </div>
</div>
<style>
  .althais-ptab { padding:0 12px; height:40px; display:flex; align-items:center; color:rgba(255,255,255,0.68); font-weight:500; font-size:13px; border-bottom:2px solid transparent; text-decoration:none; white-space:nowrap; }
  .althais-ptab:hover { color:#fff; }
  .althais-ptab-active { color:#fff; font-weight:700; border-bottom-color:#fff; }
</style>
<script>
  // The EMR's own 3-column layout is height-locked to calc(100vh - 92px) to
  // fit exactly under its own header + tab bar. Adding the 40px workspace
  // bar above it needs that budget subtracted too, or the layout overflows
  // the viewport by 40px.
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('div[style*="calc(100vh - 92px)"]').forEach(function (el) {
      el.style.height = 'calc(100vh - 132px)';
    });
  });
</script>"""
    html = html.replace('<body class="bg-shell text-ink-800">', '<body class="bg-shell text-ink-800">' + workspace_bar, 1)

    return HTMLResponse(content=html)


@app.get("/")
async def root(request: Request, user=Depends(current_user)):
    # Logged-in users go straight to the overview workspace, others see the landing page
    if user:
        return RedirectResponse(url="/overview", status_code=302)
    with open("templates/landing.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/login")
async def login(request: Request, user=Depends(current_user)):
    if user:
        return RedirectResponse(url="/overview", status_code=302)
    with open("templates/login.html", "r", encoding="utf-8") as f:
        content = f.read()
    # Inject forgot password link — works even if the HTML file is an older version
    forgot_patch = """<script>
  document.addEventListener('DOMContentLoaded', function() {
    // Find the password label and inject "Forgot password?" link next to it
    var labels = document.querySelectorAll('label');
    labels.forEach(function(label) {
      if (label.textContent.trim() === 'Password') {
        var wrapper = document.createElement('div');
        wrapper.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;width:100%';
        label.style.margin = '0';
        label.parentNode.insertBefore(wrapper, label);
        wrapper.appendChild(label);
        var link = document.createElement('a');
        link.href = '/forgot-password';
        link.textContent = 'Forgot Password';
        link.id = 'forgot-link';
        link.style.cssText = 'font-size:11px;color:#fff;text-decoration:underline;display:none';
        link.onmouseover = function(){ this.style.textDecoration='underline'; };
        link.onmouseout  = function(){ this.style.textDecoration='none'; };
        wrapper.appendChild(link);
      }
    });
    // Show the link only in login mode
    var origSetMode = window.setMode;
    if (typeof origSetMode === 'function') {
      window.setMode = function(m) {
        origSetMode(m);
        var fl = document.getElementById('forgot-link');
        if (fl) fl.style.display = (m === 'login') ? 'inline' : 'none';
      };
      // Show on initial load (login mode)
      var fl = document.getElementById('forgot-link');
      if (fl) fl.style.display = 'inline';
    }
  });
</script>"""
    content = content.replace('</body>', forgot_patch + '\n</body>')
    return HTMLResponse(content=content)


def _app_user_json(user) -> str:
    return json.dumps({
        "full_name": user.full_name or "",
        "email": user.email,
        "role": user.role or "admin",
        "organization": user.organization or "",
        "provider_name": user.provider_name or "",
    })


@app.get("/onboarding")
async def onboarding(request: Request, user=Depends(current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    # Already done — skip straight to the app
    if getattr(user, "onboarding_complete", 1):
        return RedirectResponse(url="/overview", status_code=302)
    return templates.TemplateResponse(request, "onboarding.html", {"user": user})


@app.get("/emr")
async def emr(request: Request, user=Depends(current_user)):
    """The full clinical EMR workspace (patient chart SPA) — unchanged, reached from
    the EMR workspace's sub-pages for deep patient-record functionality."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return _render_emr(user)


# ── Workspace pages (two-level nav: Overview / EMR / Revenue / Staff) ──────────
# Every workspace sub-page is a simple auth-gated template render, so a single
# factory registers them all instead of repeating the same handler by hand.
_WORKSPACE_PAGES = {
    # Overview
    "/overview": "overview_dashboard.html",
    "/overview/inbox": "overview_inbox.html",
    "/overview/activity": "overview_activity.html",
    # EMR (top-level workspace pages; the full patient-chart SPA stays at /emr)
    "/emr/patients": "patients.html",
    "/emr/schedule": "schedule.html",
    # Staff
    "/staff/team": "practice_team.html",
    "/staff/onboarding": "practice_onboarding.html",
    "/staff/credentials": "practice_credentials.html",
    "/staff/compliance": "practice_compliance.html",
    "/staff/training": "practice_training.html",
    "/staff/roles": "practice_roles.html",
}


def _workspace_route(template_name: str):
    async def handler(request: Request, user=Depends(current_user)):
        if not user:
            verified = request.query_params.get("verified")
            login_url = "/login" + (f"?verified={verified}" if verified else "")
            return RedirectResponse(url=login_url, status_code=302)
        # First-login check — invited users who haven't completed onboarding
        # yet get redirected to the setup screen regardless of which page
        # they try to reach. Covers all workspace pages in one place.
        if not getattr(user, "onboarding_complete", 1):
            return RedirectResponse(url="/onboarding", status_code=302)
        ctx = {
            "user": user,
            "verified": request.query_params.get("verified"),
            "user_json": _app_user_json(user),
        }
        return templates.TemplateResponse(request, template_name, ctx)

    return handler


for _path, _template in _WORKSPACE_PAGES.items():
    app.add_api_route(_path, _workspace_route(_template), methods=["GET"])


# ── Polished-placeholder pages ─────────────────────────────────────────────
# These workspace pages don't have dedicated functionality yet — the real
# feature lives inside the EMR SPA (dashboard.html) today. Each one renders
# the same shell (header/nav) and links through to the equivalent EMR view.
_PLACEHOLDER_PAGES = {
    "/overview/tasks": dict(
        title="Tasks", subtitle="Action items and to-dos across the practice.",
        description="A unified task list is on the way — assign follow-ups, track due dates, "
                     "and clear your queue without leaving Althais.",
    ),
    "/overview/analytics": dict(
        title="Analytics", subtitle="Practice-wide performance at a glance.",
        description="Deeper practice analytics — visit volume, revenue trends, and provider "
                     "productivity — are coming soon.",
        emr_link="/overview", emr_link_label="View Revenue Snapshot on Dashboard",
    ),
    "/emr/encounters": dict(
        title="Encounters", subtitle="Visit history and encounter timeline across all patients.",
        description="A dedicated encounters timeline is coming soon. For now, encounter history "
                     "lives within each patient's chart in the EMR.",
        emr_link="/emr#patients-view", emr_link_label="Open Patients in EMR",
    ),
    "/emr/charting": dict(
        title="Charting", subtitle="Clinical documentation and SOAP notes.",
        description="A standalone charting workspace is coming soon. Clinical notes are currently "
                     "documented directly within each patient's chart in the EMR.",
        emr_link="/emr#patients-view", emr_link_label="Open EMR",
    ),
    "/emr/second-brain": dict(
        title="AI Second Brain", subtitle="Real-time clinical documentation support during encounters.",
        description="Althea's in-encounter Second Brain is coming to this workspace soon. "
                     "Practice-wide AI recommendations are already available today.",
        emr_link="/overview#copilot", emr_link_label="View AI Practice Copilot",
    ),
    "/emr/documents": dict(
        title="Documents", subtitle="Faxes, uploads, and patient documents.",
        description="A dedicated document library for this workspace is coming soon. Documents "
                     "are currently available within the EMR.",
        emr_link="/emr#documents-view", emr_link_label="Open Documents in EMR",
    ),
    "/revenue/claims": dict(
        title="Claims", subtitle="Submission-ready and in-flight claims across the practice.",
        description="A dedicated claims workspace for this view is coming soon. Claims management "
                     "is currently available in the EMR.",
        emr_link="/emr#claims", emr_link_label="Open Claims in EMR",
    ),
    "/revenue/coding": dict(
        title="Coding", subtitle="AI-assisted CPT/ICD-10 coding queue.",
        description="A dedicated coding queue is coming soon. Coding suggestions are currently "
                     "generated within each claim in the EMR.",
        emr_link="/emr#claims", emr_link_label="Open Claims in EMR",
    ),
    "/revenue/denials": dict(
        title="Denials", subtitle="Denied claims requiring review or appeal.",
        description="A dedicated denials workspace for this view is coming soon. Denials are "
                     "currently available in the EMR.",
        emr_link="/emr#denials-view", emr_link_label="Open Denials in EMR",
    ),
    "/revenue/appeals": dict(
        title="Appeals", subtitle="Appeal letters and resubmission tracking.",
        description="A dedicated appeals tracker is coming soon. Appeal generation is currently "
                     "available from each denied claim in the EMR.",
        emr_link="/emr#denials-view", emr_link_label="Open Denials in EMR",
    ),
    "/revenue/payments": dict(
        title="Payments", subtitle="Reimbursements and payment history.",
        description="A dedicated payments ledger is coming soon. A summary of recent payments is "
                     "available today on the Overview dashboard.",
        emr_link="/overview#revenue", emr_link_label="View Revenue Snapshot",
    ),
    "/staff/clinic-onboarding": dict(
        title="Clinic Onboarding", subtitle="Set up a new clinic on Althais.",
        description="Guided clinic setup — locations, payer enrollment, and practice "
                     "configuration — is coming soon.",
    ),
    "/revenue/payer-intelligence": dict(
        title="Payer Intelligence", subtitle="Payer policy updates and reimbursement trends.",
        description="A dedicated payer intelligence workspace for this view is coming soon. "
                     "Payer Intelligence is currently available in the EMR.",
        emr_link="/emr#payer-view", emr_link_label="Open Payer Intelligence in EMR",
    ),
}


def _placeholder_route(config: dict):
    async def handler(request: Request, user=Depends(current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        ctx = {
            "user": user,
            "user_json": _app_user_json(user),
            "page_title": config["title"],
            "page_subtitle": config["subtitle"],
            "page_description": config["description"],
            "emr_link": config.get("emr_link"),
            "emr_link_label": config.get("emr_link_label"),
        }
        return templates.TemplateResponse(request, "_placeholder_page.html", ctx)

    return handler


for _path, _config in _PLACEHOLDER_PAGES.items():
    app.add_api_route(_path, _placeholder_route(_config), methods=["GET"])


# ── Backward-compatible redirects from the old single-row nav's routes ────────
_LEGACY_REDIRECTS = {
    "/dashboard": "/overview",
    "/schedule": "/emr/schedule",
    "/patients": "/emr/patients",
    "/practice/team": "/staff/team",
    "/practice/onboarding": "/staff/onboarding",
    "/practice/compliance": "/staff/compliance",
    "/practice/credentials": "/staff/credentials",
    "/practice/training": "/staff/training",
    "/practice/roles": "/staff/roles",
}


def _legacy_redirect(target: str):
    async def handler(request: Request):
        qs = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"{target}{qs}", status_code=302)

    return handler


for _old_path, _new_path in _LEGACY_REDIRECTS.items():
    app.add_api_route(_old_path, _legacy_redirect(_new_path), methods=["GET"])


def _patient_dict(p: OrgPatient) -> dict:
    return {
        "name": p.name, "mrn": p.mrn, "dob": p.dob,
        "sex": p.sex, "payer": p.payer, "provider": p.provider,
    }


def _claim_dict(c: OrgClaim) -> dict:
    return {
        "id": c.claim_id, "patientName": c.patient_name, "mrn": c.mrn,
        "payer": c.payer, "codes": json.loads(c.codes or "[]"),
        "note": c.note, "amount": float(c.amount or 0),
        "status": c.status, "score": c.score,
        "flags": json.loads(c.flags or "[]"),
        "appealLetter": c.appeal_letter,
        "createdAt": c.created_at.isoformat() if c.created_at else "",
    }


# ── Patient CRUD ──────────────────────────────────────────────────────────────

@app.get("/api/patients")
def api_list_patients(user=Depends(require_user), db: Session = Depends(get_db)):
    org = _org_namespace(user)
    rows = db.scalars(sa_select(OrgPatient).where(OrgPatient.org_key == org).order_by(OrgPatient.name)).all()
    return [_patient_dict(r) for r in rows]


@app.post("/api/patients")
async def api_save_patient(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    body = await request.json()
    org = _org_namespace(user)
    mrn = (body.get("mrn") or "").strip()
    if not mrn:
        return JSONResponse({"error": "MRN is required"}, status_code=400)
    row = db.scalar(sa_select(OrgPatient).where(OrgPatient.org_key == org, OrgPatient.mrn == mrn))
    if row:
        row.name     = body.get("name", row.name)
        row.dob      = body.get("dob", row.dob)
        row.sex      = body.get("sex", row.sex)
        row.payer    = body.get("payer", row.payer)
        row.provider = body.get("provider", row.provider)
    else:
        db.add(OrgPatient(
            org_key=org, mrn=mrn,
            name=body.get("name", ""), dob=body.get("dob", ""),
            sex=body.get("sex", ""), payer=body.get("payer", ""),
            provider=body.get("provider", ""),
        ))
    db.commit()
    return {"ok": True}


@app.delete("/api/patients/{mrn}")
def api_delete_patient(mrn: str, user=Depends(require_biller), db: Session = Depends(get_db)):
    org = _org_namespace(user)
    row = db.scalar(sa_select(OrgPatient).where(OrgPatient.org_key == org, OrgPatient.mrn == mrn))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ── Claim CRUD ────────────────────────────────────────────────────────────────

@app.get("/api/claims")
def api_list_claims(user=Depends(require_user), db: Session = Depends(get_db)):
    org = _org_namespace(user)
    rows = db.scalars(sa_select(OrgClaim).where(OrgClaim.org_key == org).order_by(OrgClaim.created_at.desc())).all()
    return [_claim_dict(r) for r in rows]


@app.post("/api/claims")
async def api_save_claim(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    body = await request.json()
    org = _org_namespace(user)
    cid = (body.get("id") or body.get("claim_id") or "").strip()
    if not cid:
        return JSONResponse({"error": "claim id required"}, status_code=400)
    row = db.scalar(sa_select(OrgClaim).where(OrgClaim.org_key == org, OrgClaim.claim_id == cid))
    if row:
        row.patient_name  = body.get("patientName", row.patient_name)
        row.mrn           = body.get("mrn", row.mrn)
        row.payer         = body.get("payer", row.payer)
        row.codes         = json.dumps(body.get("codes", json.loads(row.codes or "[]")))
        row.note          = body.get("note", row.note)
        row.amount        = body.get("amount", row.amount)
        row.status        = body.get("status", row.status)
        row.score         = body.get("score", row.score)
        row.flags         = json.dumps(body.get("flags", json.loads(row.flags or "[]")))
        row.appeal_letter = body.get("appealLetter", row.appeal_letter)
    else:
        created_str = body.get("createdAt", "")
        try:
            created = dt.datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else dt.datetime.now(timezone.utc)
        except Exception:
            created = dt.datetime.now(timezone.utc)
        db.add(OrgClaim(
            org_key=org, claim_id=cid,
            patient_name=body.get("patientName", ""), mrn=body.get("mrn", ""),
            payer=body.get("payer", ""), codes=json.dumps(body.get("codes", [])),
            note=body.get("note", ""), amount=body.get("amount", 0),
            status=body.get("status", "Draft"), score=body.get("score"),
            flags=json.dumps(body.get("flags", [])),
            appeal_letter=body.get("appealLetter", ""), created_at=created,
        ))
    db.commit()
    return {"ok": True}


@app.delete("/api/claims/{claim_id}")
def api_delete_claim(claim_id: str, user=Depends(require_biller), db: Session = Depends(get_db)):
    org = _org_namespace(user)
    row = db.scalar(sa_select(OrgClaim).where(OrgClaim.org_key == org, OrgClaim.claim_id == claim_id))
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.post("/api/code-note")
async def code_note(request: Request, user=Depends(require_biller)):
    try:
        data = await request.json()
        # Defaults to ER/urgent care since that's the primary target segment,
        # but any clinic can override this per-request once the frontend
        # passes a specialty/place-of-service value.
        specialty = data.get('specialty') or 'emergency department / urgent care'

        # Structured visit-duration inputs — kept deliberately separate from
        # the free-text note. Time-based billing (critical care, time-based
        # office E/M) is governed by exact numeric thresholds a payer audit
        # checks literally, so it's resolved with plain arithmetic in
        # coding_rules.py rather than left to the AI to read out of prose.
        duration_minutes = data.get('duration_minutes')
        try:
            duration_minutes = int(duration_minutes) if duration_minutes not in (None, '') else None
        except (TypeError, ValueError):
            duration_minutes = None
        encounter_type = data.get('encounter_type') or 'emergency'
        is_new_patient = bool(data.get('is_new_patient', True))
        is_critical_care = bool(data.get('is_critical_care', False))

        prompt = f"""You are an expert medical coder certified in ICD-10-CM and CPT coding, working a {specialty} encounter.

Given the clinical note below, return a JSON object shaped exactly like this:
{{"codes": [
  {{
    "code": "string, e.g. 99284 or S61.409A",
    "description": "short plain-English description of the code",
    "type": "ICD-10 or CPT",
    "confidence": integer 0-100,
    "justification": "one sentence citing the specific documentation that supports this code",
    "modifier": "CPT modifier if one clearly applies (e.g. 25, 59, 50), else empty string",
    "units": integer, default 1,
    "documentation_gap": "if the note doesn't fully support this code's specificity or level, name exactly what documentation is missing; otherwise empty string"
  }}
]}}

Hard rules — these are the most common ways an AI coder produces an unbillable or denial-prone claim, so follow them exactly:
1. ALWAYS include at least one CPT code representing the billable service performed (an E/M visit level, a procedure, an interpretation, etc.) whenever the note describes a billable encounter. Never return ICD-10 diagnosis codes alone — a claim with no procedure code cannot be billed.
2. When selecting an E/M level (99281-99285 for ED, 99202-99215 for office/outpatient), base the level on 2023+ Medical Decision Making (MDM) guidelines: number/complexity of problems addressed, amount/complexity of data reviewed, and risk of complications. Your justification for any E/M code must name the specific MDM element(s) that support the level — not just restate the chief complaint.
3. Choose the most specific ICD-10 code the documentation actually supports (laterality, episode of care, etc.). If the note lacks enough detail for a more specific code, select the correct less-specific code AND explain in documentation_gap what additional detail would justify the more specific one.
4. Do not guess when documentation is ambiguous. Lower confidence rather than inventing detail that isn't in the note — flagging a gap is always better than silently upcoding or downcoding.
5. Only include a modifier when the documentation clearly supports it (e.g. modifier 25 only when a significant, separately identifiable E/M service is documented alongside a same-day procedure).

Worked examples (follow this reasoning style):

Example 1 — straightforward, single diagnosis:
Note: "34F, 3 days nonproductive cough and congestion, no fever, no dyspnea. Lungs clear bilaterally. No distress."
Correct output reasoning: Single self-limited problem, no data reviewed beyond exam, minimal risk -> lowest ED E/M level. Diagnosis is clear and specific enough as documented.
{{"codes": [
  {{"code": "J06.9", "description": "Acute upper respiratory infection, unspecified", "type": "ICD-10", "confidence": 92, "justification": "Cough and congestion without a more specific source documented", "modifier": "", "units": 1, "documentation_gap": ""}},
  {{"code": "99282", "description": "ED visit, low complexity", "type": "CPT", "confidence": 90, "justification": "MDM: single self-limited problem, minimal data reviewed, minimal risk", "modifier": "", "units": 1, "documentation_gap": ""}}
]}}

Example 2 — thin documentation, laterality not specified:
Note: "Twisted ankle playing basketball yesterday. Tender, swollen. Able to bear weight. X-ray negative for fracture."
Correct output reasoning: Laterality (left/right) is never stated, so the ICD-10 code cannot be fully specific — flag the gap instead of guessing a side.
{{"codes": [
  {{"code": "S93.40", "description": "Sprain of ankle, unspecified side", "type": "ICD-10", "confidence": 70, "justification": "Ankle sprain confirmed by exam and negative X-ray", "modifier": "", "units": 1, "documentation_gap": "Note doesn't specify which ankle (left/right) — needed for full code specificity"}},
  {{"code": "99282", "description": "ED visit, low complexity", "type": "CPT", "confidence": 88, "justification": "MDM: single acute uncomplicated problem, X-ray reviewed, minimal risk", "modifier": "", "units": 1, "documentation_gap": ""}}
]}}

Example 3 — procedure plus same-day E/M needing a modifier:
Note: "4cm forearm laceration from glass, no tendon involvement. Wound irrigated, simple repair with 6 sutures. Separate visit component for unrelated evaluation of chest congestion also documented."
Correct output reasoning: The repair is its own CPT code; the E/M is billed separately only because a distinct, unrelated problem was also evaluated the same day — that's exactly when modifier 25 applies.
{{"codes": [
  {{"code": "S51.812A", "description": "Laceration without foreign body of left forearm, initial encounter", "type": "ICD-10", "confidence": 85, "justification": "Forearm laceration explicitly documented", "modifier": "", "units": 1, "documentation_gap": ""}},
  {{"code": "12002", "description": "Simple repair, 2.6cm-7.5cm", "type": "CPT", "confidence": 90, "justification": "4cm simple laceration repair with sutures, no tendon involvement", "modifier": "", "units": 1, "documentation_gap": ""}},
  {{"code": "99282", "description": "ED visit, low complexity", "type": "CPT", "confidence": 85, "justification": "MDM: separately evaluated unrelated problem (chest congestion) same visit", "modifier": "25", "units": 1, "documentation_gap": ""}}
]}}

Respond with the JSON object only — no markdown, no backticks, no commentary before or after it.
{('Documented visit duration: ' + str(duration_minutes) + ' minutes' + (' (critical care)' if is_critical_care else '') + '. Note: the exact E/M or critical-care code for this duration will be verified and corrected by a deterministic rules engine after your response, so focus your own code selection on everything else — diagnoses, procedures, modifiers.') if duration_minutes else ''}

Clinical note:
{data['note']}"""

        # Ask for Groq's native Structured Outputs as an added reliability
        # layer on top of the existing regex-based extraction below — NOT a
        # replacement for it. There's a documented, current community report
        # of openai/gpt-oss-120b sometimes ignoring response_format entirely
        # and returning free-form text anyway, so the regex fallback stays
        # as the real safety net regardless of whether this is honored.
        code_schema = {
            "type": "object",
            "properties": {
                "codes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "description": {"type": "string"},
                            "type": {"type": "string", "enum": ["ICD-10", "CPT"]},
                            "confidence": {"type": "integer"},
                            "justification": {"type": "string"},
                            "modifier": {"type": "string"},
                            "units": {"type": "integer"},
                            "documentation_gap": {"type": "string"},
                        },
                        "required": ["code", "description", "type", "confidence", "justification", "modifier", "units", "documentation_gap"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["codes"],
            "additionalProperties": False,
        }

        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,  # coding should be conservative/consistent, not creative
                response_format={"type": "json_schema", "json_schema": {"name": "coding_response", "strict": True, "schema": code_schema}},
            )
        except Exception:
            # Some models/accounts don't support json_schema mode — fall back
            # to a plain call and rely entirely on the regex extraction below.
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )

        text = response.choices[0].message.content.strip()
        # Extract the JSON object even if the model wraps it in commentary or
        # code fences despite instructions, or ignores response_format
        # altogether (see the note above) — more robust than assuming a
        # specific fence format or trusting response_format was honored.
        match = re.search(r'\{.*\}', text, re.S)
        if match:
            text = match.group(0)
        result = json.loads(text)
        # Defensive: accept either the requested {"codes": [...]} shape or a
        # bare array, in case the model ignores the wrapping instruction.
        codes = result.get('codes', []) if isinstance(result, dict) else (result if isinstance(result, list) else [])

        # Deterministic time-based override. Duration billing (critical care,
        # time-based office E/M) is decided by exact minute thresholds, not
        # AI judgment — see coding_rules.py for why ED visits are deliberately
        # excluded from this override.
        time_coding_note = None
        if duration_minutes:
            time_codes, time_coding_note = resolve_time_based_codes(
                duration_minutes, encounter_type, is_new_patient, is_critical_care
            )
            if time_codes:
                # Drop any AI-suggested code this engine governs, so the
                # biller isn't shown two different, unreconciled E/M levels.
                codes = [c for c in codes if not is_governed_code(str(c.get('code', '')).strip())]
                codes.extend(time_codes)

        # Format-validate every code (see code_validation.py for exactly what
        # this does and doesn't check) — annotates rather than removes, so a
        # flagged code is still visible to the biller, just clearly marked.
        codes = validate_codes(codes)

        response_body = {"codes": codes}
        if time_coding_note:
            response_body["time_coding_note"] = time_coding_note
        return JSONResponse(response_body)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/validate-claim")
async def validate_claim(request: Request, user=Depends(require_biller)):
    try:
        data = await request.json()
        prompt = f"""You are a medical billing compliance expert reviewing a claim before submission.

Check the codes below against these specific things, not just a general impression:
1. Medical necessity linkage — does at least one ICD-10 code on the claim actually justify each CPT/procedure code? A CPT code with no supporting diagnosis is a near-certain denial.
2. E/M level support — if an E/M code is present, does the note's documented Medical Decision Making (problems addressed, data reviewed, risk) actually support that level, or is it over/under-coded relative to what's documented?
3. Missing modifiers — flag if a modifier is likely required (e.g. modifier 25 for a same-day E/M plus procedure) but absent.
4. Specificity — flag any ICD-10 code billed as "unspecified" when the note contains enough detail to code more specifically.
5. Missing demographic/administrative fields required for a clean claim (patient DOB, payer, rendering provider, service date).
6. Possible NCCI bundling conflict — flag if two CPT codes on this claim are ones you know are commonly bundled (not separately payable together) under Medicare's National Correct Coding Initiative edits, unless an appropriate modifier (e.g. 59, XE, XS, XU) is already present to justify billing them separately. Base this on your own coding knowledge — you do NOT have access to the live, current-quarter NCCI edit table, so word any such flag as "possible" and recommend the biller verify against the current NCCI edits before treating it as certain.

Return a JSON object with exactly these fields:
- score (integer 0-100, overall claim readiness — be strict, not generous)
- flags (array of strings, each a specific issue found, referencing the code involved)
- missing (array of strings, each a specific thing that's absent and required)

Respond with the JSON object only. No markdown, no backticks, no commentary.

Codes: {json.dumps(data['codes'])}
Clinical note: {data.get('note', '')}"""

        validation_schema = {
            "type": "object",
            "properties": {
                "score": {"type": "integer"},
                "flags": {"type": "array", "items": {"type": "string"}},
                "missing": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "flags", "missing"],
            "additionalProperties": False,
        }

        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_schema", "json_schema": {"name": "validation_response", "strict": True, "schema": validation_schema}},
            )
        except Exception:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )

        text = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', text, re.S)
        if match:
            text = match.group(0)
        result = json.loads(text)

        # Real NCCI PTP edit check — deterministic lookup against the
        # CMS edit table, replacing any AI-guessed bundling flags with
        # actual authoritative answers for the pairs we have on file.
        cpt_codes = [
            c.get("code", "") for c in data.get("codes", [])
            if str(c.get("type", "")).upper().strip().startswith("CPT")
        ]
        ncci_flags = check_claim_ncci_flags(cpt_codes)
        if ncci_flags:
            existing_flags = result.get("flags", [])
            existing_flags = [
                f for f in existing_flags
                if "ncci" not in f.lower() and "bundl" not in f.lower()
            ]
            result["flags"] = existing_flags + ncci_flags
            hard_conflicts = sum(1 for f in ncci_flags if "no modifier" in f.lower())
            if hard_conflicts:
                result["score"] = max(0, result.get("score", 80) - (hard_conflicts * 15))

        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/appeal-letter")
async def appeal_letter(request: Request, user=Depends(require_biller)):
    try:
        data = await request.json()
        prompt = f"""You are an expert medical billing specialist drafting a formal insurance claim appeal letter.
        Write a professional, persuasive appeal letter for the denied claim described below.
        Structure it as a real business letter with today's date, payer's appeals department as recipient,
        a Re: line with the claim reference, medical-necessity justification referencing the CPT and ICD-10 codes,
        and a professional closing. 250-350 words. Return ONLY the letter text. No markdown.

        Patient name: {data.get('patient', '')}
        Patient MRN: {data.get('mrn', '')}
        Claim ID: {data.get('claimId', '')}
        Date of service: {data.get('svcDate', '')}
        Rendering provider: {data.get('provider', '')}
        Payer / insurer: {data.get('payer', '')}
        CPT / procedure code(s): {data.get('cpt', '')}
        ICD-10 diagnosis code(s): {data.get('icd', '')}
        Billed amount: {data.get('amount', '')}
        Denial reason / status: {data.get('status', '')}
        Practice / billing entity: {data.get('practice', 'Althais Health Systems')}"""
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        return JSONResponse({"letter": text.strip()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/althea")
async def althea_command(request: Request, user=Depends(require_user)):
    """
    Althea — a voice/text command interpreter scoped ONLY to this product's
    own functions (reading the schedule, a claims summary, opening a patient
    chart, reading back a patient's allergies/medications/labs, navigating
    to a section). This is deliberately NOT a general clinical assistant: it
    classifies a spoken/typed request into one of a small, fixed set of
    intents and extracts parameters — it never generates clinical content,
    diagnoses, or care recommendations, and any request for those is
    routed to "unknown" rather than answered.

    Conversational memory: the frontend sends a short rolling history of the
    last few exchanges (transcript + intent this endpoint returned), so a
    follow-up like "what about tomorrow" right after "what's my schedule
    today" can be resolved in context instead of needing every detail
    repeated. This endpoint stays stateless itself — the frontend is the
    one keeping the history and sending it back each time.

    Execution of the actual intent happens entirely in existing, already-
    built frontend code operating on real data already in the app (the
    appointments list, SAVED_CLAIMS, PATIENTS) — this endpoint only decides
    *which* of those existing functions to call, never generates the
    response content itself.
    """
    try:
        data = await request.json()
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            return JSONResponse({"error": "No transcript provided"}, status_code=400)

        # Short rolling history from the frontend — last few {transcript,
        # intent} pairs, just enough for follow-up resolution without
        # bloating the prompt or turning this into a full chat log.
        history = data.get("history") or []
        history_lines = []
        for h in history[-4:]:
            if isinstance(h, dict) and h.get("transcript"):
                history_lines.append(f'- You said "{h["transcript"]}" -> Althea did: {h.get("intent", "unknown")}')
        history_block = ("\n\nRecent conversation (most recent last), for resolving follow-ups only:\n" + "\n".join(history_lines)) if history_lines else ""

        prompt = f"""You are Althea, a voice command interpreter built into Althais, a medical billing and clinical workflow product. You are NOT a clinical assistant — you never give diagnoses, treatment suggestions, medication advice, or interpret symptoms. Your only job is to classify what in-app action the speaker wants, from this exact fixed list, and nothing else:

- "read_schedule" — read back the appointments/schedule for a day. Params: {{"date": "today" or "tomorrow" — default to "today" unless the speaker (or a follow-up in the recent conversation below) clearly asks about a different day}}
- "next_appointment" — read back just the single next upcoming appointment. No params.
- "claims_summary" — summarize claims (pending/denied/paid counts and total billed). No params.
- "open_patient" — open a specific patient's chart. Params: {{"patient_name": "<name as spoken>"}}
- "start_visit" — begin a new visit/encounter note for a patient. Params: {{"patient_name": "<name as spoken, or empty string if referring to the currently open patient>"}}
- "check_claim_readiness" — check whether a claim/visit is ready to submit (missing documentation, flags, risk score). Params: {{"patient_name": "<name as spoken, or empty string to check whatever claim/visit is currently open>"}}
- "read_allergies" — read back a patient's known allergies. Params: {{"patient_name": "<name as spoken, or empty string if they mean the patient whose chart is currently open, or the patient discussed earlier in the recent conversation below — e.g. 'this patient', 'my patient', 'their allergies', 'and their meds too', or no name given at all>"}}
- "read_medications" — read back a patient's current medications. Params: same "patient_name" rule as read_allergies.
- "read_labs" — read back a patient's recent lab results. Params: same "patient_name" rule as read_allergies.
- "start_visit_timer" — start timing a visit/encounter, to feed the exact duration into time-based CPT coding later. No params.
- "stop_visit_timer" — stop the running visit timer and report the elapsed time. No params.
- "claims_at_risk" — list the claims currently at highest denial risk across the practice. No params.
- "documentation_gaps_today" — list today's scheduled patients who don't have a completed visit note yet. No params.
- "prior_auth_pending" — list patients with a pending (not yet approved/denied) prior authorization. No params.
- "coding_complexity_check" — flag providers whose average E/M coding level looks lower than the practice average (a coding-pattern signal, not a clinical judgment). No params.
- "claims_denial_scan" — check the practice's highest-risk claims for specific missing documentation that could cause a denial. No params.
- "open_section" — navigate to a named part of the app. Params: {{"section": one of "claims", "scheduler", "settings", "soap", "patients"}}
- "unknown" — the request doesn't match any of the above, OR asks for anything clinical (diagnosis, treatment, medication advice, symptom interpretation) or anything outside this product's own functions.

Important on patient_name: only fill it in when a specific name is actually spoken (e.g. "open John Smith", "what is Maria's allergy"). Whenever the speaker refers to "this patient", "my patient", "the patient", "their ...", "and his/her ... too", or gives no name at all, leave patient_name as an empty string — the app resolves that to whichever patient was just discussed (in the recent conversation below) or whichever chart is currently open, so never guess a name that wasn't said.

Use the recent conversation below ONLY to resolve genuine follow-ups (a changed date, an implied "same patient as before", "what about X instead") — never let it override what the CURRENT request actually says.
{history_block}

Respond with a JSON object only, shaped exactly like:
{{"intent": "<one of the above>", "params": {{...}}, "spoken_ack": "a short, natural spoken acknowledgment of what you're doing, under 12 words"}}

If the request is clinical in nature in any way (asking Althea to interpret a result, suggest a diagnosis, or recommend treatment — as opposed to simply reading back what's on file), you MUST return intent "unknown" with a spoken_ack explaining that you only handle in-app tasks — never attempt to answer the clinical question itself.

No markdown, no commentary, no backticks — the JSON object only.

Spoken request: "{transcript}\""""

        intent_enum = [
            "read_schedule", "next_appointment", "claims_summary", "open_patient",
            "start_visit", "check_claim_readiness",
            "read_allergies", "read_medications", "read_labs",
            "start_visit_timer", "stop_visit_timer",
            "claims_at_risk", "documentation_gaps_today", "prior_auth_pending",
            "coding_complexity_check", "claims_denial_scan",
            "open_section", "unknown"
        ]
        # params varies by intent (a patient name, a section, a date) — strict
        # mode needs one fixed shape, so this covers every possible param key
        # at once; unused ones just come back as empty strings.
        althea_schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": intent_enum},
                "params": {
                    "type": "object",
                    "properties": {
                        "patient_name": {"type": "string"},
                        "section": {"type": "string"},
                        "date": {"type": "string"},
                    },
                    "required": ["patient_name", "section", "date"],
                    "additionalProperties": False,
                },
                "spoken_ack": {"type": "string"},
            },
            "required": ["intent", "params", "spoken_ack"],
            "additionalProperties": False,
        }

        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # this is intent classification, not creative generation
                response_format={"type": "json_schema", "json_schema": {"name": "althea_response", "strict": True, "schema": althea_schema}},
            )
        except Exception:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

        text = response.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            text = match.group(0)
        result = json.loads(text)

        allowed_intents = set(intent_enum)
        if not isinstance(result, dict) or result.get("intent") not in allowed_intents:
            result = {"intent": "unknown", "params": {}, "spoken_ack": "I'm not sure how to help with that."}
        if "params" not in result or not isinstance(result["params"], dict):
            result["params"] = {}
        # Strip empty-string params so the frontend's existing "is this param
        # present" checks (e.g. `params.patient_name`) still work the same
        # way they did before the fixed schema always included every key.
        result["params"] = {k: v for k, v in result["params"].items() if v not in (None, "")}
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
