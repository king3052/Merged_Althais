#!/usr/bin/env bash
# One-time: let Cloud Build deploy pushes to main, and protect the database. Run in Cloud Shell after connecting the GitHub
# repository in the console (Cloud Build > Repositories > Connect repository; see docs/GOOGLE_CLOUD_MIGRATION.md, "Deploys").
#   PROJECT_ID=althais bash deploy/gcloud-autodeploy.sh
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE="${SQL_INSTANCE:-althais-db}"
NUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
BUILDER="$NUM-compute@developer.gserviceaccount.com"     # the account Cloud Build runs as in new projects
RUNTIME="althais-run@${PROJECT_ID}.iam.gserviceaccount.com"

echo "== Database: deletion protection, maintenance Sundays 2-3 AM Central"
gcloud sql instances patch "$SQL_INSTANCE" --project "$PROJECT_ID" --deletion-protection \
  --maintenance-window-day=SUN --maintenance-window-hour=8 --quiet

echo "== What the build account may do: deploy Cloud Run, run as the app's account, read which settings exist, push images, write logs"
for role in roles/run.admin roles/secretmanager.viewer roles/cloudsql.viewer roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILDER" --role="$role" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --project "$PROJECT_ID" \
  --member="serviceAccount:$BUILDER" --role=roles/iam.serviceAccountUser >/dev/null

echo "== Trigger: every push to main deploys"
if gcloud builds triggers describe althais-main --project "$PROJECT_ID" --region "$REGION" >/dev/null 2>&1; then
  echo "Trigger althais-main already exists."
else
  CONN="$(gcloud builds connections list --project "$PROJECT_ID" --region "$REGION" --format='value(name)' | head -1)"
  REPO="$(gcloud builds repositories list --connection="$(basename "$CONN")" --project "$PROJECT_ID" --region "$REGION" --format='value(name)' | head -1)"
  [ -n "$REPO" ] || { echo "Connect the GitHub repository first: Cloud Build > Repositories > Connect repository (us-central1)."; exit 1; }
  gcloud builds triggers create github --project "$PROJECT_ID" --region "$REGION" --name althais-main \
    --repository="$REPO" --branch-pattern='^main$' --build-config=deploy/cloudbuild-deploy.yaml \
    --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILDER"
fi
echo
echo "Done. The next push to main builds and deploys by itself (Cloud Build > History shows it, about 5 minutes)."
