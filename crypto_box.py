"""Encryption for data Althais stores itself: AES-256-GCM.

Used for patient intake documents and their extracted data (patient_intake.py), saved clearinghouse and EMR
connections (clearinghouse.py, drchrono.py, tebra.py), two-step login secrets (security.py), and the key that encrypts
each clinic's data in the browser (browser_key, used by static/v4/store.js).

Each use has its own 256-bit key, derived with HKDF-SHA256 from that use's environment variable (PHI_ENC_KEY,
CLEARINGHOUSE_ENC_KEY, TEBRA_ENC_KEY, MFA_ENC_KEY), or SECRET_KEY when it isn't set. Every value gets a fresh random
96-bit nonce, and the purpose is bound in as associated data, so a value sealed for one use can't be opened as another.

Format: "A2:" + base64url(nonce || ciphertext || 16-byte tag). Values written before this (Fernet, AES-128) still open,
and are re-sealed with AES-256-GCM the next time they're saved, or all at once with `python crypto_box.py reencrypt`.

Envelope encryption (key_store.py): with KMS_KEY set, new values are sealed "A3:" with a random data key per purpose,
which is stored only wrapped by a Cloud KMS key. "A2:" values keep opening and move to "A3:" when re-saved or with
`python crypto_box.py reencrypt`. `python crypto_box.py rewrap` after a KMS rotation, `rotate <purpose>` for a new
data key, `status` to see the keys.
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = b"A2:"
_DEV = "dev-only-insecure-change-me"


def _secret(env: str) -> bytes:
    return (os.environ.get(env) or os.environ.get("SECRET_KEY") or _DEV).encode()


def _key(purpose: str, env: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"althais-aes256gcm-v2", info=("althais|" + purpose).encode()).derive(_secret(env))


ENVELOPE = b"A3:"      # "A3:" + data key id + ":" + base64url(nonce || ciphertext || tag): envelope encryption (key_store.py)


def envelope_on() -> bool:
    return bool(os.environ.get("KMS_KEY") or os.environ.get("ENVELOPE_LOCAL") == "1")


def seal_bytes(data: bytes, purpose: str, env: str) -> bytes:
    nonce = os.urandom(12)
    if envelope_on():
        import key_store
        kid, dek = key_store.active_key(purpose)      # fails closed: with KMS_KEY set, a KMS outage stops writes, not security
        aad = f"{purpose}|{kid}".encode()
        return ENVELOPE + kid.encode() + b":" + base64.urlsafe_b64encode(nonce + AESGCM(dek).encrypt(nonce, data, aad))
    return PREFIX + base64.urlsafe_b64encode(nonce + AESGCM(_key(purpose, env)).encrypt(nonce, data, purpose.encode()))


def is_current(token) -> bool:
    """Sealed in the format new values get now (so re-encryption can skip it)."""
    if isinstance(token, str):
        token = token.encode()
    if not token:
        return False
    return token.startswith(ENVELOPE) if envelope_on() else token.startswith(PREFIX) or token.startswith(ENVELOPE)


def open_bytes(token, purpose: str, env: str, legacy_label: bytes = b"") -> bytes:
    """Decrypt a sealed value. legacy_label: the label the old Fernet key was derived with (b"althais-phi|" ...)."""
    if isinstance(token, str):
        token = token.encode()
    if token.startswith(ENVELOPE):
        import key_store
        kid, _, body = token[len(ENVELOPE):].partition(b":")
        dek = key_store.key_by_id(kid.decode(), purpose)
        raw = base64.urlsafe_b64decode(body)
        return AESGCM(dek).decrypt(raw[:12], raw[12:], f"{purpose}|{kid.decode()}".encode())
    if token.startswith(PREFIX):
        raw = base64.urlsafe_b64decode(token[len(PREFIX):])
        return AESGCM(_key(purpose, env)).decrypt(raw[:12], raw[12:], purpose.encode())
    if not legacy_label:
        raise ValueError("Not an AES-256-GCM value.")
    from cryptography.fernet import Fernet   # values written before AES-256-GCM
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(legacy_label + _secret(env)).digest())).decrypt(token)


def seal_text(s: str, purpose: str, env: str) -> str:
    return seal_bytes(s.encode(), purpose, env).decode()


def open_text(token, purpose: str, env: str, legacy_label: bytes = b"") -> str:
    return open_bytes(token, purpose, env, legacy_label).decode()


def store_slug(organization: str, email: str = "") -> str:
    """The name the browser files a clinic's data under (static/v4/store.js builds the same one)."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", (organization or email or "local").strip().lower())


