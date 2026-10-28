"""Records Inbox (records_intake.py): upload links, fax, filing, Open Dental documents and the records network."""
import base64
import datetime as dt
import hashlib
import hmac
import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import records_intake as ri


def _pdf(lines):
    from fpdf import FPDF
    p = FPDF(format="Letter")
    p.add_page()
    p.set_font("Helvetica", "", 11)
    for ln in lines:
        p.cell(0, 7, ln, new_x="LMARGIN", new_y="NEXT")
    return bytes(p.output())


def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("L", (40, 30), 120).save(b, "PNG")
    return b.getvalue()


LAB = _pdf(["QUEST SAMPLE LABORATORY", "Patient Name: Rivera, Ana    DOB: 04/12/1985", "Collected 10/20/2026  Reported 10/21/2026",
            "Hemoglobin A1c  6.9 %  Reference range 4.0 - 5.6", "Glucose 131 mg/dL  Reference range 70 - 99", "Specimen: serum"])


def _user(org_name, role="admin"):
    email = f"intake-{org_name.lower().replace(' ', '-')}-{role}@example.test"
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == email))
        if not u:
            u = auth.User(email=email, password_hash=auth.hash_password("x" * 12), full_name="Intake Tester", organization=org_name, role=role,
                          claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u)
            db.commit()
            db.refresh(u)
        org = auth._org_namespace(u)
        for mrn, name, dob in (("M100", "Rivera, Ana", "1985-04-12"), ("M200", "Rivera, Carlos", "1990-02-02"), ("OD77", "Testpatient, Delta", "1968-03-15")):
            if not db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == org, auth.OrgPatient.mrn == mrn)):
                db.add(auth.OrgPatient(org_key=org, mrn=mrn, name=name, dob=dob, data="{}"))
        db.commit()
        db.refresh(u)
        db.expunge(u)
    return u


@pytest.fixture()
def as_user():
    import main
    made = []

    def use(u):
        for d in (auth.require_user, auth.require_biller, auth.current_user):
            main.app.dependency_overrides[d] = lambda u=u: u
        c = TestClient(main.app)
        made.append(c)
        return c
    ri._hits.clear()
    yield use
    main.app.dependency_overrides.clear()


def test_guess_reads_a_text_lab_report():
    u = _user("Intake Clinic A")
    with auth.SessionLocal() as db:
        g = ri.guess(LAB, "application/pdf", db, auth._org_namespace(u))
    assert g["doc_type"] == "lab" and g["dob"] == "1985-04-12" and g["patient_name"].startswith("Rivera")
    assert g["candidates"][0]["mrn"] == "M100" and g["confidence"] == "high"
    assert ri.sniff(b"MZ\x90\x00junk") == "" and ri.sniff(_png()) == "image/png" and ri.sniff(LAB) == "application/pdf"


def test_upload_link_end_to_end(as_user):
    c = as_user(_user("Intake Clinic A"))
    r = c.post("/api/intake/links", json={"label": "Northside Dental, labs for Rivera", "days": 7})
    assert r.status_code == 200 and r.json()["path"].startswith("/send-records/")
    path = r.json()["path"]
    token = path.rsplit("/", 1)[1]
    page = c.get(path)
    assert page.status_code == 200 and "Intake Clinic A" in page.text and "Rivera" not in page.text      # the label stays private
    files = [("files", ("labs.pdf", LAB, "application/pdf")), ("files", ("virus.pdf", b"MZ\x90\x00not a pdf", "application/pdf"))]
    up = c.post(f"/api/public/records/{token}", files=files, data={"sender": "Dr. Lee, Northside", "note": "Recent labs"})
    assert up.status_code == 200 and up.json()["saved"] == 1 and "not a PDF or image" in up.json()["message"]
    box = c.get("/api/intake/inbox").json()
    d = next(x for x in box["docs"] if x["sender"] == "Dr. Lee, Northside")
    assert d["status"] == "new" and d["doc_type"] == "lab" and d["guess"]["candidates"][0]["mrn"] == "M100"
    full = c.get(f"/api/intake/docs/{d['id']}").json()
    assert full["data"].startswith("data:application/pdf;base64,") and base64.b64decode(full["data"].split(",", 1)[1]) == LAB
    with auth.SessionLocal() as db:                                   # stored encrypted, not as the raw file
        row = db.get(ri.IntakeDoc, d["id"])
        assert base64.b64encode(LAB).decode()[:40] not in row.data and "Northside" not in row.sealed
    assert c.post(f"/api/intake/docs/{d['id']}/file", json={"mrn": "NOPE", "doc_type": "lab"}).status_code == 400
    f = c.post(f"/api/intake/docs/{d['id']}/file", json={"mrn": "M100", "doc_type": "lab", "title": "A1c and glucose"}).json()
    assert f["ok"] and f["doc"]["status"] == "filed" and f["doc"]["patient"] == "Rivera, Ana"
    assert any(x["id"] == d["id"] for x in c.get("/api/intake/inbox?status=filed&mrn=M100").json()["docs"])
    link = next(x for x in c.get("/api/intake/links").json() if x["files"] == 1)
    c.post(f"/api/intake/links/{link['id']}/revoke")
    assert c.get(path).status_code == 404
    assert c.post(f"/api/public/records/{token}", files=files[:1]).status_code == 404


