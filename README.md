# RAG Project — Secure Document Library

A web application where authenticated users browse and search a document corpus
strictly filtered by their business layer (Front Office, Credit & Risks,
Compliance/LBC-FT, Management). Every access is audited.

The long-term goal is a RAG chatbot over bank documents. The current increment
delivers the secure foundation first: a role-filtered document library with
simple full-text search (no LLM/RAG generation yet).

**This README is the onboarding guide for the security team.** It explains how
to set up the project, how to use Alembic migrations for testing, what the
security-relevant backlog items are, and where to start.

## Status at a glance

| Area | Status |
|---|---|
| Project structure, `uv` env, settings/secrets loading | ✅ Done |
| Data model (4 tables) + initial Alembic migration | ✅ Done |
| Health endpoint (`GET /health`) | ✅ Done |
| Authentication / JWT / sessions | ❌ Next sprint |
| Authorization (role + department enforcement) | ❌ Next sprint |
| Document endpoints & search | ❌ Next sprint |
| Audit logging (writers) | ❌ Next sprint (model exists, nothing writes yet) |
| Backend test suite | ❌ Cleared, being rebuilt (CI pytest step disabled) |
| Frontend (React + Vite) | ❌ Not started (`frontend/` is empty) |

Full backlog: [`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French).

## Stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy 2.0, Alembic, PostgreSQL
- **Config:** `pydantic-settings` + `backend/.env`
- **Frontend:** React + Vite + TypeScript *(planned, not yet implemented)*
- **Search:** PostgreSQL full-text search *(planned)*
- **CI:** GitHub Actions — Ruff format + lint *(tests step currently disabled)*
- **Tooling:** `uv` (dependency management), `ruff` (format/lint), `pytest`

---

## 1. Project structure

```text
project-root/
│
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── core/               # Configuration & settings (config.py)
│   │   ├── db/                 # SQLAlchemy Base (base.py)
│   │   ├── models/             # SQLAlchemy models (user, department,
│   │   │                       #   document, audit_log)
│   │   ├── schemas/            # Pydantic API schemas (empty, to be built)
│   │   ├── api/                # API routes (router.py empty, to be built)
│   │   │   └── routes/
│   │   ├── services/           # Business logic (empty, to be built)
│   │   └── main.py             # FastAPI app entry point
│   │
│   ├── tests/                  # Backend tests (empty, being rebuilt)
│   ├── alembic/                # Database migrations
│   │   └── versions/           # Migration revision files
│   ├── alembic.ini             # Alembic config
│   ├── .env                    # Local secrets (NOT committed)
│   ├── .env.example            # Environment variable template (committed)
│   ├── pyproject.toml          # Dependencies + ruff/pytest config
│   └── uv.lock
│
├── frontend/                   # Frontend app (planned — currently empty)
│
├── data/
│   └── documents/              # Development/sample documents
│
├── docs/                       # Project documentation
│   ├── Sprint-Backlog.docx     # Sprint backlog (French)
│   └── Gouvernance du code.docx  # Code governance charter (French)
│
├── .github/workflows/ci.yml    # CI configuration
├── .gitignore
└── README.md
```

---

## 2. Prerequisites

Before starting, install:

- Git
- Python 3.11+ (local venv runs 3.13; CI uses 3.12)
- [`uv`](https://docs.astral.sh/uv/)
- PostgreSQL
- Node.js + npm *(only when the frontend work starts)*

Check your installations:

```bash
git --version
python --version
uv --version
psql --version
```

---

## 3. Clone the repository

```bash
git clone <REPOSITORY_URL>
cd <PROJECT_FOLDER>
```

---

## 4. Backend setup

```bash
cd backend
uv sync
```

This creates the virtual environment and installs everything from
`pyproject.toml` / `uv.lock`.

---

## 5. Environment configuration

Create your local `.env` from the template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then edit `backend/.env`:

```env
APP_ENV=dev
APP_NAME=RAG for Bank Document Library API

DATABASE_URL=postgresql+psycopg://postgres:YOUR_DB_PASSWORD@localhost:5432/rag_project_dev

JWT_SECRET_KEY=replace-with-a-generated-secret
ACCESS_TOKEN_EXPIRE_MINUTES=60

CORS_ORIGINS=["http://localhost:5173"]
```

| Variable | Purpose |
|---|---|
| `APP_ENV` | Environment name (`dev`, `staging`, `production`) |
| `DATABASE_URL` | SQLAlchemy URL — `postgresql+psycopg://user:pass@host:5432/db` |
| `JWT_SECRET_KEY` | Signing key for JWTs (auth not implemented yet) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime (default 60) |
| `CORS_ORIGINS` | JSON list of allowed frontend origins |

Generate a strong JWT secret:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Settings are loaded by `backend/app/core/config.py` via `pydantic-settings`.
`DATABASE_URL` and `JWT_SECRET_KEY` are **required** — anything that loads
`settings` (e.g. Alembic, and later the auth code) raises `ValidationError` if
`.env` is missing or incomplete.

### Secrets rules

1. **Storage** — real values live only in `backend/.env` (gitignored). Only
   `backend/.env.example` (placeholders) is committed.
2. **Never commit** passwords, JWT secrets, API keys, tokens or production
   credentials — not in code, not in migration files, not in logs.
3. If a secret is exposed, rotate it immediately.

> **Sprint backlog task:** harden this further — re-introduce `SecretStr`
> masking for `JWT_SECRET_KEY`/`DATABASE_URL`, fail-fast validation (placeholder
> detection, minimum JWT secret length, no `localhost` DB in staging) and the
> corresponding config tests. This existed before the last restructure and was
> removed; see section 11.

---

## 6. Database setup

Create the development database (once):

```sql
CREATE DATABASE rag_project_dev;
```

Verify:

```bash
psql -U postgres -h localhost -p 5432
```

```sql
\l
\c rag_project_dev
```

**Do not create or alter application tables by hand in SQL.** The schema is
managed exclusively through Alembic (next section).

---

## 7. Alembic — database migrations

Alembic is how you create, update and **test** schema changes. Security
contributors will use it heavily: every new constraint, column, index or table
needed for a security feature goes through a migration.

### 7.1 How it is wired

```text
backend/.env  →  app/core/config.py (Settings)  →  alembic/env.py
                                                      ↓
                                   overrides sqlalchemy.url in alembic.ini
```

- `alembic.ini` ships a **placeholder** URL (`driver://user:pass@localhost/dbname`)
  — that is intentional. `alembic/env.py` reads `settings.database_url` from
  your `.env` and injects it. **Migrations always run against the database in
  your `.env`.** Double-check that file before running anything destructive.
- `env.py` explicitly imports all four models (`User`, `Department`,
  `Document`, `AuditLog`) so autogenerate sees them. **If you add a new model
  file, you must import it in `backend/alembic/env.py`** or autogenerate will
  silently ignore it.

### 7.2 First migration (fresh setup)

From `backend/`:

```bash
uv run alembic upgrade head
```

Verify the tables exist:

```bash
psql -U postgres -h localhost -p 5432 -d rag_project_dev -c "\dt"
```

Expected tables:

```text
departments
documents
users
audit_logs
```

Inspect a table:

```sql
\d users
\d documents
\d audit_logs
```

### 7.3 Day-to-day commands

| Command | What it does |
|---|---|
| `uv run alembic upgrade head` | Apply all pending migrations |
| `uv run alembic current` | Show which revision the DB is on |
| `uv run alembic history` | List all revisions (newest first) |
| `uv run alembic heads` | Show the latest revision(s) |
| `uv run alembic check` | Fail if models and migrations are out of sync |
| `uv run alembic downgrade -1` | Roll back the last migration |

All commands run from `backend/`.

### 7.4 Changing the schema (migration workflow)

1. **Edit the SQLAlchemy model**, e.g. `backend/app/models/document.py`.
   New model file? Add the import to `backend/alembic/env.py` too.
2. **Generate the migration:**

   ```bash
   uv run alembic revision --autogenerate -m "describe the change"
   ```

3. **Review the generated file** in `backend/alembic/versions/`. Autogenerate
   is a draft — check it creates/drops exactly what you intended, and that a
   destructive change (dropping a column/table) is really what you want.
4. **Apply it:**

   ```bash
   uv run alembic upgrade head
   ```

5. **Confirm sync:**

   ```bash
   uv run alembic check
   ```

### 7.5 Testing migrations (recommended for security work)

Always prove a migration is safe before pushing it:

```bash
# Round-trip test: apply, roll back, re-apply
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head

# Full reset (dev database only — destroys all data)
uv run alembic downgrade base
uv run alembic upgrade head

# Preview the SQL that would be executed (no DB write needed)
uv run alembic upgrade head --sql
```

Notes:

- `alembic check` is the quickest guard that you forgot to generate a migration
  after editing a model — run it in your pre-push routine.
- If a database was created manually (without Alembic), stamp it once so
  Alembic tracks it without re-running migrations:
  `uv run alembic stamp head`
- Never edit an already-applied migration in a shared branch — create a new
  revision instead.

---

## 8. Running the backend

From `backend/`:

```bash
uv run uvicorn app.main:app --reload
```

| URL | Purpose |
|---|---|
| `http://localhost:8000/docs` | Swagger UI (OpenAPI) |
| `http://localhost:8000/health` | Health check → `{"status": "ok"}` |

That is currently the **only** endpoint — the API surface is sprint work.

---

## 9. Tests

From `backend/`:

```bash
uv run pytest
uv run pytest -v          # verbose
uv run pytest --cov=app   # with coverage
```

> **Current state:** the test suite was cleared during the last restructure
> and is being rebuilt. `pytest` runs green because there are no tests yet —
> not because everything passes. Every security feature must ship with tests
> (see section 11, item 8 of the backlog).

Test configuration lives in `pyproject.toml` (`testpaths = ["tests"]`).

---

## 10. Code quality & CI

Format and lint (Ruff, line length 100, target py311):

```bash
uv run ruff format .
uv run ruff check .
```

Check without modifying files:

```bash
uv run ruff format --check .
```

Before pushing, run:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

### CI (`.github/workflows/ci.yml`)

On every push and pull request, GitHub Actions runs (in `backend/`):

1. `uv sync`
2. `uv run ruff format --check .`
3. `uv run ruff check .`
4. ~~`uv run pytest`~~ — **currently commented out**; re-enable it once the
   test suite is rebuilt.

---

## 11. Sprint 1 — security work

The full backlog is in
[`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French). The
security-relevant items, summarized:

### 1. Authentication & sessions (backlog §3)

- Login with email + password; secure password hashing (bcrypt/argon2)
- JWT issued on login; backend identifies user, role, department from the token
- Token expiration and session invalidation; expired/invalid tokens rejected
- Coherent, non-leaking error responses (invalid credentials, disabled
  account, unauthenticated access)

### 2. Authorization (backlog §4, §5.2)

- **EMPLOYEE** — search/view documents **only within their own department**,
  including when they guess another document's ID (403, not 404-leak)
- **ADMIN** — document CRUD; optional user management (create, disable,
  change department, reset password)
- Every endpoint enforces access control **in the backend** — the frontend is
  not a security boundary
- Account activation/deactivation controls access

### 3. Input validation (backlog §5.3)

- Pydantic schemas for every request body, path and query parameter
- Pagination/sorting inputs validated and bounded

### 4. File & document security (backlog §5.1, §5.3)

- Upload validation: allowed MIME types, max size, safe filenames
- Protection against path traversal in `file_path`
- Secure storage; documents inaccessible across departments

### 5. Database security

- No SQL injection (SQLAlchemy parameterization; audit raw SQL if any)
- Least-privilege DB credentials; secrets only in `.env`
- Migration review: no destructive changes without team agreement

### 6. Secrets hardening *(gap from last restructure)*

- Re-implement `SecretStr` masking, fail-fast validation (placeholder
  detection, JWT secret ≥ 32 chars, no localhost DB in staging)
- Restore the config tests that covered these rules

### 7. Audit & traceability (backlog §6)

The `audit_logs` table exists but **nothing writes to it yet**. Implement
writers for:

- successful/failed logins, logouts, session expirations
- document views — **especially denied cross-department attempts**
- admin actions: create / update / delete / archive
- never log passwords, tokens or secrets

### 8. Tests (backlog §8)

- Auth: valid login, wrong credentials, disabled account, expired token
- Authorization: employee blocked from other departments; employee blocked
  from admin operations
- Audit: allowed, denied and admin actions are recorded
- Rebuild the CI pytest step once tests exist

Suggested cycle for each feature:
**Define → Implement → Test → Document → Validate.**

---

## 12. Current data model

```text
Department
    │
    ├──< User
    │      │
    │      └──< AuditLog
    │
    └──< Document
```

| Table | Columns (current) |
|---|---|
| `departments` | `id`, `name` (unique) |
| `users` | `id`, `email` (unique), `password_hash`, `status` (`ACTIVE`/`INACTIVE`), `role` (`ADMIN`/`EMPLOYEE`), `department_id` → departments |
| `documents` | `id`, `title`, `department_id` → departments, `file_path`, `created_at`, `updated_at` |
| `audit_logs` | `id`, `user_id` → users, `action`, `resource_type`, `resource_id`, `result`, `timestamp`, `ip_address` |

Defined but not yet enforced in columns: `AuditAction`, `ResourceType`,
`AuditResult` enums in `app/models/audit_log.py` (values currently stored as
plain strings). The backlog also requires richer `Document` metadata (source,
type, date, classification, version, checksum) — expect model + migration work.

---

## 13. Access control model

```text
User
 ├── Role   (ADMIN | EMPLOYEE)
 └── Department  (Front Office, Credit & Risks, Compliance/LBC-FT, Management)
```

- **EMPLOYEE:** authenticate, search documents, view documents — own department
  only.
- **ADMIN:** everything an employee can do, plus manage documents (and
  optionally users).

Rules must be enforced by the backend on every endpoint. The frontend must
**never** be treated as a security boundary.

---

## 14. Git workflow

Create your own branch before changing anything:

```bash
git checkout -b feature/<your-feature>
git checkout -b security/<your-topic>
```

Examples: `security/jwt-auth`, `security/department-enforcement`,
`security/audit-logging`.

```bash
git status
git add .
git commit -m "feat: describe the change"
git push -u origin feature/<your-feature>
```

Commit prefix convention used in this repo: `feat:`, `fix:`, `chore:`, `test:`,
`docs:`.

Before pushing:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
uv run alembic check
```

### Do

- Work on your own branch; keep changes focused
- Write tests for new functionality as you build it
- Review every autogenerated migration before applying it
- Keep secrets in `.env` only
- Update this README / `docs/` when behaviour changes

### Don't

- Commit `.env`, passwords or API keys
- Hand-edit database tables when a migration should be used
- Bypass backend authorization from the frontend
- Push directly to shared branches without agreement
- Commit `.venv/` or `__pycache__/`

---

## 15. Where to start (security contributors)

```text
1. Clone repository
        ↓
2. Install prerequisites (uv, PostgreSQL)
        ↓
3. cp .env.example .env  →  set DATABASE_URL + JWT_SECRET_KEY
        ↓
4. Create database rag_project_dev
        ↓
5. uv run alembic upgrade head   (verify with \dt)
        ↓
6. uv run uvicorn app.main:app --reload   (check /health)
        ↓
7. Read docs/Sprint-Backlog.docx + docs/Gouvernance du code.docx
        ↓
8. Create your security/* branch
        ↓
9. Start with: authentication → authorization → audit → tests
```

Reading order for security work:

```text
README → app/models/ → app/core/config.py → backlog §3 (auth)
      → backlog §4/§5 (authorization) → backlog §6 (audit) → tests
```

---

## 16. Documentation

Project documentation lives in `docs/`:

| File | Content |
|---|---|
| [`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) | Sprint backlog (French) |
| [`docs/Gouvernance du code.docx`](docs/Gouvernance%20du%20code.docx) | Code governance charter (French) |

Code, identifiers and comments are written in **English**; naming follows the
governance charter (`snake_case` functions/variables, `UPPER_SNAKE_CASE`
constants, `PascalCase` classes, comments only where they add real value).

The README is the quick-start guide; detailed technical documentation
(architecture, API reference, security rules, testing strategy) belongs in
`docs/` and should be added as the sprint progresses.
