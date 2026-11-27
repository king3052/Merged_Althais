"""Envelope encryption keys (crypto_box.py): the data keys that encrypt stored values, kept only in wrapped form.

How it fits together:
  - A key-encryption key (KEK) lives in Google Cloud KMS (KMS_KEY, e.g.
    projects/althais/locations/us-central1/keyRings/althais/cryptoKeys/data-kek). It never leaves KMS: Althais asks KMS
    to wrap and unwrap, and Google logs every use.
  - Each purpose (payment files, Recovery cases, intake documents, clearinghouse logins, two-step login secrets...) has
    a random 256-bit data key (DEK). Its plaintext exists only in memory; the database holds it wrapped by the KEK,
    with the purpose and key id bound in as associated data.
  - Rotating the KEK (KMS does it on a schedule) needs no re-encryption of data: `python crypto_box.py rewrap` re-wraps
    the few data keys with the new KEK version. Rotating a data key (`python crypto_box.py rotate <purpose>`) starts
    sealing new values with a fresh key; old values keep opening with the old one until re-sealed.

Without KMS_KEY, nothing changes: values are sealed as before (keys derived from Secret Manager values). Tests and local
development can set ENVELOPE_LOCAL=1 to use the envelope format with a local KEK derived from SECRET_KEY.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import secrets
import threading
from typing import Dict, Optional, Tuple

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base, SessionLocal, engine


class DataKey(Base):
    __tablename__ = "data_keys"
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(64), index=True)
    wrapped: Mapped[str] = mapped_column(Text)                 # base64 of the KEK's ciphertext of the 32-byte data key
    kek: Mapped[str] = mapped_column(String(300))              # KMS key name, or "local"
    kek_version: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    retired_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


Base.metadata.create_all(engine, tables=[DataKey.__table__])

_lock = threading.Lock()
_plain: Dict[str, bytes] = {}            # key id -> data key, for this process only
_active: Dict[Tuple[str, str], str] = {}  # (kek, purpose) -> key id


def kek_name() -> str:
    """The KEK in use: the KMS key, "local" for tests and development, or "" when envelope encryption is off."""
    if os.environ.get("KMS_KEY"):
        return os.environ["KMS_KEY"].strip()
    if os.environ.get("ENVELOPE_LOCAL") == "1":
        return "local"
    return ""


def _aad(purpose: str, key_id: str) -> bytes:
    return f"althais-dek|{purpose}|{key_id}".encode()


# ── the KEK: Cloud KMS over its REST API (google-auth is already installed) ──
class KMS:
    def __init__(self, name: str):
        self.name = name
        self._session = None

    def _s(self):
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloudkms"])
            self._session = AuthorizedSession(creds)
        return self._session

    def wrap(self, plain: bytes, aad: bytes) -> Tuple[str, str]:
        r = self._s().post(f"https://cloudkms.googleapis.com/v1/{self.name}:encrypt", timeout=15,
                           json={"plaintext": base64.b64encode(plain).decode(), "additionalAuthenticatedData": base64.b64encode(aad).decode()})
        if r.status_code != 200:
            raise RuntimeError(f"KMS encrypt failed ({r.status_code}): {r.text[:200]}")
        j = r.json()
        return j["ciphertext"], j.get("name", "")

    def unwrap(self, wrapped: str, aad: bytes) -> bytes:
        r = self._s().post(f"https://cloudkms.googleapis.com/v1/{self.name}:decrypt", timeout=15,
                           json={"ciphertext": wrapped, "additionalAuthenticatedData": base64.b64encode(aad).decode()})
        if r.status_code != 200:
            raise RuntimeError(f"KMS decrypt failed ({r.status_code}): {r.text[:200]}")
        return base64.b64decode(r.json()["plaintext"])


class LocalKEK:
    """Tests and development only: AES-256-GCM with a key derived from SECRET_KEY."""
    name = "local"

    def _k(self) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        secret = (os.environ.get("SECRET_KEY") or "dev-only-insecure-change-me").encode()
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"althais-local-kek-v1", info=b"althais|kek").derive(secret)

    def wrap(self, plain: bytes, aad: bytes) -> Tuple[str, str]:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        n = os.urandom(12)
        return base64.b64encode(n + AESGCM(self._k()).encrypt(n, plain, aad)).decode(), "local/1"

    def unwrap(self, wrapped: str, aad: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(wrapped)
        return AESGCM(self._k()).decrypt(raw[:12], raw[12:], aad)


_kek_override = None          # tests swap in a fake KMS


def _kek(name: str):
    if _kek_override is not None:
        return _kek_override
    return LocalKEK() if name == "local" else KMS(name)


def _now() -> dt.datetime:
    return dt.datetime.utcnow().replace(microsecond=0)


def active_key(purpose: str) -> Tuple[str, bytes]:
    """(key id, data key) to seal new values for this purpose, creating the data key the first time."""
    name = kek_name()
    if not name:
        raise RuntimeError("envelope encryption is off (no KMS_KEY)")
    with _lock:
        kid = _active.get((name, purpose))
        if kid and kid in _plain:
            return kid, _plain[kid]
    with SessionLocal() as db:
        row = db.scalars(select(DataKey).where(DataKey.purpose == purpose, DataKey.kek == name, DataKey.retired_at.is_(None))
                         .order_by(DataKey.created_at.desc())).first()
        if row is None:
            kid = secrets.token_hex(8)
            plain = os.urandom(32)
            wrapped, version = _kek(name).wrap(plain, _aad(purpose, kid))
            row = DataKey(id=kid, purpose=purpose, wrapped=wrapped, kek=name, kek_version=version, created_at=_now())
            db.add(row)
            db.commit()
            print(f"[keys] new data key {kid} for {purpose} under {name}", flush=True)
        else:
            plain = _kek(row.kek).unwrap(row.wrapped, _aad(row.purpose, row.id))
        kid = row.id
    with _lock:
        _plain[kid] = plain
        _active[(name, purpose)] = kid
    return kid, plain


def key_by_id(kid: str, purpose: str) -> bytes:
    """The data key a stored value names. Raises if it doesn't exist or belongs to another purpose."""
    with _lock:
        if kid in _plain:
            return _plain[kid]
    with SessionLocal() as db:
        row = db.get(DataKey, kid)
        if row is None or row.purpose != purpose:
            raise ValueError("unknown data key for this purpose")
        plain = _kek(row.kek).unwrap(row.wrapped, _aad(row.purpose, row.id))
    with _lock:
        _plain[kid] = plain
    return plain


