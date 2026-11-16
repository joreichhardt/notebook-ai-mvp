# Notebook AI

Private Notebooks, eigene Quellen und KI-Antworten mit Quellenbelegen.

## Stand

Login und Workspace-Verwaltung sind vorbereitet. Der öffentliche Betrieb ist noch nicht freigeschaltet. Als Nächstes folgt Upload → Textverarbeitung → Anzeige mit persistentem lokalem Speicher. Cloud-Speicher kommt später.

## Technik

- Backend: Python/FastAPI
- Frontend: React/TypeScript
- Datenbank und Anmeldung: selbst gehostetes Supabase/PostgreSQL
- Secrets: Bitwarden Secrets Manager
- Betrieb: lokaler Testserver mit Rootless Podman hinter Cloudflare

## Backend starten

```sh
uv sync --frozen
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Konfiguration: [.env.example](.env.example). Anmeldung benötigt konfigurierte Dienste und HTTPS.

[Dokumentation](docs/README.md)
