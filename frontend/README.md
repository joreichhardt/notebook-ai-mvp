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

Implemented: session lookup, password login, logout, workspace selection, notebook list/create/rename/delete, viewer write restrictions. No mock API fallback. Test fixtures exist only in tests.

Not yet implemented: registration/invitation acceptance, Google login/recovery, source upload, chat, graph and actual provider connections. These are explicitly marked unavailable in the UI. Real end-to-end verification requires the integrated backend and configured Supabase.

Reference checks: [Vite requirements](https://vite.dev/guide/), [React effect cleanup](https://react.dev/reference/react/useEffect). Fetch effects cancel stale workspace/session requests.
