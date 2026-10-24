# The dental EMR: architecture and plan

For engineers working on the dental side of Althais. FEATURES.md tracks what's done. This page explains how it fits together and what's next.

## Shape

A clinic set to **dental** in `/admin` gets its own screens under `/v4/dental/...`. The shared v4 pages (Overview, Schedule, Patients, Chart, Claims, Denials) redirect there. Medical clinics never see the dental pages and get 403 from `/api/dental`.

| Layer | Files |
|---|---|
| Tables | `dental_db.py`: every row has `org_key` (the clinic), most have `location_id` (the office), and `sample` (synthetic data) |
| Pure logic (tested) | `dental_x12.py` (270/271, 837D, 835), `dental_rcm.py` (frequencies, estimates, coding, narratives, ERA posting, plans, memberships, recall), `dental_clinical.py` (voice perio, perio summary, ambient drafts, synthetic X-rays) |
| API | `dental_api.py` (access, setup, chart, plans, images, schedule), `dental_api_rcm.py` (revenue cycle), `dental_engage.py` (booking, texts, reminders, recall, forms, reviews), `dental_ops.py` (labs, Rx, reports, huddle, imports) |
| Vendors | `dental_vendors.py`: one interface per service, mocks today, `DENTAL_VENDOR_<KIND>` picks the implementation |
| Staff screens | `static/v4/dental-emr.js` (helpers, tooth chart, perio, schedule, patients), `dental-chart.js`, `dental-rcm.js`, `dental-ops.js`, `dental-emr.css` |
| Patient links | `dental_pages.py` and `templates/dental/public.html`: booking, forms, plan presentation, text-to-pay, review. Each is opened by an unguessable token |

Patients are the existing `org_patients` rows, joined by `(org_key, mrn)`, so the Patients list, browser sync and older claim screens keep working.

## HIPAA controls

- **Encryption in transit:** HTTPS in production, with `COOKIE_SECURE` and HSTS (from `security.py`).
- **Encryption at rest:** free text and files with PHI (notes, transcripts, perio readings, narratives, raw X12, images, form answers, messages) are encrypted in the application with AES-256-GCM. This is on top of the database's own disk encryption. Identifiers needed for lookups stay plain.
- **Access control:** each route declares the role group it needs (`need("clinical_read")` and so on). Billers can read the chart for narratives but can't chart. Only providers and admins approve code suggestions. The front desk doesn't see clinical notes.
- **Audit:** every `/api/dental` call is in the access log with the MRN or record. Patient-link actions (viewing or signing a plan, paying, submitting a form, booking) are recorded explicitly.
- **Texts carry the minimum:** a first name, a time, an amount or a link; never procedures or diagnoses. STOP is honored.
- **Synthetic data only in development:** `dental_synth.py` builds the practice. Names are invented, phones are 555-01xx, member IDs start with SYN, and images are stamped SYNTHETIC.

## Before going live: decisions and contracts

1. **Clearinghouse**: Claim.MD is written (`dental_claimmd.py`): 270/271, 837D, predeterminations and 835s, using Althais's Claim.MD account. Before switching `DENTAL_VENDOR_CLEARINGHOUSE=claimmd`: connect the account in /admin, run it with `CLAIMMD_TEST=1` against a Claim.MD test account, and enroll each payer for ERA. 276/277 claim status is still to do. DentalXChange or Vyne would be another subclass.
2. **Attachments**: Claim.MD's automated PDF attachments are written (`DENTAL_VENDOR_ATTACHMENTS=claimmd`): the claim carries one control number, then the X-rays, perio chart and narrative go as one PDF. Check the payer list for which payers take electronic attachments. NEA FastAttach would be another `AttachmentService`.
3. **SMS** (Twilio or Bandwidth): 10DLC registration, a BAA, and the inbound webhook signed with `DENTAL_SMS_WEBHOOK_SECRET`.
4. **Payments** (Stripe or Rectangle Health): a hosted card field that returns a token. We never handle card numbers (keeps PCI scope at SAQ A).
5. **Radiograph AI** (Overjet, Pearl or VideaHealth): their FDA-cleared output, displayed as theirs.
6. **Transcription** with a BAA, for ambient notes from real audio. Optionally an LLM pass on the draft, using the existing `dental_coding` client.
7. **E-prescribing** (DoseSpot or DrFirst): EPCS needs identity proofing per prescriber and the vendor's certified two-factor signing. Our two-step code check is a placeholder for that.
8. **Imaging bridges** per device brand (or VDDS/TWAIN), with images stored in object storage rather than database rows.

## Next engineering work

- Alembic migrations before the first production deploy of these tables.
- Claim status (276/277) polling, secondary claims (COB) and an appeal packet PDF for dental (reuse `appeal.js`).
- Per-location permissions for DSO staff; consolidated reporting across clinics owned by one group.
- Retire the browser-stored encounter flow for dental clinics, so all clinical data is on the server.
- Fee schedules per payer (PPO allowed amounts), so estimates and write-off expectations use contracted fees.
