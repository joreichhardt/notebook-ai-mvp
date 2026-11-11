# D · Frontend foundation

Branch: `agent/frontend`. Baseline: `0c75d3c89fe50bd576fe123ec831d59eaa073556`.

## Delivered

- React/TypeScript/Vite application with German responsive login and workspace interface.
- Typed same-origin BFF client; credentials remain in HttpOnly cookies, CSRF token kept only in memory.
- Login/session/logout, workspace selection and notebook create/rename/delete using actual APIs; deletion explicitly confirmed.
- Viewer write controls disabled; server remains authoritative.
- Abort stale read requests on workspace change/unmount. Provider error bodies never rendered, preventing upstream diagnostic/secret leaks.
- Upcoming registration/recovery/OAuth, sources/chat and provider setup explicitly unavailable; no seeded success or mock production data.
- Exact dependency pins and npm lockfile, no remote fonts/assets.

## Contracts and integration

Mount backend under `/api`; initial paths in contracts.md. Confirmed with auth agent: session includes `csrf_token`; mutations use `X-CSRF-Token`. POST login then GET session refreshes state. POST/PATCH notebook return Notebook directly; list envelope is `{items: [...]}`. DELETE may return 204. Auth Origin check must match browser origin.

Development: API on 127.0.0.1:8000; Vite proxies `/api`. Production: static `frontend/dist`. Node tested: 22.22.2; Vite requires Node 20.19+ or 22.12+. No changes needed to parent-owned Python config.

## Validation

- `npm --prefix frontend test`: 9 tests passed (API CSRF/credentials/path encoding, safe error messages, empty DELETE response, GET headers, create notebook, viewer restrictions, service failure, unavailable providers, login flow).
- `npm --prefix frontend run build`: TypeScript + production build passed.
- npm install audit: zero reported vulnerabilities at check time (not a full security assessment).
- Browser visual and real Supabase integration: parent integration step, not yet performed by D.

## Remaining scope

Invitation/registration/Google/recovery UI, data-driven documents/chat/graph and connection lifecycle need subsequent backend contracts. UI does not claim those flows work. Expired-session API errors currently prompt re-login in text; automatic session renewal is not implemented in the backend foundation.

Primary sources consulted: https://vite.dev/guide/ and https://react.dev/reference/react/useEffect .
