# Handoff · Notebook · Wave 3 / Pi-Konfigurationsreload

## Aktueller Wiederaufnahmestand (ersetzt den historischen Einstieg unten)

Nutzerauftrag: sofort Handoff sichern, Pi wegen Konfigurationsänderungen reloaden, danach weiterarbeiten. Keine weitere Integration vor Reload. Ein Reload ist noch nicht nachgewiesen; in der Pi-Oberfläche `/reload` ausführen. Anschließend diesen Abschnitt und die referenzierten Worker-Handoffs lesen, Git-Status erneut prüfen.

- Integrations-Worktree `/home/jre/orca/workspaces/gemini-nb-clone/notebook-integration`, Branch `integration/notebooklm`, letzter geprüfter sauberer HEAD `1bd22bd01182e8bc99e9116ef6173ca5746e46e5`. Dieser Commit enthält Wave-3-Verträge und Plan; Worker-Code noch NICHT integriert. Dieser Handoff folgt als eigene Dokumentationsänderung.
- A3 fertig in `../notebook-data-v3`, Branch `notebook-data-v3`: `2be22c3`, `43e4356`, Handoff-HEAD `eae387ef523d7fdb7a705f7c0c99e0fcc5a449f6`. Bericht `docs/handoffs/A3.md` dort. Dokument-/Versions-/Chunk-/Indexprofil-/Job-ORM, Migration 0003, RLS und invoker-basierte Lease-/Publication-Helfer. Worker meldet 68 Auth-/91 Backendtests mit echtem isoliertem PostgreSQL, Ruff und geänderte LSP-Pfade bestanden. Diese Nachweise stammen aus dem Worker-Stand, NICHT aus Integration. PostgreSQL auf 55440 wurde laut Worker gestoppt; Details/temporärer Pfad im Bericht. 5432/55439 unberührt lassen. Untracked `.pi/` erhalten.
- C3 Zwischenstand in `../notebook-provider-v3`, Branch `notebook-provider-v3`: Implementierung `3ef0902`, Pause-Handoff `4960609656288d80dca26b3ade2204b19b1839d3`. Bericht `docs/handoffs/C3.md` dort. Native Provider-Streams/Embeddings, numerischer kontrollierter Dial mit ursprünglichem TLS-Hostname und persönliche ExecutionService-Grenze. Worker meldete 72 Provider-Fixturetests und Ruff bestanden; keine Live-Provider-/Bitwarden-/DB-Abnahme. Abschlussreview/LSP/Edge Cases noch offen. Zuletzt war `backend/tests/providers/test_providers.py` uncommittet geändert; zusätzlich untracked `.pi/`: erhalten und prüfen.
- Nach erster Nutzerpause wurde derselbe C3-Auftrag ausdrücklich wieder aufgenommen. Orca-Follow-up Dispatch `ctx_563d2cbc5e10`, Task `task_8c4bc25551ce`, Run `run_a6efa1e8f690`; bisher kein neuer Abschluss empfangen. Für den aktuellen Reload wurde mit Nachricht `msg_c70fb32dd233` geordnetes Anhalten und Sichern verlangt. **Anforderung ist kein Nachweis, dass C3 bereits angehalten hat.** Vor Worktree-Änderungen Rückmeldung prüfen.
- Frühere Dispatches: A3 `ctx_9dd83404d0a4` erfolgreich; C3 `ctx_b217208e33ab` pausierter unvollständiger Checkpoint (outcome failed wegen Pause, kein vollständiger Abnahmeerfolg). Aktuelle CLI `/home/jre/.config/orca/linux-orca-cli-shim/orca`. Vor weiterer Nutzung Skills/versionspassenden Guide lesen. Nicht pollen; Orca weckt bei Nachrichten. Supervised Worker über Dispatch-Messaging, nicht Terminal-Eingaben steuern.
- Hauptagent muss vor Integration noch explizit `httpcore>=1,<2` in Root-Abhängigkeiten/Lock aufnehmen und `app.auth.security.fail(...) -> typing.NoReturn` annotieren. C3 hat lokale Connection-Narrowing-Korrektur übernommen; allgemeine Baseline-LSP-Fehler nicht als A3-Regression darstellen. Diese zentralen Änderungen sind noch NICHT gemacht.
- Nächste Schritte: C3-Abschluss/Pause prüfen; A3-/C3-Diffs und Zuständigkeiten prüfen, zentrale Ergänzungen durchführen, kontrolliert im Integration-Worktree mergen, reale migrations-/RLS-/ExecutionService-DB-Nachweise und Gesamtprüfung ausführen. Danach B3 für Upload/private Supabase-Storage/isoliertes Parsing/Worker/scoped Retrieval/Chat starten; D3/E3/F3 abhängig koordinieren. Kein kompletter Upload-bis-Antwort-Fluss vorhanden.
- Offene Sicherheitsgrenzen A3: vertrauenswürdige Worker-/Actor-Auswahl, atomare Komposition `lock_job_for_publish` + Chunks + `finish_job`, Storage-Cleanup/Policies, Parser-Gesamtquoten und echte Profil-/Modellprüfung. Keine generischen BYPASSRLS-Worker hinzufügen. C3-Abbruch-/Revocation-/TLS-Verhalten vor Freigabe abschließend prüfen.
- Taskliste: #1 Bestandsprüfung abgeschlossen; #2 Verträge abgeschlossen; #3 Worker-Implementierung in_progress (C3 unvollständig); #4 Integration/Prüfung pending. Nicht als vollständig erledigt markieren.
- Nur Pi-Agents starten (`--agent pi`), keine Modellwahl durch diese Vorgabe. UI ohne Entwicklungs-/Roadmap-Hinweise erhalten. Kein Deployment, keine produktiven Zugänge, keine Veröffentlichung erfolgt. Ältere Runtime-/Testnachweise unten sind historisch, nicht erneut geprüft.

## Historischer Einstieg (Wave 2, nicht aktueller Arbeitsstand)

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
