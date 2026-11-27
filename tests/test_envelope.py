"""Envelope encryption: data keys per purpose, stored only wrapped by a key-encryption key (Cloud KMS in production)."""
import base64

import pytest
from sqlalchemy import select

import crypto_box as cb
import key_store as ks


class FakeKMS:
    """Stands in for Cloud KMS: wraps with its own secret, counts calls, can be broken, and has versions."""
    name = "projects/t/locations/us/keyRings/r/cryptoKeys/k"

    def __init__(self):
        self.version, self.calls, self.down = 1, {"wrap": 0, "unwrap": 0}, False
        self._inner = ks.LocalKEK()

    def wrap(self, plain, aad):
        if self.down:
            raise RuntimeError("KMS unavailable")
        self.calls["wrap"] += 1
        w, _ = self._inner.wrap(plain, aad)
        return w, f"{self.name}/cryptoKeyVersions/{self.version}"

    def unwrap(self, wrapped, aad):
        if self.down:
            raise RuntimeError("KMS unavailable")
        self.calls["unwrap"] += 1
        return self._inner.unwrap(wrapped, aad)


@pytest.fixture
def kms(monkeypatch):
    fake = FakeKMS()
    monkeypatch.setenv("KMS_KEY", fake.name)
    monkeypatch.setattr(ks, "_kek_override", fake)
    ks.forget()
    with ks.SessionLocal() as db:                 # only this fake KMS's keys: keys under other KEKs belong to other tests
        for r in db.scalars(select(ks.DataKey).where(ks.DataKey.kek == fake.name)):
            db.delete(r)
        db.commit()
    yield fake
    ks.forget()


def test_off_without_kms_values_seal_as_before(monkeypatch):
    monkeypatch.delenv("KMS_KEY", raising=False)
    monkeypatch.delenv("ENVELOPE_LOCAL", raising=False)
    monkeypatch.delenv("ENVELOPE_LOCAL", raising=False)
    t = cb.seal_text("hello", "payfile", "PHI_ENC_KEY")
    assert t.startswith("A2:") and cb.open_text(t, "payfile", "PHI_ENC_KEY") == "hello"


def test_values_seal_with_a_wrapped_data_key(kms):
    t = cb.seal_text("835 file with a patient", "payfile", "PHI_ENC_KEY")
    assert t.startswith("A3:") and cb.open_text(t, "payfile", "PHI_ENC_KEY") == "835 file with a patient"
    kid = t.split(":")[1]
    with ks.SessionLocal() as db:
        row = db.get(ks.DataKey, kid)
        assert row.purpose == "payfile" and row.kek == kms.name and row.kek_version.endswith("/1")
        assert len(base64.b64decode(row.wrapped)) > 32            # stored wrapped, never the 32 raw bytes
    assert cb.is_current(t)


def test_each_purpose_has_its_own_key_and_cant_open_another(kms):
    a = cb.seal_text("x", "payfile", "PHI_ENC_KEY")
    b = cb.seal_text("x", "recovery", "PHI_ENC_KEY")
    assert a.split(":")[1] != b.split(":")[1]
    with pytest.raises(Exception):
        cb.open_text(a, "recovery", "PHI_ENC_KEY")


def test_kms_is_asked_once_per_key_per_process(kms):
    for i in range(20):
        cb.open_text(cb.seal_text(str(i), "payfile", "PHI_ENC_KEY"), "payfile", "PHI_ENC_KEY")
    assert kms.calls == {"wrap": 1, "unwrap": 0}
    t = cb.seal_text("again", "payfile", "PHI_ENC_KEY")
    ks.forget()                                                   # a new process
    assert cb.open_text(t, "payfile", "PHI_ENC_KEY") == "again" and kms.calls["unwrap"] == 1


def test_values_from_before_still_open(kms, monkeypatch):
    monkeypatch.delenv("KMS_KEY")
    monkeypatch.delenv("ENVELOPE_LOCAL", raising=False)
    old = cb.seal_text("old value", "payfile", "PHI_ENC_KEY")
    monkeypatch.setenv("KMS_KEY", kms.name)
    assert old.startswith("A2:") and cb.open_text(old, "payfile", "PHI_ENC_KEY") == "old value"
    assert not cb.is_current(old)


