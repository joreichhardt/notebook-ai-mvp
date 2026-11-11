# Notebook UI

React/TypeScript frontend for the first foundation milestone. Requires Node 22.12+ (tested with 22.22.2).

```sh
cd frontend
npm ci
npm test
npm run build
npm run dev -- --port 5173
```

Vite forwards `/api` to `http://127.0.0.1:8000`. Production serves `dist/` from the same origin as the BFF. No browser-side provider credentials or Supabase privileged keys are configured. Auth cookie security and CSRF enforcement belong to the BFF; exact public origin must match the browser origin.

Implemented: session lookup, password login, logout, admission check, workspace selection, notebook list/create/rename/delete, recipient-bound invitation creation and acceptance, owner member management, and personal provider connect/test/model-select/disconnect. Capability discovery hides unavailable authentication methods. No mock API fallback; fixtures exist only in tests.

Source upload, chat, graph, registration and recovery require subsequent integration and are not exposed as working controls. Real end-to-end verification requires the integrated backend, configured Supabase and Bitwarden. Invitation fragments are removed immediately and held in memory through login; reload requires reopening the invite link.

Reference checks: [Vite requirements](https://vite.dev/guide/), [React effect cleanup](https://react.dev/reference/react/useEffect). Fetch effects cancel stale workspace/session requests.
