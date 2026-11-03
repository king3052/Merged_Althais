# Althais on Google Cloud Run (built by deploy/cloudbuild.yaml; kept out of the repo root so Railway's build is unchanged). Same app as on Railway; settings come from environment variables and Secret Manager.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# never run as root
RUN useradd --create-home --uid 10001 althais && chown -R althais /app
USER althais

# one process: the billing jobs (claim batch, Claim.MD responses, ERAs, reminders) run inside it, so Cloud Run is set to
# exactly one instance with CPU always on (deploy/gcloud-deploy.sh). --proxy-headers: Cloud Run terminates HTTPS in front.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
