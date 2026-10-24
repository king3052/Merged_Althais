"""Pages patients open from a text or the office tablet, without signing in: online booking, intake and medical
history forms, their treatment plan, text-to-pay, and the post-visit review. One template (templates/dental/public.html)
draws each from the /api/public/dental/... answers; the link's token is the only key, and every action is in the access
log (dental_api.py, dental_engage.py, dental_api_rcm.py).
"""
from fastapi import APIRouter, Request

router = APIRouter()
_templates = None   # set by main.py

KINDS = {"book": "Book a visit", "form": "Your forms", "plan": "Your treatment plan", "pay": "Pay your balance", "review": "How did we do?"}


def _page(kind: str):
    async def view(request: Request, key: str):
        resp = _templates.TemplateResponse(request, "dental/public.html", {"kind": kind, "key": key, "title": KINDS[kind]})
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"   # the token is in the address
        return resp
    return view


for _k in KINDS:
    router.add_api_route(f"/dental/{_k}/{{key}}", _page(_k), methods=["GET"], include_in_schema=False)
