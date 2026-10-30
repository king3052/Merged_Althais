"""
opendental.py: the live Open Dental connection for dental clinics (Practice setup > Import data, routes in dental_ops.py).

How access works (Open Dental API docs, "API Developer Setup"):
  Althais holds one Developer Key (env OPENDENTAL_DEVELOPER_KEY, from Open Dental's developer portal). For each office,
  Althais generates a Customer Key in that portal; the office pastes it into Open Dental (Setup > Advanced Setup > API >
  Add Key) and gives it to Althais here. Every call carries "Authorization: ODFHIR {DeveloperKey}/{CustomerKey}" and goes
  to Open Dental's API service (OPENDENTAL_BASE, default https://api.opendental.com/api/v1), which routes it to the
  office's database. Open Dental charges the office a monthly fee per location for API access.

What a pull does (read-only; nothing is written to Open Dental):
  1. Completed procedures (ProcStatus C) changed in the last N days: GET /procedurelogs?ProcStatus=C&DateTStamp=...
     (100 per page, Offset to page), kept when the procedure date is in range: code, tooth (ToothNum), surfaces (Surf)
     or area (Surf UR, UL, LR, LL, or U / L for an arch), fee (ProcFee x UnitQty).
  2. Each of those patients (GET /patients/{PatNum}) and their primary dental plan: /patplans (Ordinal 1) -> /inssubs
     (subscriber ID) -> /insplans (group) -> /carriers (the payer's name and electronic payer ID).
  3. They go into the dental EMR the same way an Open Dental export does (dental_ops.commit_rows): patients become
     MRN OD<PatNum>, procedures become history (frequency limits, reports), plans become coverage. Running it again
     updates patients and skips procedures already imported.

Verified against the published API reference: field and parameter names used here. Not yet tested against a live office
(Open Dental hosts a shared test database for developers; run the connection against it before the first clinic).

The Customer Key is stored encrypted (AES-256-GCM, crypto_box.py) and never sent back to the browser.
"""
import datetime as dt
import json
import os
import re
from collections import OrderedDict
from datetime import timezone

import requests
from sqlalchemy import select as sa_select

from auth import OrgSettings

CATEGORY = "opendental"
BASE = os.environ.get("OPENDENTAL_BASE", "https://api.opendental.com/api/v1").rstrip("/")
PAGE = 100
MAX_PAGES = 60
MAX_DAYS = 120
MAX_PATIENTS = 500
ICD = re.compile(r"^[A-TV-Z][0-9][0-9AB](?:\.?[0-9A-TV-Z]{1,4})?$")
QUADS = {"UR": "UR", "UL": "UL", "LR": "LR", "LL": "LL", "U": "UA", "L": "LA"}


class OpenDentalError(Exception):
    pass


def developer_key() -> str:
    return os.environ.get("OPENDENTAL_DEVELOPER_KEY", "").strip()


# ---- stored connection ----
def _seal(t):
    import crypto_box
    return crypto_box.seal_text(t, "opendental", "PHI_ENC_KEY")


def _unseal(t):
    import crypto_box
    return crypto_box.open_text(t, "opendental", "PHI_ENC_KEY")


def _load(db, org):
    row = db.scalar(sa_select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))
    try:
        return row, (json.loads(row.data or "{}") if row else {})
    except Exception:
        return row, {}


def _save(db, org, doc):
    row, _ = _load(db, org)
    if not row:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    row.data = json.dumps(doc)
    db.commit()