def test_expired_link_and_other_clinics(as_user):
    a = as_user(_user("Intake Clinic A"))
    path = a.post("/api/intake/links", json={"days": 1}).json()["path"]
    token = path.rsplit("/", 1)[1]
    a.post(f"/api/public/records/{token}", files=[("files", ("x.png", _png(), "image/png"))])
    did = next(x["id"] for x in a.get("/api/intake/inbox").json()["docs"] if x["mime"] == "image/png")
    b = as_user(_user("Intake Clinic B"))
    assert b.get(f"/api/intake/docs/{did}").status_code == 404 and not b.get("/api/intake/inbox").json()["docs"]
    with auth.SessionLocal() as db:
        row = db.scalar(select(ri.IntakeLink).where(ri.IntakeLink.token_hash == ri._hash(token)))
        row.expires_at = dt.datetime(2020, 1, 1)
        db.commit()
    assert b.get(path).status_code == 404


def test_fax_webhook(as_user, monkeypatch):
    c = as_user(_user("Intake Clinic A"))
    assert c.post("/api/intake/settings", json={"fax_numbers": ["(512) 555-0100"]}).json()["fax_numbers"] == ["5125550100"]
    other = as_user(_user("Intake Clinic B"))
    assert other.post("/api/intake/settings", json={"fax_numbers": ["512-555-0100"]}).status_code == 400   # one clinic per number
    body = json.dumps({"to": "+1 512 555 0100", "from": "+15125559999", "pages": 2, "id": "fx-1",
                       "file": {"name": "fax.pdf", "base64": base64.b64encode(LAB).decode()}}).encode()
    assert c.post("/api/intake/fax/inbound", content=body).status_code == 503                    # not set up
    monkeypatch.setenv("FAX_WEBHOOK_SECRET", "s3cret")
    assert c.post("/api/intake/fax/inbound", content=body, headers={"X-Althais-Signature": "bad"}).status_code == 401
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    r = c.post("/api/intake/fax/inbound", content=body, headers={"X-Althais-Signature": "sha256=" + sig})
    assert r.status_code == 200 and r.json()["id"]
    assert c.post("/api/intake/fax/inbound", content=body, headers={"X-Althais-Signature": sig}).json().get("duplicate")
    c = as_user(_user("Intake Clinic A"))
    fx = [x for x in c.get("/api/intake/inbox").json()["docs"] if x["source"] == "fax"]
    assert len(fx) == 1 and "2 page(s)" in fx[0]["sender"] and fx[0]["doc_type"] == "lab"
    lost = json.dumps({"to": "4155550000", "file": {"base64": base64.b64encode(LAB).decode()}}).encode()
    assert c.post("/api/intake/fax/inbound", content=lost, headers={"X-Althais-Signature": hmac.new(b"s3cret", lost, hashlib.sha256).hexdigest()}).status_code == 404


def test_records_network_mock(as_user, monkeypatch):
    c = as_user(_user("Intake Clinic A"))
    assert c.post("/api/intake/network/M100").status_code == 400                                 # off by default
    monkeypatch.setenv("RECORDS_NETWORK", "mock")
    assert "synthetic" in c.post("/api/intake/network/M100").json()["error"]                     # real-looking patients never go out
    r = c.post("/api/intake/network/OD77").json()
    assert r["found"] == 2 and r["added"] == 2 and c.post("/api/intake/network/OD77").json()["added"] == 0
    kinds = {x["doc_type"] for x in c.get("/api/intake/inbox").json()["docs"] if x["source"] == "network"}
    assert kinds == {"lab", "imaging"}


