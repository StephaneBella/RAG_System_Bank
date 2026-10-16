# RAG Project — Secure Document Library

A web application where authenticated users browse and search a document corpus
strictly filtered by their business layer (Direction Générale, Conformité &
Risques, Direction Juridique, Finances & Comptabilité, Ressources Humaines).
Every access is audited.

The long-term goal is a RAG chatbot over bank documents. The current increment
delivers the secure foundation first: a role-filtered document library with
search (no LLM/RAG generation yet).

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
| CI (Ruff + pytest + frontend lint/typecheck/test/build) | ✅ Done |

Full backlog: [`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French).

## Stack

- **Backend:** FastAPI (Python 3.11+), SQLAlchemy 2.0, Alembic, PostgreSQL
- **Config:** `pydantic-settings` + `backend/.env`
- **Frontend:** React 18 + Vite + TypeScript + Tailwind, managed with `pnpm`
  (integrated against the real backend API)
- **Search:** Backend search endpoint over a file-based document index; the
  library UI currently filters by document title on the client
- **CI:** GitHub Actions — Ruff + pytest (backend) and pnpm
  lint/typecheck/test/build (frontend)
- **Tooling:** `uv` (dependency management), `ruff` (format/lint), `pytest`

---

## 1. Project structure

```text
project-root/
│
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── core/               # Configuration & settings, security helpers
│   │   ├── db/                 # SQLAlchemy Base, session, startup seeding
│   │   ├── models/             # SQLAlchemy models (user, department, audit_log,
│   │   │                       #   user_invitation, revoked_token)
│   │   ├── schemas/            # Pydantic API schemas
│   │   ├── api/                # API routes
│   │   │   └── routes/         # auth, documents, users, departments, audit
│   │   ├── services/           # Business logic (audit, documents, invitations…)
│   │   └── main.py             # FastAPI app entry point
│   │
│   ├── tests/                  # Backend tests (auth, documents, users, audit)
│   ├── alembic/                # Database migrations
│   │   └── versions/           # Migration revision files
│   ├── alembic.ini             # Alembic config
│   ├── .env                    # Local secrets (NOT committed)
│   ├── .env.example            # Environment variable template (committed)
│   ├── pyproject.toml          # Dependencies + ruff/pytest config
│   └── uv.lock
│
├── frontend/                   # React + Vite + TypeScript app (pnpm)
│   ├── src/
│   │   ├── api/                # Axios client, endpoints, query keys, types
│   │   ├── components/         # UI, document and user components
│   │   ├── hooks/              # React Query hooks
│   │   ├── i18n/               # Internationalization (FR default, EN)
│   │   ├── pages/              # Route pages (library, admin, auth)
│   │   └── main.tsx
│   └── package.json
│
├── data/
│   ├── documents/              # Development/sample documents + file index
│   └── archive/                # Superseded files (gitignored)
│
├── docs/                       # Project documentation
│   ├── Sprint-Backlog.docx     # Sprint backlog (French)
│   └── Gouvernance du code.docx  # Code governance charter (French)
│
├── .github/workflows/ci.yml    # CI configuration (backend + frontend)
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
- Node.js 20+ and [`pnpm`](https://pnpm.io/) (frontend)

Check your installations:

```bash
git --version
python --version
uv --version
psql --version
node --version
pnpm --version
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

# Optional: create the seed departments + an admin user on startup
SEED_ON_STARTUP=false
SEED_ADMIN_EMAIL=admin@banque.fr
SEED_ADMIN_PASSWORD=
```

| Variable | Purpose |
|---|---|
| `APP_ENV` | Environment name (`dev`, `staging`, `production`) |
| `DATABASE_URL` | SQLAlchemy URL — `postgresql+psycopg://user:pass@host:5432/db` |
| `JWT_SECRET_KEY` | Signing key for JWTs |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime (default 60) |
| `CORS_ORIGINS` | JSON list of allowed frontend origins |
| `SEED_ON_STARTUP` | When `true`, seed departments + admin on startup (default `false`) |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | Admin credentials for the seeder (no admin is created unless the password is set) |

Generate a strong JWT secret:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Settings are loaded by `backend/app/core/config.py` via `pydantic-settings`.
`DATABASE_URL` and `JWT_SECRET_KEY` are **required** — anything that loads
`settings` (Alembic, the API) raises `ValidationError` if `.env` is missing or
incomplete.

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
- `env.py` does `import app.models`, which pulls in every model through
  `app/models/__init__.py`, so autogenerate sees them. **If you add a new model
  file, you must export it in `backend/app/models/__init__.py`** or
  autogenerate will silently ignore it.

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
users
audit_logs
user_invitations
revoked_tokens
```

> Documents are **not** stored in the database — they live as files under
> `data/documents/` with a `.documents.json` index (see section 12).

Inspect a table:

```sql
\d users
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

1. **Edit the SQLAlchemy model**, e.g. `backend/app/models/audit_log.py`.
   New model file? Export it in `backend/app/models/__init__.py` too.
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

## 8. Running the app

### 8.1 Backend

From `backend/`:

```bash
uv run uvicorn app.main:app --reload
```

| URL | Purpose |
|---|---|
| `http://localhost:8000/docs` | Swagger UI (OpenAPI) |
| `http://localhost:8000/health` | Health check → `{"status": "ok"}` |

Authenticated routes take `Authorization: Bearer <token>`. The main surface:

| Method & path | Purpose |
|---|---|
| `POST /auth/login` | Exchange email + password for a JWT |
| `GET /auth/me` | Current user profile |
| `POST /auth/logout` | Revoke the current token |
| `POST /auth/accept-invitation` | Set a password from an invitation token |
| `GET /documents/get_all` | List accessible documents (paged, sorted) |
| `GET /documents/{id}` | Document metadata (department-scoped) |
| `GET /documents/{id}/content` | Inline document file (PDF/DOCX) |
| `POST /documents/add` | Create a document (ADMIN) |
| `PUT /documents/update/{id}` | Update a document (ADMIN) |
| `DELETE /documents/delete/{id}` | Delete a document (ADMIN) |
| `GET /documents/search_document` | Search documents by text (paged) |
| `GET /users` | List users (ADMIN) |
| `PATCH /users/{id}` | Update a user (ADMIN) |
| `POST /users/{id}/status` | Activate/deactivate a user (ADMIN) |
| `POST /users/invitations` | Invite a user (ADMIN) |
| `GET /departments` | List departments |
| `GET /audit` | Query the audit log (ADMIN, paged + filtered) |
| `GET /audit/export` | Export the audit log as CSV (ADMIN) |

### 8.2 Frontend

From `frontend/`:

```bash
pnpm install
pnpm dev        # http://localhost:5173
```

Point the app at the backend with `VITE_API_BASE_URL` in `frontend/.env`
(defaults to `http://localhost:8000`). The frontend talks to the real API —
there are no in-browser mocks.

Other scripts: `pnpm build`, `pnpm lint`, `pnpm typecheck`, `pnpm test`.

---

## 9. Tests

From `backend/`:

```bash
uv run pytest
uv run pytest -v          # verbose
uv run pytest --cov=app   # with coverage
```

> **Current state:** the suite covers authentication, authorization (role +
> department scoping), documents (CRUD, content, filename parsing), users and
> invitations, and audit logging. Tests use an isolated database and a temporary
> documents directory, so they never touch your dev data.

Frontend tests run with `pnpm test` (Vitest).

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

On every push and pull request, GitHub Actions runs two jobs.

**Backend** (working directory `backend/`):

1. `uv sync`
2. `uv run ruff format --check .`
3. `uv run ruff check .`
4. `uv run pytest`

**Frontend** (working directory `frontend/`):

1. `pnpm install --frozen-lockfile`
2. `pnpm lint`
3. `pnpm typecheck`
4. `pnpm test`
5. `pnpm build`

---

## 11. Sprint 1 — security work

The full backlog is in
[`docs/Sprint-Backlog.docx`](docs/Sprint-Backlog.docx) (French). The headings
below use the **backlog section numbers** (§3–§8) so this README and the
backlog line up. Security concerns that cut across several sections are
grouped at the end.

### §3 — Authentication & session management (backlog §3) — ✅ implemented

- Login with email + password; passwords hashed with Argon2 (via `pwdlib`)
- JWT issued on login; the backend resolves user, role and department from the token
- Token expiration + revocation on logout, backed by the `revoked_tokens` table
- Coherent, non-leaking errors for invalid, expired, revoked and unauthenticated
  access

### §4 — User management (backlog §4) — ✅ implemented

- Account activation/deactivation (`ACTIVE`/`INACTIVE`) controls access
- ADMIN user management: list, update role/department, enable/disable
- Invitation flow: an ADMIN invites an email; the user accepts and sets a
  password (`user_invitations`)

### §5 — Document consultation & management (backlog §5) — ✅ implemented

**§5.2 EMPLOYEE**

- Search/view documents **only within their own department** (plus `public`
  documents), including 403 (not a 404-leak) when guessing another document's ID

**§5.3 ADMIN**

- Document CRUD (add / update / delete), department + visibility assignment,
  pagination and sorting

Every endpoint enforces access control **in the backend** — the frontend is
not a security boundary.

### §6 — Audit & traceability (backlog §6) — ✅ implemented

Audit events are written on authentication, user management, document access
(including **denied** cross-department attempts) and admin actions, then exposed
through `GET /audit` and `GET /audit/export`. Passwords, tokens and secrets are
never logged.

### §8 — Tests & validation (backlog §8) — ✅ implemented

- Backend suite covers auth, authorization, documents, users/invitations and audit
- CI runs the backend suite plus frontend lint/typecheck/test/build

### Cross-cutting security (spans §1–§5)

- **Input validation (backlog §5.3)** — ✅ Pydantic schemas for every request
  body, path and query parameter; pagination/sorting validated and bounded
- **File & document security (backlog §5.1, §5.3)** — ✅ upload validation
  (`.pdf`/`.docx` only, size limit, sanitized names), path-traversal protection,
  storage outside the database, department scoping on every read
- **Database security (backlog §2)** — SQLAlchemy parameterization (no raw SQL);
  least-privilege DB credentials; secrets only in `.env`; migration review: no
  destructive changes without team agreement
- **Secrets hardening (backlog §1)** — still open: `SecretStr` masking,
  fail-fast validation (placeholder detection, JWT secret ≥ 32 chars, no
  localhost DB in staging) and the config tests that cover these rules

### Still open

- Secrets hardening (above)
- Richer document metadata (source, type, date, classification, version,
  checksum) and true full-text / RAG search

Suggested cycle for each feature:
**Define → Implement → Test → Document → Validate.**

---

## 12. Current data model

```text
Department
    │
    ├──< User
    │      │
    │      ├──< AuditLog
    │      └──< UserInvitation
    │
    └── (documents are file-based, not rows)
```

| Table | Columns (current) |
|---|---|
| `departments` | `id`, `name` (unique) |
| `users` | `id`, `email` (unique), `password_hash`, `status` (`ACTIVE`/`INACTIVE`), `role` (`ADMIN`/`EMPLOYEE`), `department_id` → departments |
| `audit_logs` | `id`, `user_id` → users, `action`, `resource_type`, `resource_id`, `result`, `timestamp`, `ip_address` |
| `user_invitations` | `id`, `user_id` → users, `token_hash` (unique), `created_at`, `expires_at`, `used_at` |
| `revoked_tokens` | `jti` (PK), `expires_at` |

Documents are **not** stored in the database. Each file lives under
`data/documents/` with a `.documents.json` index; the title, visibility and
department are parsed from the filename using the convention
`{visibility}__{department_token}__{title}.{ext}` (e.g.
`public__direction_générale__Rapport_annuel_COBAC_2024.pdf`). Expect future work
to move documents into the database with richer metadata.

Audit `action`/`resource_type`/`result` values come from the `AuditAction`,
`ResourceType` and `AuditResult` enums in `app/models/audit_log.py` (stored as
plain strings). The backlog also calls for richer document metadata (source,
type, date, classification, version, checksum).

---

## 13. Access control model

```text
User
 ├── Role   (ADMIN | EMPLOYEE)
 └── Department  (Direction Générale, Conformité & Risques, Direction Juridique,
                  Finances & Comptabilité, Ressources Humaines)
```

- **EMPLOYEE:** authenticate, search documents, view documents — own department
  (plus `public` documents) only.
- **ADMIN:** everything an employee can do, plus manage documents and users.

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
`docs:` — optionally scoped, e.g. `feat(documents): ...` or `fix(audit): ...`.

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
2. Install prerequisites (uv, PostgreSQL, Node + pnpm)
        ↓
3. cp .env.example .env  →  set DATABASE_URL + JWT_SECRET_KEY
        ↓
4. Create database rag_project_dev
        ↓
5. uv run alembic upgrade head   (verify with \dt)
        ↓
6. uv run uvicorn app.main:app --reload   (check /health, /docs)
        ↓
7. cd frontend && pnpm install && pnpm dev
        ↓
8. Read docs/Sprint-Backlog.docx + docs/Gouvernance du code.docx
        ↓
9. Create your security/* branch
        ↓
10. Start with: secrets hardening → richer document metadata → RAG search
```

Reading order for security work:

```text
README → app/models/ → app/api/routes/ → app/services/ → tests
      → docs/Sprint-Backlog.docx (remaining: secrets hardening, RAG search)
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
