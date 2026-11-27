# Encryption at rest: envelope encryption with Cloud KMS

## How it works

```
Cloud KMS key (KEK, never leaves Google; rotates every 90 days; every use logged)
   └─ wraps ─► data key per purpose (payfile, recovery, phi, mfa, clearinghouse, ...)   stored wrapped in data_keys
                  └─ encrypts ─► each stored value, AES-256-GCM, "A3:<key id>:<nonce|ciphertext|tag>"
```

- The database never holds a usable key: data keys are stored wrapped by the KMS key, with their purpose and id bound in.
- A stolen database dump or backup is unreadable without access to the KMS key, which needs Althais's service account.
- KMS rotation needs no re-encryption: `POST /api/admin/encryption/rewrap` re-wraps the data keys (a few rows).
- A purpose's data key can be replaced (`python crypto_box.py rotate payfile`); old values keep opening with the old key.
- Values sealed before (`A2:`, keys derived from Secret Manager values) keep opening, and move with
  `POST /api/admin/encryption/reseal`.
- If KMS can't be reached, writes of encrypted data stop with an error instead of falling back to weaker encryption.

Code: `crypto_box.py` (seal/open), `key_store.py` (data keys, KMS over its REST API), tests in `tests/test_envelope.py`
(and the whole suite passes with `ENVELOPE_LOCAL=1`, which uses a local test KEK).

## Turning it on (Cloud Shell, once)

```bash
gcloud services enable cloudkms.googleapis.com --project althais
gcloud kms keyrings create althais --location us-central1 --project althais
gcloud kms keys create data-kek --keyring althais --location us-central1 --purpose encryption \
  --rotation-period 90d --next-rotation-time 2027-03-01T00:00:00Z --project althais
gcloud kms keys add-iam-policy-binding data-kek --keyring althais --location us-central1 --project althais \
  --member serviceAccount:althais-run@althais.iam.gserviceaccount.com --role roles/cloudkms.cryptoKeyEncrypterDecrypter
printf 'projects/althais/locations/us-central1/keyRings/althais/cryptoKeys/data-kek' | \
  gcloud secrets create KMS_KEY --project althais --replication-policy=automatic --labels=app=althais --data-file=-
gcloud secrets add-iam-policy-binding KMS_KEY --project althais \
  --member serviceAccount:althais-run@althais.iam.gserviceaccount.com --role roles/secretmanager.secretAccessor
```

Then deploy. The logs should show `[keys] envelope encryption on: 12 data keys ready under projects/althais/...`.
Then, signed in as admin, `POST /api/admin/encryption/reseal` once, and `GET /api/admin/encryption` to see the keys.

## Never

- Never destroy a KMS key version that still wraps a data key: everything sealed under it becomes unreadable for good.
  KMS waits 30 days before destroying a version; run `rewrap` after every rotation so old versions stop being needed.
- Never copy a data key or the `data_keys` table somewhere else as a "backup": the Cloud SQL backups already hold it,
  wrapped, and it's useless without KMS, which is the point.
