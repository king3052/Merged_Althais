"""Working preview (v4): the whole app in the dense clinical layout, starting blank and fully editable.

At /v4, for logged-in users only (set ALTHAIS_V4_PREVIEW=0 to turn it off). The clinic's data lives in the browser,
kept separately for each clinic (static/v4/store.js); the pages are static/v4/app*.js. Althea runs on every page through
static/v4/althea-bridge.js, talking to the server as the logged-in user.
"""
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from auth import current_user

router = APIRouter()
_templates = None   # set by main.py

ENABLED = os.environ.get("ALTHAIS_V4_PREVIEW", "1") != "0"

# path -> (page key, browser title)
PAGES = {
    "/v4/overview": ("overview", "Overview"),
    "/v4/emr/schedule": ("schedule", "Schedule"),
    "/v4/emr/encounter": ("encounter", "Encounter"),
    "/v4/emr/patients": ("patients", "Patients"),
    "/v4/emr/chart": ("chart", "Patient chart"),
    "/v4/emr/documents": ("documents", "Documents"),
    "/v4/revenue/claims": ("claims", "Claims"),
    "/v4/revenue/denials": ("denials", "Denials"),
    "/v4/revenue/payer-rules": ("rules", "Payer rules"),
    "/v4/revenue/code-a-note": ("codenote", "Code a Note"),
    "/v4/revenue/claim-tracker": ("tracker", "Claim Tracker"),
    "/v4/revenue/billing-activation": ("billing", "Billing Activation"),
    "/v4/revenue/connect": ("connect", "Connect EMR"),
    "/v4/staff/team": ("team", "Team"),
    "/v4/settings": ("settings", "Settings"),
    "/v4/settings/access-log": ("accesslog", "Access Log"),
    # the dental EMR (static/v4/dental-*.js, server: dental_api.py). Only dental clinics get these.
    "/v4/dental/today": ("dtoday", "Today"),
    "/v4/dental/schedule": ("dschedule", "Schedule"),
    "/v4/dental/patients": ("dpatients", "Patients"),
    "/v4/dental/chart": ("dchart", "Chart"),
    "/v4/dental/benefits": ("dbenefits", "Insurance Verification"),
    "/v4/dental/claims": ("dclaims", "Claims"),
    "/v4/dental/preauths": ("dpreauths", "Pre-Authorizations"),
    "/v4/dental/era": ("dera", "ERA Posting"),
    "/v4/dental/denials": ("ddenials", "Denials & Follow-Up"),
    "/v4/dental/billing": ("dbilling", "Patient Billing"),
    "/v4/dental/messages": ("dmessages", "Messages"),
    "/v4/dental/recall": ("drecall", "Recall"),
    "/v4/dental/forms": ("dforms", "Forms"),
    "/v4/dental/reviews": ("dreviews", "Reviews"),
    "/v4/dental/reports": ("dreports", "Production & Collections"),
    "/v4/dental/labs": ("dlabs", "Lab Cases"),
    "/v4/dental/setup": ("dsetup", "Practice Setup"),
}
DENTAL_PAGES = {k for k, _ in PAGES.values() if k.startswith("d") and k not in ("documents", "denials")}
# a dental clinic opening one of the shared pages gets the dental one instead
DENTAL_INSTEAD = {"overview": "/v4/dental/today", "schedule": "/v4/dental/schedule", "patients": "/v4/dental/patients", "chart": "/v4/dental/chart",
                  "claims": "/v4/dental/claims", "denials": "/v4/dental/denials", "tracker": "/v4/dental/claims"}


# page key -> the area (auth.AREAS) it belongs to: a page shows only when the clinic's plan has that area's tool and the
# clinic's manager hasn't switched it off for this person. Pages not listed are open to every signed-in member.
AREA_OF = {"overview": "overview", "schedule": "schedule", "encounter": "patients", "patients": "patients", "chart": "patients",
           "documents": "patients", "claims": "claims", "tracker": "claims", "denials": "denials", "rules": "payer_intelligence",
           "codenote": "code_a_note", "billing": "billing_activation", "connect": "claims", "team": "team",
           "settings": "settings", "accesslog": "settings",
           "dtoday": "overview", "dschedule": "schedule", "dpatients": "patients", "dchart": "patients", "dbenefits": "claims", "dclaims": "claims",
           "dpreauths": "claims", "dera": "claims", "ddenials": "denials", "dbilling": "claims", "dmessages": "patients", "drecall": "schedule",
           "dforms": "patients", "dreviews": "overview", "dreports": "overview", "dlabs": "patients", "dsetup": "settings"}


def allowed_pages(user, products: set) -> list:
    from auth import AREAS, has_product, member_blocked
    blocked = member_blocked(user)

    def ok(key):
        area = AREA_OF.get(key)
        if not area:
            return True
        tool = AREAS[area][1]
        return area not in blocked and (tool == "*" or has_product(products, tool))
    return [k for k, _ in PAGES.values() if ok(k)]


def _page(key: str, title: str):
    async def view(request: Request, user=Depends(current_user)):
        if not user:
            return RedirectResponse("/login?next=" + quote(str(request.url.path) + ("?" + request.url.query if request.url.query else "")), status_code=302)
        import json
        from main import _app_user_json, _home_for_user, _user_products   # main imports this module, so look them up at request time
        pages = allowed_pages(user, _user_products(user))
        from main import _user_practice
        dental = _user_practice(user) == "dental"
        if not dental:
            pages = [k for k in pages if k not in DENTAL_PAGES]
        elif key in DENTAL_INSTEAD and request.query_params.get("classic") != "1":
            qs = request.url.query
            return RedirectResponse(DENTAL_INSTEAD[key] + ("?" + qs if qs else ""), status_code=302)
        if key not in pages:   # not in this clinic's plan, or switched off for this person: their first page instead
            first = next((path for path, (k, _) in PAGES.items() if k in pages and k not in ("settings", "accesslog") and (not dental or k in DENTAL_PAGES)), None)
            home = _home_for_user(user)
            return RedirectResponse(first or (home if not home.startswith("/v4") else "/no-access"), status_code=302)
        me = json.loads(_app_user_json(user))
        me["v4_pages"] = pages
        import crypto_box
        bk = crypto_box.browser_key(crypto_box.store_slug(user.organization, user.email))
        resp = _templates.TemplateResponse(request, "v4/page.html", {"page": key, "title": title, "user_json": json.dumps(me), "browser_key": bk})
        # the page carries the key to this clinic's data in the browser: never keep it in the browser's cache
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"
        return resp
    return view


if ENABLED:
    @router.get("/v4")
    async def v4_home():
        return RedirectResponse("/v4/overview", status_code=302)

    for _path, (_key, _title) in PAGES.items():
        router.add_api_route(_path, _page(_key, _title), methods=["GET"], include_in_schema=False)