def test_kms_down_stops_writes_instead_of_weakening_them(kms):
    kms.down = True
    with pytest.raises(RuntimeError):
        cb.seal_text("x", "payfile", "PHI_ENC_KEY")


def test_rotating_a_data_key_keeps_old_values_readable(kms):
    old = cb.seal_text("before", "payfile", "PHI_ENC_KEY")
    new_id = ks.rotate("payfile")
    new = cb.seal_text("after", "payfile", "PHI_ENC_KEY")
    assert new.split(":")[1] == new_id != old.split(":")[1]
    ks.forget()
    assert cb.open_text(old, "payfile", "PHI_ENC_KEY") == "before" and cb.open_text(new, "payfile", "PHI_ENC_KEY") == "after"


def test_rewrapping_after_a_kms_rotation_touches_no_data(kms):
    t = cb.seal_text("data", "payfile", "PHI_ENC_KEY")
    with ks.SessionLocal() as db:
        before = db.get(ks.DataKey, t.split(":")[1]).wrapped
    kms.version = 2
    assert ks.rewrap() >= 1
    with ks.SessionLocal() as db:
        row = db.get(ks.DataKey, t.split(":")[1])
        assert row.wrapped != before and row.kek_version.endswith("/2")
    ks.forget()
    assert cb.open_text(t, "payfile", "PHI_ENC_KEY") == "data"     # the stored value itself never changed


def test_reseal_moves_stored_patient_data_to_the_envelope(kms, monkeypatch):
    import monitor
    import datetime as dt
    monkeypatch.delenv("KMS_KEY")
    monkeypatch.delenv("ENVELOPE_LOCAL", raising=False)
    with ks.SessionLocal() as db:
        db.add(monitor.PaymentFile(org_key="Env Clinic", name="a.835", sha="envtest1", kind="835",
                                   received_at=dt.datetime(2026, 11, 1), source="upload",
                                   sealed=cb.seal_text("ISA*old", "payfile", "PHI_ENC_KEY")))
        db.commit()
    monkeypatch.setenv("KMS_KEY", kms.name)
    out = cb.reseal_stores()
    assert out["payment_files.sealed"] >= 1
    with ks.SessionLocal() as db:
        f = db.scalars(select(monitor.PaymentFile).where(monitor.PaymentFile.sha == "envtest1")).one()
        assert f.sealed.startswith("A3:") and cb.open_text(f.sealed, "payfile", "PHI_ENC_KEY") == "ISA*old"
        db.delete(f)
        db.commit()
    assert cb.reseal_stores()["payment_files.sealed"] == 0          # safe to run again


def test_admin_sees_key_ids_never_keys(kms):
    cb.seal_text("x", "payfile", "PHI_ENC_KEY")
    st = ks.status()
    assert st["envelope"] is True and st["kek"] == kms.name
    k = next(x for x in st["dataKeys"] if x["purpose"] == "payfile" and x["kekVersion"] == "1")
    assert set(k) == {"id", "purpose", "kekVersion", "created", "retired"} and k["kekVersion"] == "1"


def test_local_kek_for_development(monkeypatch):
    monkeypatch.delenv("KMS_KEY", raising=False)
    monkeypatch.setenv("ENVELOPE_LOCAL", "1")
    monkeypatch.setattr(ks, "_kek_override", None)
    ks.forget()
    t = cb.seal_text("dev", "mfa", "MFA_ENC_KEY")
    assert t.startswith("A3:") and cb.open_text(t, "mfa", "MFA_ENC_KEY") == "dev"
    ks.forget()


def test_admin_routes_are_admin_only():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    assert c.get("/api/admin/encryption").status_code in (401, 403)
    assert c.post("/api/admin/encryption/reseal").status_code in (401, 403)
    assert c.post("/api/admin/encryption/rewrap").status_code in (401, 403)