def rotate(purpose: str) -> str:
    """Retire this purpose's data key: new values get a fresh key, old ones keep opening with theirs."""
    name = kek_name()
    with SessionLocal() as db:
        for row in db.scalars(select(DataKey).where(DataKey.purpose == purpose, DataKey.kek == name, DataKey.retired_at.is_(None))):
            row.retired_at = _now()
        db.commit()
    with _lock:
        _active.pop((name, purpose), None)
    return active_key(purpose)[0]


def rewrap() -> int:
    """Re-wrap every data key with the KEK's current primary version (after a KMS rotation). Data isn't touched."""
    n = 0
    with SessionLocal() as db:
        for row in db.scalars(select(DataKey)):
            k = _kek(row.kek)
            plain = k.unwrap(row.wrapped, _aad(row.purpose, row.id))
            row.wrapped, row.kek_version = k.wrap(plain, _aad(row.purpose, row.id))
            n += 1
        db.commit()
    return n


PURPOSES = ["payfile", "recovery", "clinic-record", "records-intake", "records-intake-file", "claim-attachment", "phi",
            "clearinghouse", "tebra", "mfa", "opendental", "dental-phi"]


def ensure_keys() -> int:
    """Create (or unwrap) the data key for every known purpose: at startup, so a KMS problem shows in the logs at once
    and keys aren't first created in the middle of another write."""
    if not kek_name():
        return 0
    for p in PURPOSES:
        active_key(p)
    return len(PURPOSES)


def status() -> dict:
    with SessionLocal() as db:
        rows = list(db.scalars(select(DataKey)))
    return {"envelope": bool(kek_name()), "kek": kek_name() or None,
            "dataKeys": [{"id": r.id, "purpose": r.purpose, "kekVersion": (r.kek_version or "").rsplit("/", 1)[-1],
                          "created": r.created_at.isoformat() if r.created_at else None,
                          "retired": r.retired_at.isoformat() if r.retired_at else None} for r in rows]}


def forget() -> None:
    """Drop the in-memory data keys (tests)."""
    with _lock:
        _plain.clear()
        _active.clear()
