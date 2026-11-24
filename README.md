# HalalLens Backend

FastAPI backend foundation for the HalalLens product and ingredient research application.

## Requirements

- Python 3.12+
- Docker Compose (optional, for PostgreSQL 16 and Redis)

## Run locally

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The checked-in `.env.example` points to the local PostgreSQL database. If `DATABASE_URL` is unset, the app uses the local SQLite database. For deployment, configure `DATABASE_URL` in the hosting environment with the Supabase PostgreSQL async connection string; keep the password in deployment secrets and out of `.env.example` and source control. The local `.env` and Compose setup can continue using the local database.

## Run tests

```powershell
python -m pytest
```

## Extract text from product images

Use Python 3.12 for the OCR environment. PaddlePaddle's Windows wheel was verified for
CPython 3.12; the checked-in Python 3.14 virtual environment cannot run this OCR stack.
Install dependencies into a Python 3.12 virtual environment:

```powershell
Set-Location backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

FastAPI initializes the OCR runtime at startup and loads only the requested language pipelines on
the first scan. Latin and Arabic use PP-OCRv5 mobile models; Simplified Chinese uses PP-OCRv4
mobile models. Optional document and text-line orientation models are disabled to reduce memory
use. The first scan for each language downloads any missing models and therefore needs network
access; model downloads may take longer on a fresh installation. Loaded OCR models are reused for
later scans and released during application shutdown.

`POST /api/v1/scan/extract-text` remains synchronous and preserves its successful response
fields; it also returns `status`, `message`, and a `data.extracted_text` envelope. The backend
allows up to 600 seconds for OCR processing by default; override this with the
positive `OCR_PROCESSING_TIMEOUT_SECONDS` environment variable. The Flutter scanner waits up to
660 seconds for an OCR response and displays a slow-processing message while continuing to wait.
Invalid inputs and server failures use a consistent JSON error response with `success`,
`error_code`, `message`, and `retryable`. Swagger is available at `/docs`; upload a JPG, JPEG,
PNG, or WebP image to the endpoint. Uploads are limited to 10 MiB.

## API

OpenAPI docs are available at `/docs`. Versioned endpoints are under `/api/v1`.

### Barcode lookup

`POST /api/v1/scan/barcode` accepts a JSON body such as
`{"barcode": "5449000000996"}`. It validates the GTIN check digit, checks the local
product cache first, and queries Open Food Facts on a cache miss. Source-provided ingredients,
categories, labels, origins, and available nutrition are kept as source data; Open Food Facts
labels are not treated as verified halal certifications. The existing
`POST /api/v1/scans/barcode` scan-recording endpoint is unchanged.

### Product details translation

`POST /api/v1/translations/product-details` translates only the supplied product detail
fields into `en`, `zh-CN`, `fr`, or `ar`. Enable Google Cloud Translation API and configure
`GOOGLE_TRANSLATION_API_KEY` in the backend environment; keep this credential server-side and
never ship it with the mobile app. Product text is sent to Google Cloud Translation when no
cached translation exists. Results are cached by source-text hash and target language in a
separate table; source product data is not modified. Apply the migration with
`alembic upgrade head`.

Run `alembic upgrade head` before deploying. The barcode-data migration adds source metadata
and a unique product barcode index; it stops rather than deleting data if duplicate barcode
records already exist. Product analysis continues to return `UNKNOWN` until the existing
analysis service has verified evidence to support another status.

Automated analysis is intentionally a placeholder: it records an `UNKNOWN` result and unverified evidence rather than making an unsupported Halal/Haram determination.
