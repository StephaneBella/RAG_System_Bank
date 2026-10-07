# RAG Project — Secure Document Library

A web application where authenticated users browse and search a document corpus
strictly filtered by their business layer (Front Office, Credit & Risks,
Compliance/LBC-FT, Management). Every access is audited.

Sprint 1 delivers a secure, role-filtered document library with simple
full-text search (no LLM/RAG generation yet). The RAG chatbot comes in later
sprints on top of this foundation.

## Stack

- **Backend:** FastAPI (Python 3.11), SQLAlchemy, Alembic, PostgreSQL
- **Frontend:** React + Vite + TypeScript
- **Search:** PostgreSQL full-text search
- **CI:** GitHub Actions (lint, format, tests)

## Repository structure

```
.
├── backend/    FastAPI application, tests, migrations (Alembic)
├── frontend/   React (Vite) single-page application
├── data/       Document corpus
│   ├── raw/        Source PDFs (gitignored, re-downloadable)
│   └── processed/  Extracted/normalized text and chunks (gitignored)
├── infra/      Environment configuration (.env templates, staging)
└── docs/       Project documentation (governance, sprint backlog)
```

## Code conventions

All code, identifiers and comments are written in **English**. Naming follows
the governance charter in [`docs/Gouvernance du code.docx`](docs/Gouvernance%20du%20code.docx):

- Functions / variables / files: `snake_case`, starting with a verb where relevant
- Constants: `UPPER_SNAKE_CASE`
- Classes: `PascalCase`
- Comments only where they add real value

## Secrets management

Secrets never live in code or in Git. Rules enforced by the project:

1. **Storage** — real values go in `backend/.env` (gitignored). Only
   `backend/.env.example` (placeholders) is committed.
2. **Masking** — `JWT_SECRET_KEY` and `DATABASE_URL` are typed `SecretStr`:
   they print as `**********` in logs, errors and reprs. Read a real value only
   with `.get_secret_value()` where strictly needed (e.g. JWT signing).
3. **Fail fast** — the app refuses to start if a secret is missing, a known
   placeholder, or if `JWT_SECRET_KEY` is shorter than 32 characters.
   Staging (`APP_ENV=staging`) additionally refuses a `localhost` database URL.

Generate a new JWT secret:

```powershell
cd backend
uv run python scripts/generate_secret.py
```

On a staging/production server there is no `.env` file: variables are injected
by the deployment platform as real environment variables, which always take
priority over `.env`.

## Getting started

Detailed setup instructions will be added as the project foundation is built
(Python environment, database migrations, frontend install).