def browser_key(slug: str) -> str:
    """The AES-256 key for one clinic's data in the browser (base64), from BROWSER_ENC_KEY (else PHI_ENC_KEY, else
    SECRET_KEY). It's only sent inside a signed-in page that browsers are told not to keep, so the browser's copy
    can't be read after sign-out, or by anyone who copies it off the computer, without signing in to that clinic.
    Changing BROWSER_ENC_KEY makes what's in browsers unreadable: give it its own value so other keys can change."""
    secret = (os.environ.get("BROWSER_ENC_KEY") or os.environ.get("PHI_ENC_KEY") or os.environ.get("SECRET_KEY") or _DEV).encode()
    k = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"althais-browser-store-v1", info=("althais|browser|" + (slug or "")).encode()).derive(secret)
    return base64.b64encode(k).decode()


# ── re-encrypt everything written before AES-256-GCM ────────────────────────
def reencrypt() -> dict:
    """Re-seal every stored value that still uses the old format. Safe to run more than once."""
    import json
    from sqlalchemy import select
    from auth import OrgSettings, SessionLocal, User
    import clearinghouse as chx
    import patient_intake as pi
    import security
    import tebra
    n = {"mfa": 0, "intake_files": 0, "intake_text": 0, "clearinghouse": 0, "drchrono": 0, "tebra": 0}
    with SessionLocal() as db:
        for u in db.scalars(select(User).where(User.mfa_secret.isnot(None))):
            if u.mfa_secret and not is_current(u.mfa_secret):
                u.mfa_secret = security._seal(security._open(u.mfa_secret)); n["mfa"] += 1
        for d in db.scalars(select(pi.PatientDocument)):
            if d.data and not is_current(d.data):
                d.data = pi.seal(pi.unseal(d.data)); n["intake_files"] += 1
            for f in ("filename", "extraction"):
                v = getattr(d, f)
                if v and not is_current(v):
                    setattr(d, f, pi.seal_text(pi.unseal_text(v))); n["intake_text"] += 1
        for row in db.scalars(select(OrgSettings).where(OrgSettings.category.in_(("clearinghouse", "drchrono", tebra.CATEGORY)))):
            doc = json.loads(row.data or "{}")
            fields = {"clearinghouse": ("key",), "drchrono": ("access", "refresh")}.get(row.category, ("secret",))
            changed = False
            for f in fields:
                if doc.get(f) and not is_current(doc[f]):
                    if row.category == tebra.CATEGORY:
                        doc[f] = tebra._seal(tebra._unseal(doc[f]))
                    else:
                        doc[f] = chx.seal(chx.unseal(doc[f]))
                    changed = True
            if changed:
                row.data = json.dumps(doc)
                n["tebra" if row.category == tebra.CATEGORY else row.category] += 1
        db.commit()
    n.update(reseal_stores())
    return n


# the main stores of patient data: (module, model, column, purpose, env)
STORES = [
    ("monitor", "PaymentFile", "sealed", "payfile", "PHI_ENC_KEY"),
    ("recovery", "RecoveryCase", "sealed", "recovery", "PHI_ENC_KEY"),
    ("clinic_records", "ClinicRecord", "data", "clinic-record", "PHI_ENC_KEY"),
    ("records_intake", "IntakeDoc", "sealed", "records-intake", "PHI_ENC_KEY"),
    ("records_intake", "IntakeDoc", "data", "records-intake-file", "PHI_ENC_KEY"),
    ("records_intake", "IntakeLink", "sealed", "records-intake", "PHI_ENC_KEY"),
]


def reseal_stores(batch: int = 200) -> dict:
    """Re-seal every value in STORES that isn't in the current format (moves "A2:" to "A3:" once KMS_KEY is set).
    Opens and re-seals in memory, a batch at a time; safe to run more than once and to stop halfway."""
    import importlib
    from sqlalchemy import select
    from auth import SessionLocal
    out = {}
    for mod, model_name, col, purpose, env in STORES:
        model = getattr(importlib.import_module(mod), model_name)
        done = 0
        with SessionLocal() as db:
            for row in db.scalars(select(model)).yield_per(batch):
                v = getattr(row, col)
                if v and not is_current(v):
                    setattr(row, col, seal_text(open_text(v, purpose, env), purpose, env))
                    done += 1
                    if done % batch == 0:
                        db.commit()
            db.commit()
        out[f"{model.__tablename__}.{col}"] = done
    return out


if __name__ == "__main__":
    import json as _json
    import sys
    cmd = sys.argv[1:]
    if cmd == ["reencrypt"]:
        import main  # noqa: F401  (loads every table)
        print(reencrypt())
    elif cmd == ["rewrap"]:
        import key_store
        print({"rewrapped": key_store.rewrap()})
    elif len(cmd) == 2 and cmd[0] == "rotate":
        import key_store
        print({"purpose": cmd[1], "newKey": key_store.rotate(cmd[1])})
    elif cmd == ["status"]:
        import key_store
        print(_json.dumps(key_store.status(), indent=1))
    else:
        print("Usage: python crypto_box.py reencrypt | rewrap | rotate <purpose> | status")
