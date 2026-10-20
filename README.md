# Althais

Althais is AI-assisted billing and coding software for small emergency and urgent care clinics. It suggests ICD-10 and CPT codes from clinical notes, checks claims before they go out (code format, NCCI bundling, missing information, filing deadlines), and runs the claim from creation through the clearinghouse.

Live app: [althais.com](https://althais.com)

## What's in the app

| Area | What it does | Main code |
|---|---|---|
| Marketing site | Landing page, product pages, pricing, contact, legal | `templates/landing.html`, `more_pages.py`, `platform_pages.py`, `product_pages_rc.py`, `marketing_data.py` |
| EMR (new UI) | Schedule, encounters, patients, charts, documents | `templates/v4/`, `static/v4/`, `v4_preview.py` |
| EMR (classic) | The original clinical workspace and Virtual SOAP | `templates/dashboard.html`, `main.py` |
| Revenue | Claims, Claim Tracker, Denials, Payer Rules, Code a Note | `main.py`, `ncci_checker.py`, `code_validation.py`, `coding_rules.py`, `icd10.py` |
| Connect EMR | Bring claims in from an EMR (athenahealth, DrChrono, Tebra, or any CSV/Excel export) and check them | `emr_connect.py`, `athenahealth.py`, `drchrono.py`, `tebra.py`, `static/v4/app8.js` |
| Browser extension | Althais on top of the clinic's EMR: claim checks, coding a visit note, creating claims | `extension/` ([README](extension/README.md)), `extension_api.py`, test screens in `static/mock-emr/` |
| Billing Activation | Gets a clinic ready to send claims; Claim.MD connection | `billing_activation.py`, `billing_registry.py`, `clearinghouse.py`, [docs](docs/BILLING_ACTIVATION.md) |
| Patient intake | Scan To Autofill for new-patient registration | `patient_intake.py`, `eligibility.py`, [docs](docs/PATIENT_INTAKE.md) |
| Staff portal | Onboarding, credentials, training, document review, offboarding | `staff_*.py`, `althais_training.py`, `training_content.py`, `practice_pages.py` |
| Althea | Voice and chat assistant across the app | `staff_assistant.py`, `policy_knowledge.py`, `static/` |
| Accounts | Sign-in, roles, teams, admin console | `auth.py`, `templates/admin_*.html` |

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Jinja2 templates
- **Database:** Supabase Postgres in production, SQLite locally
- **AI:** Groq (coding and Althea) and Anthropic (document reading for patient intake)
- **Frontend:** server-rendered HTML with Tailwind and plain JavaScript; the new UI lives in `static/v4/`
- **Hosting:** Railway, which deploys `main` automatically on every push

## Run it locally

Requires Python 3.12 or newer (the tests run on 3.13).

```bash
git clone https://github.com/king3052/Merged_Althais.git
cd Merged_Althais
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env              # Windows: copy .env.example .env
uvicorn main:app --reload
```

Open http://localhost:8000. Fill in `.env` first: `SECRET_KEY` and `ADMIN_PASSWORD` can be anything locally, and `GROQ_API_KEY` is needed for AI coding. Ask Kanishk or Kevin for development keys. Never commit `.env`.

## Run the tests

```bash
python -m pytest tests -q
```

The tests use a throwaway SQLite database and fake keys (see `tests/conftest.py`), so they never touch real data.

## Project layout

```
main.py                 App entry point: routes for the EMR, coding and claims, and router registration
auth.py                 Accounts, sessions, roles, organizations, database models
*_pages.py              Page routes for the marketing site and practice/platform pages
staff_*.py              Staff portal features
billing_*.py            Billing Activation and the claim sending rules
clearinghouse.py        Clearinghouse connector (Claim.MD)
ncci_checker.py         NCCI bundling checks (reads ncci_edits.db)
templates/              Jinja2 HTML templates (templates/v4/ is the new UI)
static/                 CSS, JavaScript, images (static/v4/ is the new UI)
public/                 Marketing videos
data/                   ICD-10-CM code list
prompts/                AI prompts
docs/                   Feature documentation
tests/                  Pytest tests
extension/              Chrome/Edge extension (load it unpacked from this folder)
```

## Working on the code

Read [CONTRIBUTING.md](CONTRIBUTING.md) before your first change. In short: branch from `main`, open a pull request, and never put real patient data or secrets in the repo.

## Data and privacy

Althais handles protected health information. Use synthetic test data only, keep keys in environment variables, and talk to Kanishk before adding anything that stores, logs or sends patient data.

## Team

- Kevin Qu
- Kanishk Yankarla
- Ammar Multani
