"""AES-256-GCM for everything Althais encrypts itself (crypto_box.py), with old Fernet values still readable."""
import base64
import hashlib
import json

import pytest
from cryptography.fernet import Fernet

import crypto_box as cb
import main  # noqa: F401  (loads the modules in their usual order)


def _old(label: bytes, env: str, text: bytes) -> bytes:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(label + cb._secret(env)).digest())).encrypt(text)


def test_round_trip_and_format():
    t = cb.seal_bytes(b"patient file", "phi", "PHI_ENC_KEY")
    assert t.startswith(b"A2:") and cb.is_current(t)
    raw = base64.urlsafe_b64decode(t[3:])
    assert len(raw) == 12 + len(b"patient file") + 16              # 96-bit nonce, 128-bit tag
    assert cb.open_bytes(t, "phi", "PHI_ENC_KEY") == b"patient file"
    assert cb.seal_bytes(b"x", "phi", "PHI_ENC_KEY") != cb.seal_bytes(b"x", "phi", "PHI_ENC_KEY")   # fresh nonce each time
    assert len(cb._key("phi", "PHI_ENC_KEY")) == 32                 # 256-bit key


def test_purposes_and_tampering_are_refused():
    t = cb.seal_text("secret", "mfa", "MFA_ENC_KEY")
    with pytest.raises(Exception):
        cb.open_text(t, "clearinghouse", "MFA_ENC_KEY")              # sealed for another use
    raw = bytearray(base64.urlsafe_b64decode(t[3:]))
    raw[-1] ^= 1
    with pytest.raises(Exception):
        cb.open_bytes(b"A2:" + base64.urlsafe_b64encode(bytes(raw)), "mfa", "MFA_ENC_KEY")


def test_old_values_still_open():
    import clearinghouse as chx
    import patient_intake as pi
    import security
    import tebra
    assert pi.unseal(_old(b"althais-phi|", "PHI_ENC_KEY", b"scan")) == b"scan"
    assert pi.unseal_text(_old(b"althais-phi|", "PHI_ENC_KEY", b"name.pdf").decode()) == "name.pdf"
    assert chx.unseal(_old(b"althais-clearinghouse|", "CLEARINGHOUSE_ENC_KEY", b"key123").decode()) == "key123"
    assert tebra._creds({"secret": _old(b"althais-tebra|", "TEBRA_ENC_KEY", b'{"user":"u"}').decode()}) == {"user": "u"}
    assert security._open(_old(b"althais-mfa|", "MFA_ENC_KEY", b"JBSWY3DP").decode()) == "JBSWY3DP"
    # new values are AES-256-GCM
    assert pi.seal(b"x").startswith(b"A2:") and chx.seal("k").startswith("A2:") and security._seal("s").startswith("A2:")
    assert tebra._seal(json.dumps({"a": 1})).startswith("A2:")
    assert pi.unseal(pi.seal(b"x")) == b"x" and chx.unseal(chx.seal("k")) == "k" and security._open(security._seal("s")) == "s"