# ---- the API ----
class Client:
    def __init__(self, customer_key: str, clinic_num: str = "", session=None):
        if not developer_key():
            raise OpenDentalError("Althais's Open Dental developer key isn't set up yet.")
        self.auth = f"ODFHIR {developer_key()}/{customer_key}"
        self.clinic = str(clinic_num or "").strip()
        self.http = session or requests
        self.cache = {}

    def get(self, path, params=None):
        try:
            r = self.http.get(BASE + path, params=params or {}, headers={"Authorization": self.auth, "Accept": "application/json"}, timeout=30)
        except requests.RequestException as e:
            raise OpenDentalError(f"Couldn't reach Open Dental ({type(e).__name__}).")
        if r.status_code in (401, 403):
            raise OpenDentalError("Open Dental didn't accept the key. Check that the office added this Customer Key in Setup > Advanced Setup > API, and that it's enabled.")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise OpenDentalError(f"Open Dental answered {r.status_code}: {(r.text or '')[:200]}")
        try:
            return r.json()
        except ValueError:
            raise OpenDentalError("Open Dental sent something that isn't JSON.")

    def post(self, path, body):
        try:
            r = self.http.post(BASE + path, json=body, headers={"Authorization": self.auth, "Accept": "application/json"}, timeout=60)
        except requests.RequestException as e:
            raise OpenDentalError(f"Couldn't reach Open Dental ({type(e).__name__}).")
        if r.status_code in (401, 403):
            raise OpenDentalError("Open Dental didn't accept the key, or the key doesn't have permission for documents. Ask Open Dental to add Documents read/create to Althais's key.")
        if r.status_code == 404:
            raise OpenDentalError("Open Dental couldn't find that document or patient.")
        if r.status_code >= 400:
            raise OpenDentalError(f"Open Dental answered {r.status_code}: {(r.text or '')[:200]}")
        try:
            return r.json() if (r.text or "").strip() else {}
        except ValueError:
            return {}

    def one(self, path):
        if path not in self.cache:
            self.cache[path] = self.get(path)
        return self.cache[path]

    def pages(self, path, params):
        out = []
        for i in range(MAX_PAGES):
            rows = self.get(path, dict(params, Offset=i * PAGE)) or []
            if not isinstance(rows, list):
                break
            out.extend(rows)
            if len(rows) < PAGE:
                return out, False
        return out, True

    def providers(self):
        if "providers" not in self.cache:
            self.cache["providers"] = {str(p.get("ProvNum")): p for p in (self.get("/providers", {"ClinicNum": self.clinic} if self.clinic else None) or [])}
        return self.cache["providers"]

    def plan(self, pat_num):
        """(payer name, subscriber ID, group number, the payer's electronic ID) of the patient's primary dental plan."""
        plans = self.get("/patplans", {"PatNum": pat_num}) or []
        prim = sorted([p for p in plans if str(p.get("IsPending") or "false").lower() in ("false", "0")], key=lambda p: int(p.get("Ordinal") or 9))
        if not prim:
            return "", "", "", ""
        pp = prim[0]
        sub = (self.one(f"/inssubs/{pp.get('InsSubNum')}") or {}) if pp.get("InsSubNum") else {}
        plan = (self.one(f"/insplans/{sub.get('PlanNum')}") or {}) if sub.get("PlanNum") else {}
        car = (self.one(f"/carriers/{plan.get('CarrierNum')}") or {}) if plan.get("CarrierNum") else {}
        return ((car.get("CarrierName") or plan.get("GroupName") or "").strip(), (pp.get("PatID") or sub.get("SubscriberID") or "").strip(),
                (plan.get("GroupNum") or "").strip(), (car.get("ElectID") or "").strip())


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "") or 0)
    except ValueError:
        return 0.0


def _date(v):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    return "-".join(m.groups()) if m else ""


def area(surf: str):
    """Open Dental keeps a quadrant or arch in Surf for per-quadrant and arch codes."""
    return QUADS.get(str(surf or "").strip().upper(), "")


def to_rows(procs, cl: Client, start: dt.date):
    """Completed CDT procedures since `start`, and the patients they belong to with their primary dental plan, in the
    row shapes the dental EMR's import uses (dental_ops.commit_rows). Returns (patients, procedures, notes)."""
    keep, notes, skipped = [], [], 0
    for p in procs:
        if str(p.get("ProcStatus") or "C").upper() not in ("C", "COMPLETE", "2"):
            continue
        day = _date(p.get("ProcDate"))
        if not day or day < start.isoformat():
            continue
        code = str(p.get("procCode") or "").strip().upper()
        if not re.fullmatch(r"D\d{4}", code):
            skipped += 1               # office-defined codes (adjustments, broken appointments...) aren't procedures plans pay for
            continue
        keep.append(p)
    procedures, pats = [], OrderedDict()
    for p in keep:
        pat = str(p.get("PatNum"))
        pats.setdefault(pat, None)
        surf = str(p.get("Surf") or "").strip().upper()
        q = area(surf)
        units = max(1, int(_num(p.get("UnitQty")) or 1))
        procedures.append({"src_id": pat, "date": _date(p.get("ProcDate")), "code": str(p.get("procCode")).strip().upper(),
                           "tooth": str(p.get("ToothNum") or "").strip().upper(), "surface": "" if q else surf, "quad": q,
                           "fee": round(_num(p.get("ProcFee")) * units, 2)})
    patients = []
    for pat in list(pats)[:MAX_PATIENTS]:
        pt = cl.one(f"/patients/{pat}") or {}
        payer, member, group, elect = cl.plan(pat)
        sex = str(pt.get("Gender") or "")
        patients.append({"src_id": pat, "name": f"{str(pt.get('LName', '')).title()}, {str(pt.get('FName', '')).title()}".strip(", ") or f"Patient {pat}",
                         "dob": _date(pt.get("Birthdate")), "sex": {"0": "M", "1": "F", "MALE": "M", "FEMALE": "F"}.get(sex.upper(), sex[:1].upper()),
                         "phone": re.sub(r"\D", "", str(pt.get("WirelessPhone") or pt.get("HmPhone") or "")), "email": pt.get("Email") or "",
                         "addr": ", ".join(x for x in (pt.get("Address"), pt.get("City"), f"{pt.get('State', '')} {pt.get('Zip', '')}".strip()) if x),
                         "payer": payer, "member": member, "group": group, "payer_id": elect})
    if len(pats) > MAX_PATIENTS:
        notes.append(f"{len(pats) - MAX_PATIENTS} more patients had procedures in that range; pull a shorter range for them.")
        allowed = {p["src_id"] for p in patients}
        procedures = [p for p in procedures if p["src_id"] in allowed]
    if skipped:
        notes.append(f"{skipped} completed procedure(s) with office-only codes (not CDT) were left out.")
    no_plan = sum(1 for p in patients if not p["payer"])
    if no_plan:
        notes.append(f"{no_plan} patient(s) have no primary dental plan in Open Dental.")
    return patients, procedures, notes


