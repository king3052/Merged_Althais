#!/usr/bin/env bash
# Load Althais's settings into Google Secret Manager, one secret per variable, from a .env file.
#
#   1. Railway > the Althais service > Variables > Raw Editor > copy everything into a file named railway.env
#      (in Cloud Shell: nano railway.env, paste, Ctrl+O, Enter, Ctrl+X)
#   2. PROJECT_ID=althais-prod bash deploy/gcloud-import-env.sh railway.env
#   3. Delete the file:  shred -u railway.env
#
# Values are read from the file and sent straight to Secret Manager; nothing is printed. Settings the deploy script sets
# itself (or that only made sense on Railway) are skipped, and so is DATABASE_URL (Cloud SQL's, from gcloud-setup.sh, is kept).
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
FILE="${1:?usage: gcloud-import-env.sh railway.env}"
SKIP=" DATABASE_URL APP_URL PUBLIC_BASE_URL COOKIE_SECURE AI_PROVIDER GOOGLE_CLOUD_PROJECT VERTEX_LOCATION DB_POOL_SIZE DB_MAX_OVERFLOW PORT JOBS_DISABLED "
n=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  key="${line%%=*}"; key="$(echo "$key" | sed 's/^export //;s/[[:space:]]//g')"
  val="${line#*=}"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
  case "$key" in RAILWAY_*|NIXPACKS_*) continue;; esac
  [[ "$SKIP" == *" $key "* ]] && { echo "skip  $key (set by the deploy)"; continue; }
  if [[ "$val" =~ ^\".*\"$ || "$val" =~ ^\'.*\'$ ]]; then val="${val:1:${#val}-2}"; fi
  if gcloud secrets describe "$key" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '%s' "$val" | gcloud secrets versions add "$key" --project "$PROJECT_ID" --data-file=- >/dev/null
  else
    printf '%s' "$val" | gcloud secrets create "$key" --project "$PROJECT_ID" --replication-policy=automatic --labels=app=althais --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$key" --project "$PROJECT_ID" \
    --member="serviceAccount:althais-run@${PROJECT_ID}.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor >/dev/null
  echo "saved $key"; n=$((n+1))
done < "$FILE"
echo "$n settings saved. Now delete the file: shred -u $FILE"
