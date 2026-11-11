# Handoff · Notebook · 2026-11-11

## Einstieg

Aktueller Auftrag: Handoff erstellen; danach bei Fortsetzung das Produkt weiterbauen. Das zuvor gesendete „weiter“ wurde unterbrochen; seit Welle 2 wurde keine weitere Implementierungswelle begonnen.

1. Im Worktree `/home/jre/orca/workspaces/gemini-nb-clone/notebook-integration` arbeiten, Branch `integration/notebooklm`. Implementierungsstand: `0e6b0c8347f31729d49cffc854cb3ac23ec9fd7b`; dieser Handoff und die Pi-Regel folgen als Dokumentationsänderung.
2. `agent.md`, `AGENTS.md`, `multi-agent.md`, `docs/product-spec.md`, `docs/orchestration/plan.md`, `docs/architecture/contracts.md`, `docs/architecture/decisions.md` und `docs/orchestration/integration-log.md` lesen. Vor Änderungen Git-/Worktree-Status erneut prüfen.
3. **Alle künftig gestarteten Agents sind Pi**, auch durch Orca gestartete Agents: explizit `--agent pi`. Keine Codex-/Claude-Worker als Ersatz starten oder alte Sitzungen für neue Aufgaben wiederverwenden. Die Vorgabe betrifft die Agentensoftware; ein Modell wurde nicht festgelegt.
4. Nutzerwunsch zur UI erhalten: keine Entwicklungs-, Roadmap- oder Freischaltungshinweise wie „Die Wissensfunktionen werden schrittweise freigeschaltet.“ Unverfügbare Bedienelemente ausblenden; konkrete Fehler und hilfreiche Wiederholungsaktionen bleiben sichtbar.

## Arbeitsbereiche

Der ursprüngliche Chat-Worktree `../gemini-nb-clone` ist nicht der aktuelle Implementierungsstand. Seine ungetrackten Dateien unter `docs/diagrams/` erhalten. Auch `/home/jre/orca/projects/gemini-nb-clone` (main) nicht als Integrationsbereich verwenden. Kein Git-Remote konfiguriert; keine Veröffentlichung oder Produktionseinführung erfolgt.

Bestehende Worker-Worktrees liegen neben `notebook-integration`:

| Rolle | Worktree | Branch | letzter bekannter Stand |
| --- | --- | --- | --- |
| A Auth/Daten | notebook-auth-data | agent/auth-data | 2914ad1 |
| C Provider | notebook-providers | agent/providers | b752ad7 |
| D Frontend | notebook-frontend | agent/frontend | 3c5eb91 |
| E Betrieb | notebook-deploy | agent/deploy | 521a2da |
| F Review | notebook-review | agent/review | 58eea05 |

Vor Wiederverwendung Working Tree prüfen und Integrationsstand kontrolliert übernehmen. Nur der Hauptagent erstellt/integriert Worktrees und pflegt gemeinsame Verträge. A besitzt sämtliche ORM-Modelle und Migrationen, auch für Provider/RAG. B (Dokumente/RAG) ist noch nicht gestartet. Pro aktivem Agent eigener Branch und Worktree; Parallelität nach verfügbaren Plätzen begrenzen.

Orca-Skill vor Starts lesen. Zuletzt funktionierende CLI: `/home/jre/.config/orca/linux-orca-cli-shim/orca`. Für eine neue Pi-Sitzung in einem vorhandenen Worktree `agent launch --agent pi --worktree <geprüfter Selektor> --prompt <Auftrag> --json` verwenden; Selektor aus Orca ermitteln. Dieses Dokument startet selbst keinen Agenten.

## Implementiert und geprüft

- FastAPI/SQLAlchemy/Alembic, React/TypeScript, gepinnte Python-/npm-Abhängigkeiten.
- Supabase-Passwortlogin und serverseitige Identitätsprüfung; HttpOnly-Sitzung, Origin-/CSRF-Prüfung, App-Zulassung, Workspace-Rollen und Notebook-CRUD mit RLS.
- Einladungsannahme für bereits vorhandene verifizierte Konten, einmaliger Admin-Bootstrap, Mitgliederrollen und Schutz des letzten Owners. Migrationen 0001/0002.
- Persönliche Provider-Verbindungen: Bitwarden-Speicherung, konfigurierte Provider-Auswahl, Modellabfrage/-auswahl, Verbindungstest und Trennen mit wiederholbarer Secret-Bereinigung. Keine Klartext-Secrets in Responses oder DB.
- Frontend für diese Abläufe, ohne Entwicklungsankündigungen; fehlgeschlagene Capability-Abfrage erneut ausführbar.
- bws-Host-Provisionierung, non-root/read-only Container, lokale Compose-Grundlage, CI mit PostgreSQL-/RLS-Tests, Trivy-Gates und SBOM-Konfiguration.

Detailübergaben: `docs/handoffs/A2.md`, `C.md`, `D2.md`, `E.md`. Unabhängiger Review `docs/review/wave2.md`: beide Findings geschlossen. Bitwarden-Löschung prüft auch Einzelresultate; HTTP-/SDK-Gesamterfolg allein genügt nicht.

## Nachweise und lokale Laufzeit

Letzte vollständige Implementierungsprüfung: **50 Backendtests mit echtem isoliertem PostgreSQL, 17 Frontendtests, 9 Provisionierungstests**, Ruff und TypeScript/Vite-Build erfolgreich. Diese Ergebnisse stammen aus Welle 2; für den Dokumentationshandoff wurden die Tests nicht erneut ausgeführt. Eine upstream Starlette/httpx-Deprecation-Warnung bleibt.

