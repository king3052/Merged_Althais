# Dental EMR features

What's built, what's partly built, and what's stubbed behind a mock until a vendor contract is signed. Keep this current when a feature changes state.

**Done**: works end to end against the server, with tests where there's billing logic.
**Partial**: works, with a known gap listed.
**Mock**: the screen and logic are real, but the third-party call goes to a mock in `dental_vendors.py` (nothing leaves Althais).
**Claim.MD**: a real implementation is built (`dental_claimmd.py`) and is switched on with `DENTAL_VENDOR_<KIND>=claimmd`; the mock stays the default until the switch.

Last updated: 2026-10-24.

## Foundation

| Feature | State | Where |
|---|---|---|
| Dental data on the server (not the browser), scoped to a clinic and a location | Done | `dental_db.py` (34 tables) |
| Encryption at rest for PHI text and files (AES-256-GCM, `PHI_ENC_KEY`) | Done | `dental_db.Sealed`, `SealedJSON` |
| Role-based access: read, front desk, clinical read and write, billing, admin | Done | `dental_api.ROLES`, `need()` |
| Access log of every chart, claim, plan and image read or change, plus patient-link actions | Done | `audit.py` middleware, `dental_api` path labels, `audit.record` on public routes |
| Vendor interfaces with mocks (clearinghouse, attachments, SMS, payments, AI radiographs, transcription, eRx, imaging, reviews) | Done | `dental_vendors.py`; switch with `DENTAL_VENDOR_<KIND>` |
| Claim.MD as the real clearinghouse and attachment service (270/271, 837D, predeterminations, 835 per clinic NPI and Tax ID, PDF attachments); synthetic patients refused | Claim.MD | `dental_claimmd.py`; Althais's Claim.MD account is connected in /admin |
| Synthetic practice (2 offices, 4 providers, 48 patients, 2 years of history), removable | Done | `dental_synth.py`, Practice setup > Synthetic practice |
| Dental navigation; old pages redirect to their dental versions | Done | `static/v4/ui.js`, `v4_preview.DENTAL_INSTEAD` |
| Database migrations (Alembic) | Not started | Tables are created at startup, like the rest of the app |
| Appointments and visit notes in the older v4 encounter screens (medical clinics) are saved on the server, encrypted, and shared between the clinic's browsers | Done | `clinic_records.py`, `static/v4/sync.js` section 5 |

## Core clinical

| Feature | State | Where |
|---|---|---|
| Tooth chart with anatomical teeth, 5-surface boxes, and existing / planned / completed / watch states | Done | `dental-emr.js` `DA.teeth`, Chart tab |
| Chart in three modes: existing, treatment plan, complete today; CDT code picked from tooth and surfaces | Done | Chart tab |
| Perio chart (6 sites, bleeding, suppuration, recession, mobility, furcation, comparison with the last exam) | Done | Perio tab, `PC.table` |
| Perio keyboard entry (auto-advance) | Done | Perio tab |
| Perio summary and suggested stage / SRP by quadrant (suggestion only) | Done | `dental_clinical.perio_summary` |
| Treatment plans: phases, priorities, insurance and patient estimates, scheduling from the plan | Done | Treatment plans tab |
| Schedule by operatory, provider hours and lunch, drag to move, click to book, conflict checks | Done | `V.pages.dschedule` |
| Morning huddle | Done | `V.pages.dtoday`, `/api/dental/huddle` |
| Imaging: FMX/BW/pano mount, viewer (brightness, contrast, zoom, invert), upload | Done | Imaging tab |
| Clinical notes with templates, signing | Done | Notes & coding tab |
| Lab case tracking (late / due-after-seat flags) | Done | `V.pages.dlabs` |

## Phase 1: revenue cycle

