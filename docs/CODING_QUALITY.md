# Coding quality: rules in code, a fixed test, and the clinic's own denials

Admin console › **Coding Quality** (`/admin/quality`).

## 1. CMS edit tables (each quarter, ~15 minutes)

1. cms.gov › Medicare › Coding & Billing › National Correct Coding Initiative Edits.
2. **Medicare NCCI Procedure to Procedure (PTP) Edits** › Practitioner PTP Edits: download the current quarter's 4 zip files.
   **Medicare NCCI Medically Unlikely Edits (MUEs)** › Practitioner Services MUE Table: download the zip.
   CMS asks you to accept its license (it includes the AMA's CPT terms) first.
3. Coding Quality › upload the 4 PTP zips together, then the MUE zip. Loading takes a few minutes; the page shows progress.

From then on every claim check (EMR, claim builder) uses CMS's full tables for the date of service: NCCI pairs and the
modifier that may or may not unbundle them, and the most units per code per day. Until a table is loaded, NCCI uses the
62 built-in pairs and MUEs aren't checked. A file that isn't the right one is refused and the old table is kept.

Also checked in code now: diagnoses that don't fit the patient's sex or age (`claim_edits.py`).

## 2. The coding test

`evals/coding_cases.json`: 30 urgent care visits with the codes they should get. **The labels are a draft** written
from public coding rules; a certified coder (CPC) should check every one. Until then, scores show direction, not truth.

Coding Quality › **Run the coding test** runs every case through the same claim builder the EMR uses, with the AI this
deployment is set to, and keeps the scorecard (last 20 runs): fully right, CPT exact, E/M level, ICD-10 found,
modifiers, the claim check's own score, seconds per case, and each case's answer.

Groq vs Gemini: run it on a Groq deployment, redeploy with `AI_PROVIDER=vertex`, run it again. Same cases, two rows.

Adding a case: copy one in the file. `cpt` and `icd10` are lists of slots; each slot lists the acceptable codes, best
first. `modifiers` says which modifiers must be on which code. Keep notes synthetic: no real patient's details.

## 3. The clinic's own denials, used before a claim goes out

When a clinic has payment files in Althais (Lost Money, Claim.MD connection), its payer memory (`money_checks.py`) now
feeds the claim builder: this payer's repeat denials go into the AI coder's instructions, and the money checks (repeat
denial patterns, missing drug or admin codes, missing modifier 25, deleted codes) run on the built claim. A flag that
means a likely denial lowers the check score, so the builder takes a second pass.