def test_open_dental_documents(as_user, monkeypatch):
    import opendental as od
    c = as_user(_user("Intake Clinic A"))
    png = _png()

    class FakeHTTP:
        def __init__(self):
            self.posts = []

        def get(self, url, params=None, headers=None, timeout=None):
            class R: pass
            r = R()
            r.status_code, r.text = 200, "x"
            if url.endswith("/documents"):
                r.json = lambda: [{"DocNum": 34, "MountNum": 0, "filePath": "\\\\server\\OpenDentImages\\T\\Delta\\bw.png", "Description": "Bitewings",
                                   "DateCreated": "2026-10-01", "docCategory": "BW", "ToothNumbers": "2-5"}]
            else:
                r.json = lambda: {"DocNum": 34, "FileName": "bw.png"}
            return r

        def post(self, url, json=None, headers=None, timeout=None):
            self.posts.append((url, json))
            class R: pass
            r = R()
            r.status_code, r.text = 201, "x"
            r.json = (lambda: {"location": "drop.example.com/" + json["SftpAddress"].split("/", 1)[1]}) if url.endswith("DownloadSftp") else (lambda: {"DocNum": 99})
            return r
    http = FakeHTTP()

    class FakeSFTP:
        def stat(self, p): return type("S", (), {"st_size": len(png)})()
        def open(self, p, mode):
            assert p.startswith("/drops/")
            return io.BytesIO(png)
        def remove(self, p): self.removed = p
        def close(self): pass
    monkeypatch.setattr(od, "stored_key", lambda db, org: ("CUSTKEY123", ""))
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "DEVKEY")
    real_client = od.Client
    monkeypatch.setattr(od, "Client", lambda k, cl="": real_client(k, cl, session=http))
    assert c.get("/api/intake/opendental/M100/documents").status_code == 400                    # not an Open Dental patient
    lst = c.get("/api/intake/opendental/OD77/documents").json()
    assert lst["documents"][0]["doc_num"] == "34" and lst["documents"][0]["file"] == "bw.png" and not lst["files_ready"]
    assert "file drop" in c.post("/api/intake/opendental/OD77/documents/34").json()["error"]
    for k, v in {"OPENDENTAL_SFTP_HOST": "drop.example.com", "OPENDENTAL_SFTP_USER": "od", "OPENDENTAL_SFTP_PASSWORD": "pw",
                 "OPENDENTAL_SFTP_DIR": "drops", "OPENDENTAL_SFTP_HOSTKEY": "ssh-ed25519 AAAA"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(od, "_sftp", lambda: (type("T", (), {"close": lambda self: None})(), FakeSFTP()))
    r = c.post("/api/intake/opendental/OD77/documents/34").json()
    assert r["ok"] and r["doc"]["status"] == "filed" and r["doc"]["mrn"] == "OD77" and r["doc"]["mime"] == "image/png"
    dl = next(p for p in http.posts if p[0].endswith("DownloadSftp"))[1]
    assert dl["DocNum"] == 34 and dl["SftpAddress"].startswith("drop.example.com/drops/") and dl["SftpAddress"].endswith(".png")
    assert c.get("/api/intake/opendental/OD77/documents").json()["documents"][0]["imported"]
    push = c.post(f"/api/intake/docs/{r['doc']['id']}/opendental").json()
    up = next(p for p in http.posts if p[0].endswith("/documents/Upload"))[1]
    assert push["doc_num"] == "99" and up["PatNum"] == 77 and up["extension"] == ".png" and base64.b64decode(up["rawBase64"]) == png


def test_dental_xray_filed_into_images(as_user, monkeypatch):
    import dental_db as ddb
    u = _user("Intake Dental C")
    c = as_user(u)
    monkeypatch.setattr(auth, "org_practice", lambda user, db: "dental")
    tok = c.post("/api/intake/links", json={"mrn": "M100"}).json()["path"].rsplit("/", 1)[1]
    c.post(f"/api/public/records/{tok}", files=[("files", ("pa.png", _png(), "image/png"))])
    d = next(x for x in c.get("/api/intake/inbox").json()["docs"] if x["mime"] == "image/png")
    assert d["mrn"] == "M100" and d["doc_type"] == "xray"                                          # the link was for this patient
    r = c.post(f"/api/intake/docs/{d['id']}/file", json={"mrn": "M100", "doc_type": "xray", "image_kind": "bitewing", "teeth": ["2", "3"]}).json()
    assert r["image_id"]
    with auth.SessionLocal() as db:
        img = db.get(ddb.Image, r["image_id"])
        assert img.org_key == auth._org_namespace(u) and img.kind == "bitewing" and img.device == "outside records" and img.data.startswith("data:image/png")