Beim Handoff erneut geprüft: Compose-Projekt `notebook-foundation-verify`, API und Frontend healthy. Vorschau: `http://127.0.0.1:18080`. Supabase/Bitwarden sind dort nicht live konfiguriert; Login deshalb nicht verfügbar. Keine authentifizierten Browser-E2E oder Live-Provider-Erfolge behaupten.

Test-PostgreSQL16 unter `/tmp/notebook-auth-pg`, Port 55439, wurde nach den Tests gestoppt; Datenverzeichnis für Wiederaufnahme erhalten. System-PostgreSQL auf 5432 nicht verändern. Bei erneuten DB-Tests zuerst Status prüfen, isolierte Instanz starten und frische Testdatenbank migrieren. Die zuletzt verwendete Wegwerf-Datenbank `notebook_wave2_verify` wurde gelöscht.

Prüfkommandos aus dem Integrations-Worktree, nach Bereitstellung der Testdatenbank:

```sh
NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55439/postgres uv run pytest -q
uv run ruff check backend
npm test --prefix frontend
npm run build --prefix frontend
python -m unittest discover -s deploy/tests
docker compose -p notebook-foundation-verify ps
```

Die CI und `docs/operations/foundation.md` sind maßgeblich für Migration/Testsetup. Browserprüfung über Orca; zuletzt Seite `7d4d5a85-e4d0-4b44-8841-0f16f5d780a3`. Flüchtige IDs/Pfade vor Wiederverwendung prüfen.

## Nächster Implementierungsschritt

Ziel bleibt ein echter vertikaler Durchstich: Anmeldung → eigenes Notebook → persönliche Modellverbindung → Dokument → Antwort mit Quellen. Vor parallelem Start einen kleinen Vertrag für Upload, Dokumentversionen, Jobs, Indexprofil und Streaming vervollständigen.

1. A und B koordinieren Dokument-/Job-/Chunk-Schema, RLS und Storage-Policies; A liefert Migrationen, B Upload, isoliertes Parsing und Jobverarbeitung. Jobs tragen Nutzer, Workspace, Dokumentversion und Connection-ID, keine Secrets; Berechtigungen zur Ausführungszeit erneut prüfen.
2. C ergänzt echte Generation/Streaming und Embeddings; vorhandene Verbindungstests prüfen bislang nur Modellabfrage. D integriert funktionierende Upload-/Quellen-/Chat-Abläufe erst gegen den vereinbarten Vertrag.
3. E ergänzt die tatsächliche lokale Supabase-Integration und Betriebsnachweise. Danach unabhängigen Review des integrierten Upload-bis-Antwort-Flusses mit fremden Workspace-/Credential-Zugriffen durchführen.

Weitere offene Produktarbeit: neue Konten über Einladung, Google OAuth, Recovery/Session-Refresh, Einladungsliste/Widerruf, Audit/Rate-Limits, persönliches Standardmodell, Key-Ersetzung, belegter Graph. Nicht als bereits implementiert darstellen.

## Secrets und Release-Grenzen

Bitwarden Secrets Manager ist verbindlich, Supabase Vault entfällt. Auf kube1 Host-Provisionierung über gepinnte bws CLI. Dynamische persönliche Keys über offizielles Bitwarden-SDK im Prozess: CLI-Erstellung würde Werte in Argumenten exponieren. Getrennte minimal berechtigte Machine Accounts für Lesen bei Deployment und persönliche Secret-Verwaltung. Konfiguration/Variablennamen stehen in `.env.example` und den Verträgen; keine Werte in Chat, Logs, Git oder Worker-Prompts.

Vor Infrastrukturarbeit `~/dev/homeserver/README.md` und `~/dev/homeserver/secrets/README.md` lesen; relevante Runbooks gezielt nachladen. Bestehende kube1-Dienste erhalten. Zuletzt war Docker auf kube1 nicht rootless; non-root Container sind kein Rootless-Nachweis.

Offene Release-Gates: Live-Supabase/Bitwarden; Provider-DNS-Prüfung und Verbindungsaufbau gegen DNS-Wechsel absichern; SDK-Deadlines; verwaiste Secrets bei DB-/Vault-Teilfehlern bereinigen; Rootless/Parser-Isolation, tatsächliche Scans, Backup/Restore, Release/Rollback und öffentliche Smoke-Tests. GitHub-Remote/Actions, anwendungseigene Bitwarden-Zugänge, SMTP/Google-Konfiguration und Routing zu `note.yokoma.de` sind noch einzurichten bzw. zu bestätigen. Es gab keinen Produktionsrollout.

## Excalidraw-Kontext

Das Wiring-Diagramm liegt auf `draw.yokoma.de` auf kube1 und zeigt die geplante Architektur. Die vorherige Szene wurde gesichert und als eigene `.excalidrawlib` exportiert; der Import in die Browser-Library ist noch offen, da der verfügbare MCP keine Library-Setter-Methode hatte. Backups im ursprünglichen Worktree erhalten. Lokaler MCP-Zugang über SSH wurde verwendet; Hilfsskript zuletzt `/tmp/draw_local.py`. Vor Nutzung Verfügbarkeit und Inhalt prüfen. Ein Diagramm ist kein Nachweis eines laufenden Stacks.
