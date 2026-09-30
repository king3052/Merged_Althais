# Billing Activation

Revenue › Billing Activation gets a clinic ready to send claims. Until it's done, everything else in Althais works; only
sending a claim waits, and claims are kept (held) with the exact reason instead of being sent.

Code: `billing_activation.py` (data, rules, jobs, API), `billing_registry.py` (requirements), `clearinghouse.py`
(connector layer), `templates/revenue_billing_activation.html` (owner, biller and provider views), the **Billing** page
in the Staff Portal (`templates/portal.html`), and the send check in the EMR (`runEdiAnimation` in `templates/dashboard.html`).

## What's real today, and what isn't

| Area | Status |
|---|---|
| Biller onboarding (training, practice claim, acknowledgment, access approval) | **Working.** Only people whose Staff › Roles role is Biller get it. |
| Clinic billing profile, providers, locations, payers | **Working.** Prefilled from Clinic Onboarding and staff records, never marked confirmed until an owner confirms. |
| NPI checks | **Working, real integration:** the public NPPES NPI Registry API (organization and individual NPIs). |
| Document reading (W-9, IRS letter, payer approval letter, license, malpractice, business license, CLIA) | **Working** when `ANTHROPIC_API_KEY` is set (Claude reads; rules decide; uncertain or mismatched documents go to a person). Without the key, every document goes to a person. Bank letters are stored but never read for fields. |
| Requirements registry | **Working, v2026.09.1**, with a documented source per requirement. **Not yet reviewed** by a person against every source (`billing_registry.REVIEW`); the screens say so. |
| Signatures and attestations | **Working** in Althais, signed only by the named person's own login. Payer forms that need a signature on the payer's own form (e.g. in PECOS) are still signed there by that person. |
| Application packets | **Working:** prefilled from confirmed data, downloadable by owners, with a manual submission task. |
| Submitting enrollments electronically | **Not available.** No partner integration yet. Every application is a prepared packet plus a manual task; the owner records the reference number. |
| Clearinghouse connection, payer lists, claim sending, 999/277CA/835 | **Not available (partner access needed).** `clearinghouse.NotConnected` supports nothing. Claims are held with "Althais can send claims through a connected clearinghouse" as the blocker. |
| Webhooks | **Built and tested** (HMAC-signed, idempotent, stale-update safe). Nothing will call them until a partner connector exists. |
| Medicaid and "other" payers | **Tracked, manual.** State programs differ; Althais doesn't prefill them. |
| Test mode (sandbox) | **Working** for clinics listed in `BILLING_SANDBOX_ORGS`. Every test result is stamped `sandbox` and never counts for a production clinic. |

Before this change the EMR *animated* a transmission "via Claim.MD" but sent nothing. It now asks the server first and
shows the real outcome: held (with reasons), sent, or sent as a test.

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables document reading. Optional. |
| `APP_URL` | Link used in notification emails. |
| `CLEARINGHOUSE_PARTNER` | `claimmd`, `availity`, `officeally`, `waystar` or `optum`. Selecting one without a built connector keeps every capability off and the screen says partner access is needed. |
| `CLEARINGHOUSE_API_KEY` | The partner's credential, once a connector exists. |
| `CLEARINGHOUSE_WEBHOOK_SECRET` | HMAC-SHA256 secret for `POST /api/billing/webhooks/<partner>` (header `X-Althais-Signature`). |
| `BILLING_SANDBOX_ORGS` | `|`-separated clinic names put in test mode. Never list a production clinic. |
| `BILLING_JOBS_DISABLED` | Set in tests to stop the background loop. |

### Adding a clearinghouse partner

1. Get the partner's API documentation and credentials. Don't guess endpoints.
2. Subclass `clearinghouse.Connector`, set only the capabilities the partner documents, implement those methods, and
   return `Result(environment="production", ...)` with the partner's reference numbers.
