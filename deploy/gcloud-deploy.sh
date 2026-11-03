#!/usr/bin/env bash
# Build and deploy Althais to Cloud Run. Run from the repo folder in Google Cloud Shell:
#
#   PROJECT_ID=althais-prod bash deploy/gcloud-deploy.sh            # deploys, sends no traffic to the new version
#   PROJECT_ID=althais-prod PROMOTE=1 bash deploy/gcloud-deploy.sh  # deploys and makes it live
#
# Exactly one instance, CPU always on: the app's billing jobs (end-of-day claim batch, Claim.MD responses every 5 minutes,
# ERAs hourly, reminders) run inside the web process, so there must always be one copy running and never two.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-althais}"
SQL_INSTANCE="${SQL_INSTANCE:-althais-db}"
AI_PROVIDER="${AI_PROVIDER:-groq}"              # switch to vertex after the coding tests pass (migration doc, step 6)
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/althais/app:${TAG}"
SA="althais-run@${PROJECT_ID}.iam.gserviceaccount.com"
CONN="$(gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" --format='value(connectionName)')"

gcloud builds submit --project "$PROJECT_ID" --config deploy/cloudbuild.yaml --substitutions "_IMAGE=$IMAGE" .

# every setting loaded by gcloud-import-env.sh (label app=althais) becomes an environment variable of the same name
SECRETS="$(gcloud secrets list --project "$PROJECT_ID" --filter='labels.app=althais' --format='value(name)' | sed 's/.*/&=&:latest/' | paste -sd, -)"
[ -n "$SECRETS" ] || { echo "No settings in Secret Manager yet: run deploy/gcloud-import-env.sh first."; exit 1; }

ENVS="COOKIE_SECURE=1,APP_URL=https://app.althais.com,PUBLIC_BASE_URL=https://app.althais.com,AI_PROVIDER=${AI_PROVIDER}"
ENVS+=",GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${REGION},DB_POOL_SIZE=10,DB_MAX_OVERFLOW=10"
[ "${PROMOTE:-0}" = "1" ] || ENVS+=",JOBS_DISABLED=1"   # a test copy never runs billing jobs: the live copy does
[ -n "${EXTRA_ENV:-}" ] && ENVS+=",${EXTRA_ENV}"    # e.g. EXTRA_ENV="BILLING_SANDBOX_ORGS=TEST CLINIC,RECORDS_NETWORK=mock"

gcloud run deploy "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --image "$IMAGE" \
  --service-account "$SA" --add-cloudsql-instances "$CONN" \
  --min-instances 1 --max-instances 1 --no-cpu-throttling --cpu 1 --memory 1Gi --concurrency 80 --timeout 300 \
  --allow-unauthenticated --ingress all --execution-environment gen2 \
  --set-secrets "$SECRETS" --set-env-vars "$ENVS" \
  --startup-probe "httpGet.path=/api/health,initialDelaySeconds=5,periodSeconds=5,failureThreshold=24" \
  $( [ "${PROMOTE:-0}" = "1" ] || echo "--no-traffic --tag candidate" )

echo
if [ "${PROMOTE:-0}" = "1" ]; then
  echo "Live: $(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
else
  echo "Deployed with no traffic. Test it at the 'candidate' URL above, then run again with PROMOTE=1."
fi