| Feature | State | Where |
|---|---|---|
| Eligibility and benefits: 270 out, 271 in; remaining max, deductible, coinsurance by category, waiting periods | Mock / Claim.MD | `dental_x12.build_270/parse_271`, Insurance tab, Verify benefits; Claim.MD `/services/elig/` returns the payer's 271 |
| Frequency limits per patient (shared groups, per tooth, per quadrant, age limits, next covered date) | Done | `dental_rcm.frequency_check` |
| Night-before batch verification | Mock | `/eligibility/batch`, Schedule and Verify benefits |
| Treatment estimates against remaining max, deductible and coinsurance | Done | `dental_rcm.estimate` |
| CDT suggestions from the note: missed and undercoded procedures; a dentist approves each | Done (rules) / Partial (AI) | `dental_rcm.find_missed`; AI suggestions use the existing Groq coder when `GROQ_API_KEY` is set |
| Claim scrubbing before sending (tooth, surfaces, quadrant, frequency, attachments, NPI, subscriber) | Done | `dental_api_rcm.run_check` |
| Auto-generated narratives from the chart (perio depths for SRP, conditions for crowns, and more) | Done | `dental_rcm.narrative` |
| Radiographs, perio charts and the narrative attached to claims (PWK segments, then one PDF after the claim is accepted) | Mock / Claim.MD | `/claims/{id}/attach`, `dental_api_rcm._attachments_pdf`, attachment vendor |
| 837D claims and predeterminations, with validation that mirrors a 999 | Done (building) / Mock or Claim.MD (sending) | `dental_x12.build_837d`, `validate_837d`, `for_receiver`; the browser extension's dental claims use the same builder (`clearinghouse.build_837d`) |
| Corrected claims (frequency 7) | Done | `/claims/{id}/corrected` |
| Pre-authorization tracking (sent, pending, approved, denied, expiry, days waiting) | Mock | `V.pages.dpreauths` |
| ERA 835 auto-posting: payments, CO-45 write-offs, patient portions, idempotent per check | Done (posting) / Mock or Claim.MD (fetching, filtered to the clinic's NPI and Tax ID) | `dental_rcm.post_remittance`, `dental_api_rcm.post_835` |
| Denial and follow-up queue (CARC to next step, corrected claim, appeal letter, bill patient, write off, 30-day no-response) | Done | `V.pages.ddenials` |
| Patient billing: ledger, desk payments, text-to-pay, payment plans with autopay, adjustments | Mock (card and SMS) | Account tab, Patient billing |
| In-house membership plans for uninsured patients (included care, member discount, estimates use them) | Done | `dental_rcm.membership_price` |
| AR aging (insurance and patient) | Done | `/ar` |
| Claim status inquiry (276/277) | Not started | The 30-day "no response" queue item is the stand-in |
| Secondary insurance claims (COB) | Not started | Coverage has a `rank`; claims go to primary only |

## Phase 2: patient engagement

| Feature | State | Where |
|---|---|---|
| Online booking from real provider hours, chairs and existing appointments; new patients become requests | Done | `/dental/book/<clinic>`, `dental_engage.slots` |
| Two-way texting, with confirm by "C" and opt-out by "STOP" | Mock | `V.pages.dmessages`; inbound webhook is HMAC-signed |
| Appointment reminders at 72 and 24 hours, each sent once (background job) | Mock | `dental_engage.run_reminders` |
| Hygiene recall (3-month perio, 6-month prophy), campaigns with booked/sent tracking | Mock | `V.pages.drecall` |
| Intake and medical history forms that write into the chart and flag the update for review | Done | `/dental/form/<token>`, `dental_engage.apply_form` |
| Post-visit review requests (no review gating) | Mock | `/dental/review/<token>` |

## Phase 3: AI clinical tools

| Feature | State | Where |
|---|---|---|
| Ambient note drafting from visit audio (browser speech, or the transcriber) | Partial / Mock | Notes & coding; the draft is rule-based. Real audio needs a transcriber with a BAA |
| Hands-free voice perio charting | Done | Browser speech plus `dental_clinical.parse_perio_speech` |
| FDA-cleared radiograph AI integration (Overjet / Pearl / VideaHealth); the dentist accepts or dismisses | Mock | `RadiographAI` interface, Imaging tab. We don't diagnose |
| Visual treatment plan presentation the patient signs | Done | `/dental/plan/<token>` |

## Phase 4: integrations and operations

| Feature | State | Where |
|---|---|---|
| Imaging device integration (sensors, pano, CBCT, intraoral scanners, cameras) | Mock | `ImagingBridge`; mock draws labeled synthetic images |
| E-prescribing with allergy checks; EPCS requires a DEA number and a fresh two-step code | Mock | `dental_ops.prescribe` |
| Payment processing (card tokens only, never card numbers) | Mock | `PaymentProcessor` |
| Production and collections dashboard: gross, net, collections, case acceptance, clean claim rate, days to pay, hygiene reappointment, by provider with goals, by office, by category | Done | `V.pages.dreports` |
| Multi-location (DSO): location on schedule, procedures, claims and ledger; office filter; regions | Done / Partial | No per-location user permissions yet |
| Import from Open Dental, Dentrix and Eaglesoft exports (patients, procedure history) with preview | Done | Practice setup > Import data, `dental_ops.parse_import` |
| Live Open Dental connection: patients, primary dental plan (payer, subscriber ID, group, payer ID) and completed procedures, pulled again without duplicates | Built, off until `OPENDENTAL_DEVELOPER_KEY` is set | `opendental.py`, Practice setup > Import data; not yet run against Open Dental's test database |
| Open Dental documents and X-rays: list a patient's Imaging-module documents, copy one into Althais, push a filed record back | Built, off until the SFTP drop (`OPENDENTAL_SFTP_*`) is set up | `opendental.documents/download/upload`, Records Inbox > Open Dental documents; not yet run against Open Dental |
| Records Inbox: outside records from secure upload links, fax, Open Dental and a records network, read for patient and kind, filed by staff (X-rays also to dental images) | Done (upload links, staff upload) / Built, off until a fax provider is signed (fax) | `records_intake.py`, `/v4/emr/inbox`; patient matching reads text PDFs only (no OCR yet) |
| Records network (Quest/Labcorp results, Direct, Carequality/CommonWell/TEFCA) | Mock | `records_network.py`; needs a vendor contract and BAA |
| Import of appointments, insurance plans and ledgers from those systems | Not started | |

## Tests

- `tests/test_dental_rcm.py`: 39 tests covering X12, frequencies, estimates, coding, narratives, ERA posting, payment plans, memberships, recall and voice perio.
- `tests/test_dental_api.py`: 20 tests covering roles, clinic isolation, encryption at rest, the access log, chart to claim to 837D to 835 to ledger, red edits blocking sends, denials, code approvals, plan signing, online booking, forms into the chart, text-to-pay, EPCS, reports and imports.
- Run them with `python -m pytest tests -q`.
