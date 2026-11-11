# Decisions

- 2026-11-11: Fresh repository, implementation starts with auth/data, frontend and deployment foundation; integration branch never deploys automatically.
- Supabase provides auth/database/storage. Bitwarden supplies secrets. No production fallback credentials or shared personal provider account.
- BFF owns sessions; no provider credentials in browser. Full Supabase and external provider verification remain release gates.

## Foundation boundary

The first integrated milestone is deliberately limited to session authentication, existing admitted workspaces, notebook CRUD and deployment tooling. It does not constitute acceptance of the full product. Remaining roles B/C and invitation onboarding must be completed before a user can exercise the full upload-to-answer path. Restricted runtime roles may not provision admissions; test fixtures are never production bootstrap.

## Verified references

- FastAPI ASGI/Uvicorn startup: https://fastapi.tiangolo.com/deployment/manually/
- SQLAlchemy transaction context: https://docs.sqlalchemy.org/en/21/orm/session_transaction.html
- Backend versions are resolved in uv.lock and installed with `uv sync --frozen`; exact versions are not copied into this document.

## Product copy · 2026-11-11

The user explicitly rejects development-status and future-enablement notices in the application. Hide unavailable controls; use action-specific configuration guidance only when it helps the user complete that action. Keep implementation limitations in engineering handoffs and release notes, not promotional UI cards. This does not authorize fabricated functionality or successful operations.

## Personal credential lifecycle

Host provisioning stays on bws. Personal credentials created/replaced through the application use the official Bitwarden SDK in-process: CLI create exposes values as positional arguments. A separate least-privilege machine account and application project are required. Provider connection rows contain secret references only; endpoint choice remains administrator-controlled.