def pull(customer_key: str, clinic_num: str, days: int, session=None):
    """Read-only pull from Open Dental: (patients, procedures, notes)."""
    cl = Client(customer_key, clinic_num, session)
    today = dt.datetime.now(timezone.utc).date()
    start = today - dt.timedelta(days=days)
    params = {"ProcStatus": "C", "DateTStamp": f"{start.isoformat()} 00:00:00"}
    if clinic_num:
        params["ClinicNum"] = clinic_num
    procs, more = cl.pages("/procedurelogs", params)
    patients, procedures, notes = to_rows(procs, cl, start)
    if more:
        notes.insert(0, f"Open Dental had more than {PAGE * MAX_PAGES} procedures in that range; pull a shorter range for the rest.")
    return patients, procedures, notes


def connection(db, org) -> dict:
    """What Practice setup shows: whether Althais's developer key is set, and this office's connection (no secrets)."""
    _, doc = _load(db, org)
    return {"available": bool(developer_key()), "connected": bool(doc.get("secret")), "clinic_num": doc.get("clinic_num", ""),
            "connected_at": doc.get("connected_at", ""), "last_pull": doc.get("last_pull", ""), "last_summary": doc.get("last_summary") or {}}


def connect(db, org, key: str, clinic: str, session=None):
    key, clinic = str(key or "").strip(), str(clinic or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", key):
        raise OpenDentalError("Paste the Customer Key exactly as Althais gave it (letters and numbers).")
    if clinic and not clinic.isdigit():
        raise OpenDentalError("The clinic number is a number from Open Dental (leave it empty for a single office).")
    Client(key, clinic, session).get("/providers", {"ClinicNum": clinic} if clinic else None)     # proves the key works
    _save(db, org, {"secret": _seal(key), "clinic_num": clinic, "connected_at": dt.datetime.now(timezone.utc).isoformat()})


def disconnect(db, org):
    row, _ = _load(db, org)
    if row:
        row.data = "{}"
        db.commit()


def stored_key(db, org):
    _, doc = _load(db, org)
    if not doc.get("secret"):
        raise OpenDentalError("Open Dental isn't connected yet.")
    return _unseal(doc["secret"]), doc.get("clinic_num", "")


def record_pull(db, org, summary: dict):
    _, doc = _load(db, org)
    doc["last_pull"], doc["last_summary"] = dt.datetime.now(timezone.utc).isoformat(), summary
    _save(db, org, doc)


# ---- documents and images (Imaging module) ----
# Open Dental never returns a file's bytes over the API. DownloadSftp asks the office's eConnector to put the file on an
# SFTP server we name; Althais then reads it from there and deletes it. The SFTP server is Althais's own (env below),
# reachable from the internet with a key-pinned host. Uploading goes the other way through POST /documents/Upload
# (base64), which Open Dental turns into a real file the first time someone opens it in the Imaging module.
SFTP = {"host": "OPENDENTAL_SFTP_HOST", "port": "OPENDENTAL_SFTP_PORT", "user": "OPENDENTAL_SFTP_USER",
        "password": "OPENDENTAL_SFTP_PASSWORD", "dir": "OPENDENTAL_SFTP_DIR", "hostkey": "OPENDENTAL_SFTP_HOSTKEY"}
UPLOAD_EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif", "image/webp": ".webp"}


def sftp_config() -> dict:
    c = {k: os.environ.get(v, "").strip() for k, v in SFTP.items()}
    c["port"] = int(c["port"] or 22)
    c["dir"] = c["dir"].strip("/")
    return c


def sftp_ready() -> bool:
    c = sftp_config()
    return bool(c["host"] and c["user"] and c["password"] and c["hostkey"])


def pat_num(mrn: str) -> str:
    """Open Dental's PatNum for a patient that came from Open Dental (MRN OD<PatNum>), or ""."""
    m = re.fullmatch(r"OD(\d+)", str(mrn or "").strip().upper())
    return m.group(1) if m else ""


def documents(cl: Client, pnum: str) -> list:
    """The patient's documents and images in Open Dental (newest first), without file contents."""
    rows = cl.get("/documents", {"PatNum": pnum}) or []
    out = []
    for d in rows if isinstance(rows, list) else []:
        out.append({"doc_num": str(d.get("DocNum") or ""), "mount_num": str(d.get("MountNum") or "0"), "description": d.get("Description") or "",
                    "date": _date(d.get("DateCreated")), "category": d.get("docCategory") or "", "teeth": d.get("ToothNumbers") or "",
                    "image_type": d.get("ImgType") or "", "file": (str(d.get("filePath") or "").replace("\\", "/").rsplit("/", 1)[-1])})
    return sorted([d for d in out if d["doc_num"] and d["doc_num"] != "0"], key=lambda d: d["date"], reverse=True)


def _sftp():
    import base64 as b64
    import paramiko
    c = sftp_config()
    if not sftp_ready():
        raise OpenDentalError("Althais's file drop for Open Dental isn't set up yet (OPENDENTAL_SFTP_* settings).")
    kt, _, kb = c["hostkey"].partition(" ")
    try:
        key = paramiko.PKey.from_type_string(kt, b64.b64decode(kb.split()[0]))
    except Exception:
        raise OpenDentalError("OPENDENTAL_SFTP_HOSTKEY should look like 'ssh-ed25519 AAAA...'.")
    t = paramiko.Transport((c["host"], c["port"]))
    t.banner_timeout = t.auth_timeout = 20
    t.start_client(timeout=20)
    if t.get_remote_server_key() != key:          # pinned: never send the password to a server we didn't expect
        t.close()
        raise OpenDentalError("The file drop server's key doesn't match OPENDENTAL_SFTP_HOSTKEY. Nothing was downloaded.")
    t.auth_password(c["user"], c["password"])
    return t, paramiko.SFTPClient.from_transport(t)


def download(cl: Client, doc_num: str, max_bytes: int = 20 * 1024 * 1024):
    """(bytes, filename) of one Open Dental document, through Althais's SFTP drop. The copy on the drop is deleted."""
    import uuid
    c = sftp_config()
    if not sftp_ready():
        raise OpenDentalError("Althais's file drop for Open Dental isn't set up yet (OPENDENTAL_SFTP_* settings).")
    info = cl.get(f"/documents/{doc_num}") or {}
    name = str(info.get("FileName") or "")
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ".jpg"
    if not re.fullmatch(r"\.[a-z0-9]{2,5}", ext):
        ext = ".bin"
    rel = "/".join(x for x in (c["dir"], uuid.uuid4().hex + ext) if x)
    res = cl.post("/documents/DownloadSftp", {"DocNum": int(doc_num), "SftpAddress": f"{c['host']}/{rel}", "SftpUsername": c["user"],
                                              "SftpPassword": c["password"], **({"SftpPort": c["port"]} if c["port"] != 22 else {})})
    loc = str((res or {}).get("location") or rel)
    path = "/" + loc.split(c["host"], 1)[-1].lstrip("/") if c["host"] in loc else "/" + rel
    t, sf = _sftp()
    try:
        if sf.stat(path).st_size > max_bytes:
            raise OpenDentalError("That file is over 20 MB. Open it in Open Dental instead.")
        with sf.open(path, "rb") as fh:
            data = fh.read()
        try:
            sf.remove(path)
        except Exception:
            pass
    finally:
        sf.close()
        t.close()
    return data, (name or f"opendental-{doc_num}{ext}")


def upload(cl: Client, pnum: str, data: bytes, mime: str, description: str) -> dict:
    """Put a file into the patient's Open Dental Imaging module. Returns Open Dental's document (DocNum ...)."""
    import base64 as b64
    ext = UPLOAD_EXT.get(mime)
    if not ext:
        raise OpenDentalError("Open Dental takes PDF, PNG, JPEG, TIFF or WebP files.")
    return cl.post("/documents/Upload", {"PatNum": int(pnum), "rawBase64": b64.b64encode(data).decode(), "extension": ext,
                                         "Description": str(description or "Outside record")[:255]})
