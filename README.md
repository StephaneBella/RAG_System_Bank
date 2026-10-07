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

## Getting started

Detailed setup instructions will be added as the project foundation is built
(Python environment, database migrations, frontend install).
