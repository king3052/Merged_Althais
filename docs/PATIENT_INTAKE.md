# Scan To Autofill (new-patient registration)

When staff click **New Patient** they choose between **Scan Documents To Autofill** and **Enter Manually**. The manual
form is the same form as before, and staff can switch to it at any step without losing what was read or typed.

## How it works

1. **Upload.** Staff add files with drag and drop, Choose Files (which also works with a scanner's output folder) or
   Use Camera on a phone. The accepted formats are JPG, PNG, HEIC and PDF, up to 15 MB each. Each file is tagged as
   Insurance Card Front, Insurance Card Back, Photo ID, Intake Form, or Consent / Other, and the tag can be changed.
   Files are uploaded as soon as they're added and are stored encrypted.
2. **Read.** `POST /api/intake/{id}/extract` sends each document to Claude (vision) with the prompt in
   [prompts/patient_intake_extraction.md](../prompts/patient_intake_extraction.md). For each field the model returns a value,
   a confidence (high, medium or low) and the field's location on the page, or null when the field isn't there.
   `parse_extraction` normalizes dates, phone numbers, states and ZIP codes. A value with a bad format is kept but
   marked low confidence, and placeholders such as "N/A" are dropped. `merge_documents` combines the documents. When two
   documents disagree, the field is left blank and flagged as a conflict instead of one value being picked.
3. **Review.** The form opens pre-filled, with the source documents beside it. Clicking a field highlights where its
   value came from.
   - Low-confidence fields are yellow. Conflicts are red, with a button for each candidate value.
   - Every field can be edited, and validation runs inline: date format, DOB not in the future, ZIP of 5 or 9 digits,
     10-digit phone, valid US state, and required fields.
   - Each field's provenance is tracked: `ai`, `ai_edited`, `ai_cleared` or `manual`.
4. **Confirm.** Nothing is created until staff click **Confirm & Create Patient**. Then the documents are attached to
   the patient (listed under **Scanned At Registration** on the chart), the provenance is saved, and an eligibility
   check runs if insurance was filled in.

If extraction is unavailable, fails or times out, staff land on the manual form with whatever was read. If only some
documents fail, review opens with what the others returned and the failed ones are marked.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Required for extraction. Without it, Scan To Autofill goes straight to the manual form. |
| `INTAKE_MODEL` | `claude-opus-5-5` | The Claude model used to read the documents. |
| `INTAKE_TIMEOUT_SECONDS` | `75` | The time limit for reading all of an intake's documents. |
| `PHI_ENC_KEY` | falls back to `SECRET_KEY` | The key used to encrypt stored documents, file names and extraction results. Set it in production and don't rotate it without re-encrypting. |

HEIC photos need `pillow-heif` (in requirements.txt). To run the tests: `pip install -r requirements-dev.txt && python -m pytest tests -q`.

**BAA:** before real patient documents are sent, Althais must have a signed Business Associate Agreement with Anthropic
(or use Claude through a cloud provider that is covered by the organization's BAA). The vendor is set in one place,
`patient_intake.claude_extract`, so a different BAA-covered vision model can be swapped in there. The function takes
the file bytes, the content type and the tag, and returns the JSON text.

## Security

- **Encryption.** Documents, file names and extraction results are encrypted at rest (Fernet), and downloads are sent
  with `Cache-Control: no-store`. In transit, everything is HTTPS: the app's deployment and the API call to Anthropic.
- **Access.** Only the admin, biller, provider and **front_desk** roles can use these endpoints, and only for their
  own clinic's documents. Front desk is a new role. It can register patients and schedule, but it can't code or bill.
  Staff who were onboarded with patients-only access now log in as front_desk.
- **Audit log.** The `patient_intake_audit` table records uploaded, retagged, removed, viewed, extracted, reviewed, edited, confirmed and
  eligibility events, with the user, time and document. It never stores field values. No PHI is
  written to logs; errors record only the exception type.

## Eligibility

[eligibility.py](../eligibility.py) defines an `EligibilityRequest` (payer, member ID, subscriber, DOB, relationship, plus
the clinic's NPI and Tax ID) and an `EligibilityResult` (active, inactive, not_connected or error, with copay,
deductible, deductible remaining and errors).

Until Althais's Claim.MD account is connected in the admin console, the check returns `not_connected`. Once it's
connected, the check uses Claim.MD's `/services/eligdata/` endpoint. A failed eligibility check never blocks
registration.

## Files

- `patient_intake.py` contains the models, the extraction parser and merge, validation and the endpoints.
- `prompts/patient_intake_extraction.md` is the extraction prompt.
- `eligibility.py` is the eligibility hook.
- `static/v4/app7.js` contains the choice screen, upload, review and confirm steps.
- `tests/test_patient_intake.py` has tests for the parser, merging, validation, storage, and the manual fallback.
