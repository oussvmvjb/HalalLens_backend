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

The app uses SQLite for local development when `DATABASE_URL` is unset. Set `DATABASE_URL` to a PostgreSQL async URL for PostgreSQL.

## Run tests

```powershell
python -m pytest
```

## API

OpenAPI docs are available at `/docs`. Versioned endpoints are under `/api/v1`.

Automated analysis is intentionally a placeholder: it records an `UNKNOWN` result and unverified evidence rather than making an unsupported Halal/Haram determination.
