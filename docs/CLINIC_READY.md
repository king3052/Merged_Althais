# Clinic-ready: what changed and what to do before the first real clinic

## What the app now does

| Area | What it does | Where |
|---|---|---|
| Appeal windows | Each clinic enters its real appeal window and filing limit per payer (or per kind of payer). Deadlines it hasn't set are marked **estimated** in the report, the CSV and Recovery text, with a link to set them. | `/clinic-setup`, `clinic_setup.py`, `lost_money.ClinicTerms` |
| Contract rates | Clinics upload each payer's fee schedule (CSV: code, modifier, rate; payer column or chosen on the page). Lines paid under contract are flagged, so a payer that underpays every claim is caught. Denial values use the contract rate. | `/clinic-setup`, `clinic_setup.parse_contracts` |
| Corrected claims | A denial later paid under a **new claim number** for the same patient (member ID and name), date and code counts as fixed, not lost. A claim answered under another number isn't reported as "no answer". | `Analysis.is_resolved`, `no_response` |
| Insurer takebacks | Provider-level adjustments (835 PLB: overpayment recoveries, offsets, interest) are read, shown in "Money insurers took back", and subtracted in `paid_after_takebacks`. Never counted as money found. | `parse_835(plb=)`, `Analysis.takebacks` |
| Sites | Findings carry the billing NPI (named by the clinic) and the rendering provider; the report shows "By site and provider" when there is more than one. | `Analysis.by_site` |
| Setup checklist | BAA signed, biller invited, two-step login, payment files, first report, appeal windows, contract rates, CPT notice. | `/clinic-setup` |
| AMA CPT notices | Short line on the report, the CSV, Recovery and every medical app page. Long notice accepted once per person before CPT pages (re-shown if the text changes). | `cpt_notice.py` |
| Data return and deletion | /admin offers a full export (every table as JSON, payment files decrypted) before deleting a clinic. Deletion now also removes the claim store's rows (`org_<name>`) and sessions, and produces a printable **certificate of deletion**. | `clinic_data.py`, `auth.admin_remove_clinic` |
| Alerts | Failed Claim.MD pulls, failed hourly checks and server errors email `ALERT_EMAIL` (at most once per problem per clinic every 6 hours, no clinic data in the email) and log an ERROR line that Google Cloud alerts on. | `ops_alerts.py`, `deploy/gcloud-alerts.sh` |

## Before the first real clinic (in order)

1. **Ship the patch** (`_patches\inbox`, then `ship.ps1`).
2. **Alerts:** in Cloud Shell, `PROJECT_ID=althais ALERT_EMAIL=you@althais.com,kevin@althais.com bash deploy/gcloud-alerts.sh`, then push anything (or redeploy) so the app reads ALERT_EMAIL. Confirm the email Google sends to each address.
3. **AMA notice text:** when the license is signed, paste Appendix E's long notice into `legal/ama_cpt_notice.txt` and ship. Until then a placeholder is shown (`/api/cpt-notice` says `"final": false`).
4. **BAA:** signed with the clinic, then recorded by the clinic admin at `/clinic-setup`. No files before that.
5. **With the clinic:** enter appeal windows for its top payers and upload fee schedules if it has them. Name its sites.
6. **First real files:** read the report yourself before the clinic sees it. Check payer names, amounts, and that resubmitted claims aren't shown as lost.

## Still manual or not covered

- Real payment files from Texas payers haven't been through the parser yet. Expect small fixes after the first clinic.
- The Chrome extension shows codes inside the EMR; it doesn't show the long CPT notice (people accept it in the app).
- Takebacks aren't matched to the exact claim they recover; the reference number from the payer is shown.
- Database backups (14 days) still hold a deleted clinic until they expire; the certificate says so.
