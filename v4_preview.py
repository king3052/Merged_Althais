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
}


def _page(key: str, title: str):
    async def view(request: Request, user=Depends(current_user)):
        if not user:
            return RedirectResponse("/login?next=" + quote(str(request.url.path) + ("?" + request.url.query if request.url.query else "")), status_code=302)
        from main import _app_user_json   # main imports this module, so look it up at request time
        return _templates.TemplateResponse(request, "v4/page.html", {"page": key, "title": title, "user_json": _app_user_json(user)})
    return view


if ENABLED:
    @router.get("/v4")
    async def v4_home():
        return RedirectResponse("/v4/overview", status_code=302)

    for _path, (_key, _title) in PAGES.items():
        router.add_api_route(_path, _page(_key, _title), methods=["GET"], include_in_schema=False)
