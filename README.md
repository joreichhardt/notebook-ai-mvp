# Notebook auf kube1

Private Notebooks mit nachvollziehbaren Quellenantworten. Python/FastAPI, React/TypeScript und selbst gehostetes Supabase; Secrets aus Bitwarden Secrets Manager über `bws`.

Dokumentation: [Übersicht und Einstieg](docs/README.md).

## Entwicklungsstand

Implementiert sind Authentifizierung, Workspace-/Notebook-Verwaltung, Einladungsannahme für bestätigte Konten, Mitgliederrollen und persönliche Anbieter-Verbindungen mit Modellprüfung. Zusätzlich sind die Datenbasis für Dokumentversionen, Chunks und abgeschirmte Jobs (Migrationen 0003/0004), dauerhafte Upload-Intents mit geschützter Finalisierung/Cleanup und sicherer Statusprojektion (0005) sowie interne Streaming-/Embedding-Adapter mit persönlicher Ausführungsprüfung integriert. Auch der begrenzte TXT-/Markdown-Service mit privatem Storage-Adapter, sicheren Status-DTOs und gefencetem Worker ist inzwischen integriert und unter synthetischen Storagebedingungen geprüft. Diese Bausteine sind noch nicht als HTTP-/UI-Upload oder Chat freigeschaltet. Dies ist noch kein produktionsfertiger NotebookLM-Klon. HTTP-/UI-Upload, belegte Modellantworten, Graphretrieval und die Registrierung neuer Konten sind noch offen; die Quotenmigration0006 ist integriert, während produktive Storage-/Dispatch- und Release-Gates offen bleiben. Persönliche Anbieter-Verbindungen benötigen eine konfigurierte Bitwarden-Instanz und freigegebene Endpoints. Es gibt keine fingierten Antworten oder Produktions-Auth-Mocks.

Die lesende Quellenstatus-Oberfläche ist integriert und anhand synthetischer Komponenten-/Transporttests geprüft (33 Tests und TypeScript auf kube1). Sie bleibt hinter der unveränderten Dokument-Capability; echte API-/Storage-/Upload-/Chat-Flows sind damit nicht freigeschaltet. Details: [D3a-Handoff](docs/handoffs/D3a.md).

Die Modellprüfung revalidiert persönliche Verbindungen nach dem Secret-Abruf und während der Discovery; Metadaten werden in einer frischen Kurztransaktion publiziert. Die begrenzte F4-Abnahme auf kube1 ist bestätigt: 30 echte PostgreSQL-/ASGI-HTTP-Parallelitätsfälle und 135 fokussierte Regressionstests ohne Fehler oder Skips; externe Auth-/Secret-/Providerantworten bleiben synthetisch. Kein Produktions-/Liveprovider-Nachweis. Das isolierte A5-Diagnosepaket ist ebenfalls integriert (63 lokale Tests, 1 DB-Skip), ohne Phase-A-/Storagefreigabe. [Prüfstand und nächste Gates](docs/orchestration/plan.md).

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
