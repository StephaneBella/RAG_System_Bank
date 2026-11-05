# RAG Project — Secure Document Library

A web application where authenticated users browse and search a document corpus
strictly filtered by their business layer (Front Office, Credit & Risks,
Compliance/LBC-FT, Management). Every access is audited.

The long-term goal is a RAG chatbot over bank documents. The current increment
delivers the secure foundation first: a role-filtered document library with
simple full-text search. A RAG assistant is implemented but **disabled by
default** and, even when enabled, **refuses every user** until the department →
business layer mapping (decision H10) is defined (see section 5).

**This README is the onboarding guide for the security team.** It explains how
to set up the project, how to use Alembic migrations for testing, what the
security-relevant backlog items are, and where to start.

## Status at a glance

| Area | Status |
|---|---|
| Project structure, `uv` env, settings/secrets loading | ✅ Done |
| Data model + Alembic migrations (up to date) | ✅ Done |
| Health endpoint (`GET /health`) | ✅ Done |
| Authentication / JWT / sessions | ✅ Done |
| Authorization (role + department enforcement) | ✅ Done |
| User management + invitation flow | ✅ Done |
| Document endpoints, search & file storage | ✅ Done |
| Audit logging (writers + list/export API) | ✅ Done |
| Backend test suite | ✅ Done (`uv run pytest`) |
| Frontend (React + Vite + TypeScript) | ✅ Done (integrated against the real API) |
| CI (Ruff + ESLint + pytest + build) | ✅ Done |