def test_old_authenticator_secret_upgrades_on_sign_in():
    import auth
    import security
    from sqlalchemy import select
    secret = security.new_secret()
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == "crypto-up@example.test"))
        if not u:
            u = auth.User(email="crypto-up@example.test", password_hash=auth.hash_password("x" * 12), full_name="C", organization="Crypto Clinic",
                          role="admin", claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u)
        u.mfa_secret, u.mfa_enabled, u.mfa_last_step = _old(b"althais-mfa|", "MFA_ENC_KEY", secret.encode()).decode(), 1, 0
        db.commit(); db.refresh(u)
        import time
        code = security.hotp(secret, int(time.time()) // 30)
        assert security.use_code(db, u, code) == "totp"
        db.refresh(u)
        assert cb.is_current(u.mfa_secret) and security._open(u.mfa_secret) == secret


def test_browser_key_is_per_clinic():
    a, b = cb.browser_key("Clinic A"), cb.browser_key("Clinic B")
    assert a != b and a == cb.browser_key("Clinic A") and len(base64.b64decode(a)) == 32


def test_reencrypt_upgrades_stored_values():
    import auth
    import clearinghouse as chx
    with auth.SessionLocal() as db:
        chx.save_connection(db, "Reenc Clinic", {"partner": "claimmd", "key": _old(b"althais-clearinghouse|", "CLEARINGHOUSE_ENC_KEY", b"abc").decode()})
        db.commit()
    n = cb.reencrypt()
    assert n["clearinghouse"] >= 1
    doc = chx.load_connection("Reenc Clinic")
    assert cb.is_current(doc["key"]) and chx.unseal(doc["key"]) == "abc"
    assert cb.reencrypt()["clearinghouse"] == 0                        # nothing left to do


# ── the browser's copy (static/v4/store.js) ─────────────────────────────────
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

RUNNER = r"""
const fs = require('fs'), vm = require('vm'), { webcrypto } = require('crypto');
const store = {}, ls = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
Object.assign(store, input.storage || {});
const window = { __ALTHAIS_USER__: { organization: 'Demo Family Clinic' }, __ALTHAIS_BK__: input.key, addEventListener() {}, TextEncoder, crypto: webcrypto };
const ctx = { window, localStorage: ls, document: { addEventListener() {}, hidden: false }, TextEncoder, TextDecoder, crypto: webcrypto, atob, setTimeout, clearTimeout, console };
window.window = window; vm.createContext(ctx);
for (const f of ['vendor/althais-crypto.min.js', 'store.js']) vm.runInContext(fs.readFileSync(process.argv[1] + f, 'utf8'), ctx);
const D = window.D;
if (input.patient) { D.S.patients.push(input.patient); D.flush(); }
process.stdout.write(JSON.stringify({ storage: store, key: D.KEY, patients: D.S.patients, hash: D.crypto.hash('abc') }));
"""


def run_store(key, storage=None, patient=None):
    out = subprocess.run([NODE, "-e", RUNNER, os.path.join(ROOT, "static", "v4") + os.sep], input=json.dumps({"key": key, "storage": storage or {}, "patient": patient}),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _py_open(stored: str, key_b64: str) -> dict:
    """Decrypt the browser's copy in Python: proves it's standard AES-256-GCM, not something home-made."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    head, n, body = stored.split(":", 2)
    assert head == "A2u"
    n, acc, bits, out = int(n), 0, 0, bytearray()
    for ch in body:
        acc = (acc << 15) | (ord(ch) - 0x1000); bits += 15
        while bits >= 8 and len(out) < n:
            bits -= 8; out.append((acc >> bits) & 0xFF)
        acc &= (1 << bits) - 1
    assert len(out) == n
    return json.loads(AESGCM(base64.b64decode(key_b64)).decrypt(bytes(out[:12]), bytes(out[12:]), b"althais-browser-store"))


@pytest.mark.skipif(not NODE, reason="Node isn't installed")
def test_browser_copy_is_aes256_gcm_and_old_copies_are_upgraded():
    key = cb.browser_key(cb.store_slug("Demo Family Clinic"))
    r = run_store(key, patient={"mrn": "MRN1", "first": "Ada", "last": "Lovelace"})
    stored = r["storage"][r["key"]]
    assert r["key"] == "althais.v4.store.v1::demo_family_clinic" and stored.startswith("A2u:") and "Lovelace" not in stored
    assert all(0x1000 <= ord(c) <= 0x8FFF for c in stored.split(":", 2)[2])               # no broken surrogates
    assert _py_open(stored, key)["patients"][0]["last"] == "Lovelace"
    assert r["hash"] == hashlib.sha256(b"abc").hexdigest()
    # reading it back
    again = run_store(key, storage=r["storage"])
    assert again["patients"][0]["mrn"] == "MRN1"
    # an unencrypted copy saved before this change is read, then saved encrypted
    plain = json.dumps(_py_open(stored, key))
    up = run_store(key, storage={r["key"]: plain})
    assert up["patients"][0]["mrn"] == "MRN1" and up["storage"][r["key"]].startswith("A2u:")
    # another clinic's key can't open it: it's set aside and this browser starts again
    other = run_store(cb.browser_key("another_clinic"), storage=r["storage"])
    assert other["patients"] == [] and other["storage"][r["key"] + "::undecryptable"] == stored


def test_v4_page_carries_the_clinic_key_and_isnt_cached():
    import re
    import auth
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == "crypto-page@example.test"))
        if not u:
            u = auth.User(email="crypto-page@example.test", password_hash=auth.hash_password("x" * 12), full_name="P", organization="Key Page Clinic",
                          role="admin", claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u); db.commit(); db.refresh(u)
        db.expunge(u)
    main.app.dependency_overrides[auth.current_user] = lambda: u
    try:
        r = TestClient(main.app).get("/v4/emr/patients", follow_redirects=False)
    finally:
        main.app.dependency_overrides.clear()
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert re.search(r'__ALTHAIS_BK__ = "([^"]+)"', r.text).group(1) == cb.browser_key("key_page_clinic")
    assert "althais-crypto.min.js" in r.text
