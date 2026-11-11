# Decisions

- 2026-11-11: Fresh repository, implementation starts with auth/data, frontend and deployment foundation; integration branch never deploys automatically.
- Supabase provides auth/database/storage. Bitwarden supplies secrets. No production fallback credentials or shared personal provider account.
- BFF owns sessions; no provider credentials in browser. Full Supabase and external provider verification remain release gates.
