#!/usr/bin/env bash
# Copy Althais's database from Supabase to Cloud SQL. Run in Google Cloud Shell.
#
#   SOURCE_DATABASE_URL='postgresql://...supabase...'  PROJECT_ID=althais-prod  bash deploy/gcloud-copy-db.sh
#
# SOURCE_DATABASE_URL: Railway > Variables > DATABASE_URL (the Supabase connection string). Use the "session" or
# direct connection (port 5432), not the transaction pooler (6543): pg_dump needs a real session.
#
# Copies only Althais's own tables (schema "public"), not Supabase's internal ones. The target must be empty: run this
# for the trial copy, and again at cutover after the old app is stopped (the script asks before replacing anything).
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
SRC="${SOURCE_DATABASE_URL:?set SOURCE_DATABASE_URL to the Supabase connection string}"
SQL_INSTANCE="${SQL_INSTANCE:-althais-db}"
PORT=5439

need_pg() { command -v pg_dump >/dev/null && [ "$(pg_dump --version | grep -oE '[0-9]+' | head -1)" -ge 17 ]; }
if ! need_pg; then
  echo "== Installing the Postgres 17 client tools"
  sudo apt-get update -qq && sudo apt-get install -y -qq postgresql-common >/dev/null
  sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
  sudo apt-get install -y -qq postgresql-client-17 >/dev/null
  export PATH="/usr/lib/postgresql/17/bin:$PATH"
fi

DST_URL="$(gcloud secrets versions access latest --secret=DATABASE_URL --project "$PROJECT_ID")"
DB_USER="$(echo "$DST_URL" | sed -E 's#^postgres(ql)?://([^:]+):.*#\2#')"
DB_PASS="$(echo "$DST_URL" | sed -E 's#^postgres(ql)?://[^:]+:([^@]+)@.*#\2#')"
DB_NAME="$(echo "$DST_URL" | sed -E 's#^[^@]+@/([^?]+).*#\1#')"
CONN="$(gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" --format='value(connectionName)')"

echo "== Connecting to Cloud SQL through the Cloud SQL Auth Proxy"
command -v cloud-sql-proxy >/dev/null || { curl -sSo cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.linux.amd64 && chmod +x cloud-sql-proxy && export PATH="$PWD:$PATH"; }
cloud-sql-proxy --port "$PORT" "$CONN" >/tmp/csp.log 2>&1 &
PROXY=$!; trap 'kill $PROXY 2>/dev/null || true' EXIT
for i in $(seq 1 30); do PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -p "$PORT" -U "$DB_USER" -d "$DB_NAME" -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
T="psql -h 127.0.0.1 -p $PORT -U $DB_USER -d $DB_NAME -v ON_ERROR_STOP=1 -q"
export PGPASSWORD="$DB_PASS"

HAS="$($T -tAc "select count(*) from information_schema.tables where table_schema='public'")"
if [ "$HAS" != "0" ]; then
  read -r -p "Cloud SQL already has $HAS tables. Replace them with a fresh copy from Supabase? Type REPLACE: " ok
  [ "$ok" = "REPLACE" ] || { echo "Nothing changed."; exit 1; }
  $T -c "drop schema public cascade; create schema public;"
fi

echo "== Copying (schema public, no owners or grants)"
pg_dump "$SRC" --schema=public --no-owner --no-privileges --no-comments --format=plain | $T >/dev/null

echo "== Checking row counts match"
bad=0
for t in $(PGPASSWORD= psql "$SRC" -tAc "select tablename from pg_tables where schemaname='public' order by 1"); do
  a="$(psql "$SRC" -tAc "select count(*) from public.\"$t\"")"; b="$($T -tAc "select count(*) from public.\"$t\"")"
  if [ "$a" != "$b" ]; then echo "MISMATCH $t: supabase $a, cloud sql $b"; bad=1; fi
done
[ $bad = 0 ] && echo "All tables match." || { echo "Some tables differ: don't switch over yet."; exit 1; }
