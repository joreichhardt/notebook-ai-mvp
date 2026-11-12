# Notebook auf kube1

Private Notebooks mit nachvollziehbaren Quellenantworten. Python/FastAPI, React/TypeScript und selbst gehostetes Supabase; Secrets aus Bitwarden Secrets Manager über `bws`.

Dokumentation: [Übersicht und Einstieg](docs/README.md).

## Entwicklungsstand

Implementiert sind Authentifizierung, Workspace-/Notebook-Verwaltung, Einladungsannahme für bestätigte Konten, Mitgliederrollen und persönliche Anbieter-Verbindungen mit Modellprüfung. Zusätzlich sind die Datenbasis für Dokumentversionen, Chunks und abgeschirmte Jobs (Migrationen 0003/0004), dauerhafte Upload-Intents mit geschützter Finalisierung/Cleanup und sicherer Statusprojektion (0005) sowie interne Streaming-/Embedding-Adapter mit persönlicher Ausführungsprüfung integriert. Diese Bausteine sind noch nicht als Upload-/Chat-Funktionen freigeschaltet. Dies ist noch kein produktionsfertiger NotebookLM-Klon. Upload, belegte Modellantworten, Graphretrieval und die Registrierung neuer Konten sind noch offen. Persönliche Anbieter-Verbindungen benötigen eine konfigurierte Bitwarden-Instanz und freigegebene Endpoints. Es gibt keine fingierten Antworten oder Produktions-Auth-Mocks.

Verbindliche Anforderungen: [agent.md](agent.md), [AGENTS.md](AGENTS.md), [Multi-Agent-Arbeit](multi-agent.md), [Produktspezifikation](docs/product-spec.md). Aktueller Stand: [Plan](docs/orchestration/plan.md) und [Integrationslog](docs/orchestration/integration-log.md).

## Backend lokal starten

Python 3.12+ und uv installieren, dann:

```sh
uv sync --frozen
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

`GET /api/health/live` bestätigt nur, dass der API-Prozess läuft. Es ist kein Nachweis für Datenbank, Supabase oder produktive Bereitschaft. Ohne konfigurierte Dienste bleiben davon abhängige Funktionen nicht verfügbar.

```sh
uv run pytest
uv run ruff check backend tests/security
```

Konfiguration: [.env.example](.env.example). Umgebungsvariablen explizit an den Prozess übergeben; diese Vorlage enthält keine Zugangsdaten. Auth-Cookies sind Secure; integrierte Login-Tests und lokale Nutzung benötigen einen passenden HTTPS-Ursprung. Keine Cookie-Sicherheitsabsenkung als Produktions-Fallback.

## Betrieb

Produktionsziel: `https://note.yokoma.de`, Docker Compose auf kube1. Bestehende Hostdienste bleiben unverändert. Der [lesende Infrastrukturcheck](docs/architecture/inventory.md) dokumentiert offene Voraussetzungen. Kein Git-Remote oder GitHub-Workflow-Lauf ist bisher eingerichtet bzw. nachgewiesen. Ein Release setzt die Security- und Abnahmekriterien der Produktspezifikation voraus.

## Architektur

[Verträge](docs/architecture/contracts.md) · [Entscheidungen](docs/architecture/decisions.md). Normale Nutzerabfragen laufen mit eingeschränkter Datenbankrolle und transaktionslokalem Nutzerkontext. Supabase-Identität allein gewährt keinen App-Zugang. Persönliche Modell-Credentials bleiben strikt nutzergebunden; die Datenbank speichert nur Referenzen.

## Container-Tests auf kube1

Docker-Builds und Compose-Teststacks laufen ausschließlich auf **kube1**, nicht auf dem Laptop. Anleitung inklusive isoliertem Testprojekt und SSH-Zugriff: [kube1-Testbetrieb](docs/operations/testing-kube1.md).

Der Foundation-Teststack enthält API und statisches Frontend, noch kein Supabase. Er prüft Start, Darstellung und sicheres Verhalten bei fehlender Konfiguration; echte Anmeldung benötigt Supabase, Zulassung und HTTPS. Kein Produktionsrollout. Frontend-Quellcode: `frontend/`.
