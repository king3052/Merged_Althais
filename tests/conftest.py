import os
import sys
import tempfile

# a throwaway database and keys before any app module is imported
_DB = os.path.join(tempfile.mkdtemp(prefix="althais-tests-"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _DB)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("ADMIN_PASSWORD", "x")
os.environ.setdefault("GROQ_API_KEY", "x")
os.environ.setdefault("BILLING_JOBS_DISABLED", "1")
os.environ.setdefault("CPT_NOTICE_ENFORCE", "0")   # tested on its own in test_clinic_ready.py
os.environ.setdefault("MFA_ENFORCE", "0")   # two-step login is tested on its own in test_security.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.environ.get("ENVELOPE_LOCAL") == "1":      # the whole suite with envelope encryption: keys made up front, as at startup
    import key_store
    key_store.ensure_keys()
