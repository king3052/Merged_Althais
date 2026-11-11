#!/usr/bin/env bash
# One-time Google Cloud setup for Althais. Run in Google Cloud Shell (console.cloud.google.com, the >_ button), which is
# already signed in as you. Safe to run twice: each step skips what already exists.
#
#   PROJECT_ID=althais-prod REGION=us-central1 bash deploy/gcloud-setup.sh
#
# It creates: the APIs Althais needs, a service account the app runs as (least privilege), a Cloud SQL Postgres
# database (private, encrypted, daily backups, point-in-time recovery) and its connection string in Secret Manager.
# It does NOT copy data or switch traffic: see docs/GOOGLE_CLOUD_MIGRATION.md.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID, e.g. althais-prod}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE="${SQL_INSTANCE:-althais-db}"
SQL_TIER="${SQL_TIER:-db-g1-small}"          # ~2 GB RAM. Enough for pilots; resize later with no data loss.
DB_NAME="${DB_NAME:-althais}"
DB_USER="${DB_USER:-althais_app}"
SA="althais-run@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"

echo "== APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com aiplatform.googleapis.com logging.googleapis.com

echo "== Service account the app runs as"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create althais-run --display-name="Althais app (Cloud Run)"
for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
done

echo "== Container registry"
gcloud artifacts repositories describe althais --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create althais --repository-format=docker --location="$REGION"

echo "== Cloud SQL (Postgres 17, the version Supabase runs)"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_17 --region="$REGION" --tier="$SQL_TIER" \
    --storage-type=SSD --storage-size=10GB --storage-auto-increase \
    --backup-start-time=08:00 --enable-point-in-time-recovery --retained-backups-count=14 \
    --require-ssl --database-flags=cloudsql.iam_authentication=on
fi
gcloud sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create "$DB_NAME" --instance="$SQL_INSTANCE"
if ! gcloud sql users list --instance="$SQL_INSTANCE" --format="value(name)" | grep -qx "$DB_USER"; then
  DB_PASS="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)"
  gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" --password="$DB_PASS"
  CONN="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"
  # the app reaches Cloud SQL through Cloud Run's built-in connector (a unix socket), never over the internet
  printf '%s' "postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONN}" | \
    gcloud secrets create DATABASE_URL --data-file=- --replication-policy=automatic --labels=app=althais 2>/dev/null || \
  printf '%s' "postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONN}" | gcloud secrets versions add DATABASE_URL --data-file=-
  unset DB_PASS
fi

echo
echo "Done. Cloud SQL connection name: $(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"
echo "Next: load Railway's settings with deploy/gcloud-import-env.sh (docs/GOOGLE_CLOUD_MIGRATION.md, step 3)."