Full backlog: [`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French).

## Stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy 2.0, Alembic, PostgreSQL
- **Config:** `pydantic-settings` + `backend/.env`
- **Frontend:** React + Vite + TypeScript (integrated against the real backend API)
- **Search:** In-file index search over stored documents
- **CI:** GitHub Actions — Ruff + ESLint + pytest + frontend build
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

### RAG assistant (optional, disabled by default)

The RAG assistant exposes `POST /rag/chat` and `POST /rag/chat/stream`. It is
**disabled by default** (`RAG_ENABLED=false`).

- **LLM provider:** Groq, through `ChatGroq` (`langchain-groq`), is the **only**
  provider. There is no other provider and no fallback. Every call goes through
  `backend/app/services/Agentic_RAG/llm_adapters.py`: one request context per
  question, masking/pseudonymisation before sending, call and size limits, and
  one `LLM_CALL` audit event per provider call (no content).
- **Known limits:** emails, IBANs, card and phone numbers are masked, but client
  **names are not pseudonymised**: pseudonymisation only applies to known values
  and the RAG provides none. Streaming is not progressive: the answer is sent as
  one chunk, after its tokens are restored.
- **External provider consent:** Groq is an external API. The RAG can only be
  enabled with `ALLOW_EXTERNAL_LLM=true`, the explicit consent of decision H1.
- **Dependencies:** the RAG stack lives in the optional `rag` dependency group
  (Python ≥ 3.12). `uv sync` does not install it (the CI installs it in a
  dedicated step):

  ```bash
  uv sync --group rag
  ```

Settings read by the RAG (see `backend/.env.example`, all optional while the
RAG is disabled):

| Variable | Default | Purpose |
|---|---|---|
| `RAG_ENABLED` | `false` | Enables the RAG routes and their startup |
| `ALLOW_EXTERNAL_LLM` | `false` | Consent to call Groq; **required** (`true`) when the RAG is enabled |
| `LLM_MODEL` | — | Groq model identifier; **required**. A leading `groq:` is removed |
| `GROQ_API_KEY` | — | Groq API key (secret); **required** |
| `QDRANT_URL` | — | Internal Qdrant server, `http(s)://host:port`; **required**. The host must be in `QDRANT_INTERNAL_HOSTS`; no credentials in the URL; embedded mode (`:memory:`, a path) is refused |
| `QDRANT_API_KEY` | — | Qdrant API key (secret); **required** |
| `QDRANT_COLLECTION` | `rag_system_bank` | Collection queried by the RAG |
| `QDRANT_INTERNAL_HOSTS` | `["127.0.0.1","localhost","qdrant"]` | Allowed Qdrant hosts (checked at startup) |
| `QDRANT_HTTPS` | `false` | Must match the `QDRANT_URL` scheme (checked at startup) |
| `MAX_PROVIDER_CALLS` | `12` | Provider calls per question |
| `MAX_PROMPT_CHARS` | `32000` | Characters sent per provider call |
| `TIMEOUT_SECONDS` | `30.0` | Timeout of each provider call |

Declared and validated, **not used by the code yet**: `QDRANT_TIMEOUT`,
`QDRANT_INGEST_API_KEY`, `EMBEDDING_CACHE_DIR`, `EMBEDDING_DEVICE`,
`EMBEDDING_OFFLINE`, `EMBEDDING_MODEL`. The embedding model is currently loaded by
`Data_preparation/vectorisation.py` from the **process environment** variable
`EMBEDDING_MODEL` (default
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`), not from `.env`.
The ingestion pipeline (`Data_preparation/storing.py`, used by `run_data_preparation.py`) reads
`QDRANT_URL`, `QDRANT_API_KEY` and `QDRANT_COLLECTION` itself and does not use
`QDRANT_INGEST_API_KEY` yet.

Behaviour:

| Situation | Result |
|---|---|
| `RAG_ENABLED=false` (default) | The API starts; the RAG stack is not imported; `/rag/*` answers `503 {"detail": "Assistant RAG indisponible"}` |
| `RAG_ENABLED=true` with a missing or invalid setting above | `settings` raises `ValidationError`: the API (and anything loading `settings`) does **not** start. Messages name the fields, never their values |
| `RAG_ENABLED=true`, `rag` group not installed | The API starts; log `RAG enabled but not importable: <ErrorType>`; `/rag/*` answers 503 |
| RAG initialisation fails (embedding model, Qdrant unreachable, collection missing) | The API starts; log `RAG initialisation failed: <ErrorType>` (type only); `/rag/*` answers 503 |
| RAG running | Authenticated users with the `search` permission. The department → business layer mapping (decision H10) is **not defined yet**, so every user is refused with `403` and an `ACCESS_DENIED` / `DEPARTMENT_FORBIDDEN` audit event |

> Keep LangChain/LangSmith tracing disabled (`LANGSMITH_TRACING` unset or
> `false`): a tracer would receive the messages before the adapters mask them.

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

The API also exposes the authentication, documents, users, departments and
audit routes (`backend/app/api/router.py`, listed in Swagger UI), and the RAG
routes `POST /rag/chat` and `POST /rag/chat/stream`, which answer 503 while the
RAG is disabled (see section 5).

---

## 9. Tests

From `backend/`:

```bash
uv run pytest
uv run pytest -v          # verbose
uv run pytest --cov=app   # with coverage
```

> **Current state:** the backend suite covers authentication, documents,
> users, audit, suspicious-activity detection, the US 07 modules and the RAG
> assistant. The RAG tests (`tests/test_rag_access.py`, `test_rag_audit.py`,
> `test_rag_llm_adapters.py`, `test_main_rag.py`) need the `rag` dependency
> group (`uv sync --group rag`); without it they are skipped. The CI installs the
> group in a dedicated step and runs them. Every security feature must ship with
> tests (backlog §8, summarized in section 11).

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
[`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French). The headings
below use the **backlog section numbers** (§3–§8) so this README and the
backlog line up. Security concerns that cut across several sections are
grouped at the end.

### §3 — Authentication & session management (backlog §3)

- Login with email + password; secure password hashing (bcrypt/argon2)
- JWT issued on login; backend identifies user, role, department from the token
- Token expiration and session invalidation; expired/invalid tokens rejected
- Coherent, non-leaking error responses (invalid credentials, disabled
  account, unauthenticated access)

### §4 — User management (backlog §4)

- Account activation/deactivation controls access
- ADMIN user management (optional for Sprint 1): create, disable, change
  department, reset password

### §5 — Document consultation & management (backlog §5)

**§5.2 EMPLOYEE**

- Search/view documents **only within their own department**, including when
  they guess another document's ID (403, not 404-leak)

**§5.3 ADMIN**

- Document CRUD (add / update / delete / archive), department assignment,
  pagination and sorting

Every endpoint enforces access control **in the backend** — the frontend is
not a security boundary.

### §6 — Audit & traceability (backlog §6)

The `audit_logs` table exists but **nothing writes to it yet**. Implement
writers for:

- successful/failed logins, logouts, session expirations
- document views — **especially denied cross-department attempts**
- admin actions: create / update / delete / archive
- never log passwords, tokens or secrets

### §8 — Tests & validation (backlog §8)

- Auth: valid login, wrong credentials, disabled account, expired token
- Authorization: employee blocked from other departments; employee blocked
  from admin operations
- Audit: allowed, denied and admin actions are recorded
- Rebuild the CI pytest step once tests exist

### Cross-cutting security (spans §1–§5)

- **Input validation (backlog §5.3)** — Pydantic schemas for every request
  body, path and query parameter; pagination/sorting inputs validated and
  bounded
- **File & document security (backlog §5.1, §5.3)** — upload validation:
  allowed MIME types, max size, safe filenames; protection against path
  traversal in `file_path`; secure storage; documents inaccessible across
  departments
- **Database security (backlog §2)** — no SQL injection (SQLAlchemy
  parameterization; audit raw SQL if any); least-privilege DB credentials;
  secrets only in `.env`; migration review: no destructive changes without
  team agreement
- **Secrets hardening (backlog §1, gap from last restructure)** — re-implement
  `SecretStr` masking, fail-fast validation (placeholder detection, JWT
  secret ≥ 32 chars, no localhost DB in staging); restore the config tests
  that covered these rules

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