3. Register it in `clearinghouse.PARTNERS` and set `CLEARINGHOUSE_PARTNER` / `CLEARINGHOUSE_API_KEY`.
4. Map the partner's webhook payload in `parse_webhook` to `{event_id, occurred_at, type, item|reference, status, effective_date}`.
5. Run it against the partner's test environment first, with a clinic in `BILLING_SANDBOX_ORGS`, then in production.

## Data (created automatically on startup)

New tables, created by `Base.metadata.create_all` like the rest of the app (no manual migration needed; back up the
database first as usual): `billing_profiles`, `billing_items`, `billing_events`, `billing_tasks`, `billing_signatures`,
`billing_documents`, `biller_access`, `billing_jobs`, `claim_transmissions`. Existing tables are unchanged; claims sent
from the EMR are also recorded in `org_claims`.

Readiness is stored per scope on `billing_items`: clinic, entity, provider, location and payer, plus the transaction
(837P, 835). Enrollment, credentialing, contracting, EDI, ERA/EFT and connectivity are separate tracks.

## The rules

- **Individual vs shared.** Biller onboarding is per person. Clinic, provider and payer setup is per clinic and reused by
  every biller: a new biller creates no new enrollment work.
- **Access.** The Biller role never grants claim submission by itself. An owner approves it, or an owner turns on the
  auto-activation rule (recorded with who and when), which applies only to active billers whose checklist is complete.
  Owners can allow their own login explicitly. Moving someone out of the Biller role stops their access at once.
- **Ready means ready.** A requirement counts only if it's APPROVED / VERIFIED / NOT_REQUIRED, has evidence, is
  effective today or earlier, isn't expired, and was recorded in the clinic's own environment. Unknown, pending,
  expired, future-effective and test results never count. No AI score is involved.
- **Sending a claim** also needs: the sender's active access, the claim passing current checks (payer, member ID, CPT,
  ICD-10, amount, date), and a connector that can send. Otherwise the claim is held unchanged with each blocker and who
  can fix it. Held claims are released automatically only if the owner turned that on, and only after every check passes
  again. The same claim is never sent twice; the same claim number with different content is blocked.
- **Existing enrollment first.** For each payer Althais asks whether the clinic already bills them. If yes, it asks for
  proof and an owner confirms it with evidence; if no, it prepares new applications.
- **Manual confirmation** needs an owner, evidence (a document or a written description), and an effective date for
  approvals. Test-mode documents can't be evidence in production.
- **Tracking.** Claim delivery, clearinghouse acknowledgment, payer decision and payment are tracked separately.

## Background jobs

`billing_jobs` holds persistent work: syncs, NPI checks, payer-rule checks, connection checks, submissions, status polls
and daily reminders. Each has an idempotency key; failures retry with backoff (2, 4, 8, 16 minutes) and after 5 tries
create an escalation task. Independent jobs run side by side (one at a time on SQLite). Overdue tasks get reminders and,
after a week, an escalation; applications pending 30 days get a follow-up task; approvals expiring in 60/30/7 days
notify owners.

## Security

- Every endpoint checks the clinic (tenant) and the role on the server. Billers and providers only see their own tasks
  and requests; Tax ID is masked everywhere except the owner-only application packet.
- Notification emails are generic: no Tax ID, NPI, patient detail or payer letter text.
- Documents are stored in the database, served only to owners and the uploader, with duplicate detection.
- Every change is recorded in `billing_events` (status history) and the clinic audit log.

## Measures

The Activity tab shows four separate numbers: staff readiness (biller added → access approved), application
preparation (started → submitted), payer approval (submitted → approved) and time to the first payer-accepted claim.
Payers decide how long approvals take; Althais measures the time, it doesn't control it.

## Tests

`p8.py` (80 checks) covers role-specific onboarding, shared-setup reuse, access approval and the auto rule, role changes,
evidence rules, future-effective/expired/pending/test-mode approvals, held claims and their exact reasons, duplicate
sends, tenant isolation, document checks, generic notifications, job retries and escalation, webhook signatures,
duplicates and stale updates, sandbox isolation, and held-claim release under policy.
