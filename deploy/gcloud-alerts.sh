#!/usr/bin/env bash
# One-time: get told when Althais breaks. Run in Cloud Shell:
#   PROJECT_ID=althais ALERT_EMAIL=kyankarla@althais.com,kevinqu@althais.com bash deploy/gcloud-alerts.sh
#
#   1. ALERT_EMAIL becomes a setting of the app (Secret Manager, like the others), so ops_alerts.py emails those
#      addresses when a Claim.MD pull, an hourly payment check or a page fails. Applies from the next deploy.
#   2. Google Cloud itself also emails them, even if the app's email is down:
#        - when the app logs an alert or any error (a log-based metric over Cloud Run's logs), and
#        - when app.althais.com/api/health stops answering for 5 minutes (an uptime check).
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
ALERT_EMAIL="${ALERT_EMAIL:?set ALERT_EMAIL (comma-separated)}"
SERVICE="${SERVICE:-althais}"
HOST="${HOST:-app.althais.com}"
RUNTIME="althais-run@${PROJECT_ID}.iam.gserviceaccount.com"

echo "== The app's alert setting (ALERT_EMAIL)"
if gcloud secrets describe ALERT_EMAIL --project "$PROJECT_ID" >/dev/null 2>&1; then
  printf '%s' "$ALERT_EMAIL" | gcloud secrets versions add ALERT_EMAIL --project "$PROJECT_ID" --data-file=- >/dev/null
else
  printf '%s' "$ALERT_EMAIL" | gcloud secrets create ALERT_EMAIL --project "$PROJECT_ID" --replication-policy=automatic --labels=app=althais --data-file=- >/dev/null
fi
gcloud secrets add-iam-policy-binding ALERT_EMAIL --project "$PROJECT_ID" --member="serviceAccount:$RUNTIME" \
  --role=roles/secretmanager.secretAccessor >/dev/null

echo "== Email channels"
CHANNELS=()
IFS=',' read -ra ADDRS <<< "$ALERT_EMAIL"
for a in "${ADDRS[@]}"; do
  a="$(echo "$a" | xargs)"; [ -z "$a" ] && continue
  ch="$(gcloud beta monitoring channels list --project "$PROJECT_ID" --filter="labels.email_address=\"$a\"" --format='value(name)' | head -1)"
  if [ -z "$ch" ]; then
    ch="$(gcloud beta monitoring channels create --project "$PROJECT_ID" --display-name="Althais alerts: $a" --type=email \
          --channel-labels=email_address="$a" --format='value(name)')"
  fi
  CHANNELS+=("$ch"); echo "  $a"
done
JOINED="$(printf '"%s",' "${CHANNELS[@]}")"; JOINED="[${JOINED%,}]"

echo "== Log-based metric: app alerts and errors"
FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\" AND severity>=ERROR"
if ! gcloud logging metrics describe althais_errors --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud logging metrics create althais_errors --project "$PROJECT_ID" --description="Althais alerts and errors" --log-filter="$FILTER"
fi

TMP="$(mktemp -d)"
cat > "$TMP/errors.json" <<JSON
{"displayName": "Althais: errors in the app",
 "combiner": "OR",
 "conditions": [{"displayName": "An alert or error was logged",
   "conditionThreshold": {"filter": "metric.type=\\"logging.googleapis.com/user/althais_errors\\" AND resource.type=\\"cloud_run_revision\\"",
     "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "0s",
     "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_COUNT"}]}}],
 "alertStrategy": {"autoClose": "3600s", "notificationRateLimit": {"period": "3600s"}},
 "notificationChannels": $JOINED,
 "documentation": {"content": "Open Cloud Run > althais > Logs, filter severity ERROR. Lines with \"alert\" name the clinic and what failed.", "mimeType": "text/markdown"}}
JSON

echo "== Uptime check: $HOST/api/health"
UP="$(gcloud monitoring uptime list-configs --project "$PROJECT_ID" --filter="displayName=althais-health" --format='value(name)' | head -1)"
if [ -z "$UP" ]; then
  gcloud monitoring uptime create althais-health --project "$PROJECT_ID" --resource-type=uptime-url \
    --resource-labels=host="$HOST",project_id="$PROJECT_ID" --path=/api/health --protocol=https --period=5 >/dev/null
fi
CHECK_ID="$(gcloud monitoring uptime list-configs --project "$PROJECT_ID" --filter="displayName=althais-health" --format='value(name)' | head -1 | xargs basename)"
cat > "$TMP/uptime.json" <<JSON
{"displayName": "Althais: app.althais.com is down",
 "combiner": "OR",
 "conditions": [{"displayName": "Health check failing",
   "conditionThreshold": {"filter": "metric.type=\\"monitoring.googleapis.com/uptime_check/check_passed\\" AND resource.type=\\"uptime_url\\" AND metric.label.check_id=\\"$CHECK_ID\\"",
     "comparison": "COMPARISON_GT", "thresholdValue": 1, "duration": "300s",
     "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_NEXT_OLDER", "crossSeriesReducer": "REDUCE_COUNT_FALSE", "groupByFields": ["resource.label.project_id"]}],
     "trigger": {"count": 1}}}],
 "notificationChannels": $JOINED}
JSON

echo "== Alert policies"
for p in errors uptime; do
  name="$(python3 -c "import json;print(json.load(open('$TMP/$p.json'))['displayName'])")"
  if gcloud alpha monitoring policies list --project "$PROJECT_ID" --filter="displayName=\"$name\"" --format='value(name)' | grep -q .; then
    echo "  exists: $name"
  else
    gcloud alpha monitoring policies create --project "$PROJECT_ID" --policy-from-file="$TMP/$p.json" >/dev/null && echo "  created: $name"
  fi
done
rm -rf "$TMP"
echo "Done. Redeploy (push to main) so the app picks up ALERT_EMAIL. Google will send a confirmation email to each address first."
