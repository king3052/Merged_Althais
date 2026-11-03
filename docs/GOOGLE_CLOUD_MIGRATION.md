# Moving Althais to Google Cloud

From Railway (app) + Supabase (database) + Groq (AI) to Google Cloud: Cloud Run (app), Cloud SQL (database) and
Vertex AI (Gemini), in one region under one Google Cloud BAA.

Why: one BAA instead of three (Groq doesn't sign one, so real patient notes can't go through Code a Note today), the app
and database side by side (most of each page's wait is the trip between Railway and Supabase), and startup credits.

**Nothing switches until step 7.** Railway and Supabase keep running the live site the whole time, and stay as the
fallback for a week after.

Everything below runs in **Google Cloud Shell** (console.cloud.google.com, the `>_` button at the top right). It's a
terminal already signed in as you, with `gcloud` installed. Get the code there once:

```bash
git clone https://github.com/king3052/Merged_Althais.git && cd Merged_Althais
```

## 1. Account (you, ~30 minutes, once)

1. Create a Google Cloud **organization** for althais.com if you can (Cloud Identity Free, or Google Workspace), then a
   project, e.g. `althais-prod`. Add a billing account.
2. **Sign the BAA** before any patient data goes in: Google Cloud console > IAM & Admin > Settings or the Compliance page
   ("HIPAA Business Associate Amendment"). It's accepted by an organization admin for Althais Inc.
3. Apply for **Google for Startups Cloud Program** credits (cloud.google.com/startup).
4. Turn on **2-step verification** for every Google account that has access, and give Kevin his own account (never share
   logins).
5. Budget alert: Billing > Budgets & alerts, e.g. $200/month, email to both founders.

## 2. Create the infrastructure (~15 minutes, mostly waiting)

```bash
PROJECT_ID=althais-prod REGION=us-central1 bash deploy/gcloud-setup.sh
```

Creates: the APIs, a service account the app runs as (Cloud SQL client, Secret Manager reader, Vertex AI user, log
writer, nothing else), a container registry, and Cloud SQL Postgres 17 (2 GB, SSL required, daily backups kept 14 days,
point-in-time recovery). The database password is generated and stored straight into Secret Manager as `DATABASE_URL`;
nobody sees it.

Region: `us-central1` (Iowa) has every service and Gemini model. `us-south1` (Dallas) is closer to Texas but check that
Vertex AI has the Gemini models you need there before choosing it. App and database must be in the same region.

## 3. Settings and secrets (~10 minutes)

**The encryption keys must be copied exactly** (`SECRET_KEY`, `PHI_ENC_KEY`, `BROWSER_ENC_KEY`, `ADMIN_TOTP_SECRET`):
with different keys, saved records and sign-ins can't be read.

1. Railway > the Althais service > Variables > **Raw Editor** > copy everything.
2. In Cloud Shell: `nano railway.env`, paste, Ctrl+O, Enter, Ctrl+X.
3. ```bash
   PROJECT_ID=althais-prod bash deploy/gcloud-import-env.sh railway.env
   shred -u railway.env
   ```
   Every variable becomes a secret the app reads at start. Values are never printed. `DATABASE_URL` from Railway is
   skipped (Cloud SQL's is used).

## 4. Trial copy of the database (~5 minutes)

```bash
SOURCE_DATABASE_URL='<Supabase connection string, port 5432>' PROJECT_ID=althais-prod bash deploy/gcloud-copy-db.sh
```

Copies Althais's tables (schema `public`) and checks every table's row count matches. This is a test copy: it will be
replaced at cutover.

## 5. Deploy a test copy (~10 minutes)

```bash
PROJECT_ID=althais-prod bash deploy/gcloud-deploy.sh
```

It deploys with **no traffic** and **billing jobs off** (`JOBS_DISABLED=1`), so it can't send claims or reminders twice
while Railway is still live. It prints a `candidate` URL. Check, on that URL:

- sign in as TEST CLINIC (sessions from Railway won't carry over; sign in again)
- open several EMR pages: each should load in well under a second (`Server-Timing` header in the Network tab)
- open a sample patient, run Claim Check, Code a Note, eligibility
- `/api/health` answers `{"ok": true, ...}`

## 6. Switch the AI to Vertex (Gemini), then test coding

Do this only after the BAA is signed, and before any real patient's note is coded.

```bash
PROJECT_ID=althais-prod AI_PROVIDER=vertex bash deploy/gcloud-deploy.sh
```

Defaults: `gemini-2.5-pro` for coding, `gemini-2.5-flash` for fast checks (override with `EXTRA_ENV="VERTEX_MODEL=...,VERTEX_FAST_MODEL=..."`
if newer models are available). Then run the coding checks against the candidate URL: the sample clinic's 5 edits,
Code a Note on the strep, laceration and diabetes notes, the claim builder. Compare with Groq's answers. Switch back
(`AI_PROVIDER=groq`) if anything is worse, and fix the prompts first.

Claude (patient intake, staff documents) stays on Anthropic's own API under Anthropic's BAA. It can run on Vertex
(`EXTRA_ENV="CLAUDE_ON_VERTEX=1"`), but Google's HIPAA page doesn't list Anthropic's models as covered: confirm with Google
before sending patient data that way.

**Confirm with Google in writing** that Gemini on Vertex AI is covered by your BAA (Google's page lists it under
"Generative AI on Agent Platform"), and keep the answer.

## 7. Cutover (~30 minutes; not at 6 PM Central, when the claim batch runs)

1. Tell clinics: a 15-minute maintenance window.
2. **Stop Railway's jobs and writes:** Railway > Variables > add `JOBS_DISABLED=1` (it redeploys). Then put Railway in
   maintenance (or scale to zero) so nobody saves anything to Supabase.
3. Fresh copy: run step 4 again (type `REPLACE` when asked). It must say "All tables match."
4. Go live on Cloud Run: `PROJECT_ID=althais-prod PROMOTE=1 bash deploy/gcloud-deploy.sh`
5. Point the domain: Cloud Run > althais > Manage custom domains > add `app.althais.com` (and `althais.com` if the app
   serves it). Google shows the DNS records; set them in **Cloudflare** (DNS only, grey cloud, until the certificate is
   issued; then orange if you want Cloudflare in front).
6. Check: sign in, open pages, run eligibility, check the admin console shows Claim.MD connected and the last poll times
   update within 5 minutes (responses) and an hour (ERAs).
7. Update the Claim.MD webhook / any partner URLs if they point at a Railway address.

**Rollback** (any time in the first week): point Cloudflare back to Railway, remove `JOBS_DISABLED` on Railway. Anything
saved on Cloud SQL after cutover would need copying back, so decide within the first hours if possible.

## 8. After a quiet week

- Delete the Railway service and the Supabase project (export a final Supabase backup first, keep it encrypted).
- Remove `GROQ_API_KEY` once Vertex has run a week.
- Cloud SQL: set a maintenance window (Sundays early morning), and test a restore from backup once.

## Day to day

- Deploy: merge to main, then `PROJECT_ID=althais-prod PROMOTE=1 bash deploy/gcloud-deploy.sh` from Cloud Shell
  (or test first without `PROMOTE`). A Cloud Build trigger on main can do this automatically later.
- Logs: Cloud Run > althais > Logs. They must never contain patient details: the app logs error types, not data.
- Change a setting: `printf '%s' 'new value' | gcloud secrets versions add NAME --data-file=-`, then deploy again.

## Cost (before credits, rough)

| | |
|---|---|
| Cloud Run, 1 instance always on (1 CPU, 1 GB) | ~$45–65 / month |
| Cloud SQL, db-g1-small + 10 GB SSD + backups | ~$30–40 / month |
| Vertex AI (Gemini), pilot volume | ~$5–30 / month |
| Secret Manager, registry, logs | ~$5 / month |

Check current prices in the Google Cloud pricing calculator; these move.

## How the app runs there

- **One instance, CPU always on.** The claim batch, Claim.MD response checks (every 5 minutes), ERA import (hourly)
  and reminders run inside the app, so there must always be exactly one copy running. Test copies run with
  `JOBS_DISABLED=1`. Moving the jobs to Cloud Scheduler later would allow more than one instance.
- **Database** through Cloud Run's Cloud SQL connector (a unix socket), never a public address. `DATABASE_URL` looks
  like `postgresql://user:pass@/althais?host=/cloudsql/PROJECT:REGION:althais-db`.
- **AI** through `ai_client.py`: `AI_PROVIDER=groq` or `vertex`, same calls either way. On Cloud Run the service
  account signs Vertex calls; there's no AI key to manage.
- **Health check**: `/api/health` (the database answers).
- **Image**: `deploy/Dockerfile`, built by `deploy/cloudbuild.yaml`. It's not in the repo root on purpose: Railway
  switches to any root Dockerfile, and Railway keeps building its own way until the move is done.
