# Fortsetzung · Notebook

## Einstieg

1. In `/home/jre/orca/workspaces/gemini-nb-clone/notebook-integration`, Branch `integration/notebooklm`, arbeiten; zuerst Git-/Worktree-Status prüfen und Nutzeränderungen erhalten. Letzter integrierter Implementierungs-/Prüfstand: `f77255d`. Nachfolgende Dokumentationsänderungen sind kein neuer Implementierungsnachweis.
2. [Dokumentationsindex](../README.md), `agent.md`, `AGENTS.md`, `multi-agent.md`, [Spezifikation](../product-spec.md), [Plan](../orchestration/plan.md), [Verträge](../architecture/contracts.md) und [Entscheidungen](../architecture/decisions.md) lesen. Aktueller Nutzerwunsch: Markdowns fortlaufend pflegen, kompakte notwendige Dokumentation unter `docs/`, diese Regel in AGENTS.md verbindlich erhalten.
3. Nach Kompaktierung/Zusatzaufträgen den freigegebenen Hauptauftrag selbstständig fortsetzen; vorhandene B3-/A5-Worker nicht durch neue Worktrees oder doppelte Implementierung ersetzen. Main prüft/integriert und bearbeitet unabhängige Arbeit; bei reinen Worker-Abhängigkeiten Orca-Wakeup abwarten, nicht pollen.
4. Nur Pi-Agents starten, auch über Orca explizit `--agent pi`. Nutzer hat kein bestimmtes Modell gewählt. UI ohne Entwicklungs-/Roadmap-/Freischaltungshinweise; nicht verfügbare Bedienelemente ausblenden, konkrete Fehler/Wiederholung erhalten.

## Integriert und tatsächlich geprüft

- Auth/BFF, Zulassung, Notebook-/Workspace-Verwaltung, Einladungsannahme bestätigter Konten, Mitgliederrollen sowie persönliche Bitwarden-Providerverbindungen aus Welle 2.
- A3/C3: Dokument-/Versions-/Chunk-/Job-/Indexprofil-Datenbasis, RLS, gefencete Lease-/Publikationshelfer sowie interne Streaming-/Embedding-Adapter und persönliche ExecutionService-Grenze. Noch keine fertige Upload-/Chat-Funktion.
- A4/F3a: Profilidentitätsbefund F3a-01 durch Migration 0004 korrigiert und unabhängig auf `f46054b` geschlossen. Reviewabschluss `edcaf398` integriert; normale Testsuche erfasst Sicherheitsregressionen.
- Main prüfte auf der kombinierten 0004-Basis frische Migrationen und **176 bestandene Tests**, Ruff für Backend/Security und vier aktive LSP-Pfade ohne Fehler. Eine vorhandene Starlette/httpx-Warnung bleibt. Frühere 17 Frontendtests/Build und 9 Provisionertests bestanden; für den backend-only Reviewabschluss nicht erneut ausgeführt.
- Test-DB `notebook_wave3_review_verify` gelöscht, isolierter PostgreSQL16 unter `/tmp/notebook-auth-pg`, Port 55439, danach gestoppt. Vor Wiederaufnahme Status/Port prüfen, Socket-Verzeichnis explizit setzen und neue Wegwerf-DB verwenden. System-DB 5432 unverändert lassen. Details/Befehle im [Integrationslog](../orchestration/integration-log.md).

## Laufende Aufträge · vor Änderungen Rückmeldungen prüfen

Orca-Run `run_a6efa1e8f690`, CLI zuletzt `/home/jre/.config/orca/linux-orca-cli-shim/orca`. Vor CLI-Nutzung Skill und versionspassenden Guide laden. Nachrichten über Orchestrierungs-Dispatch, nicht per Terminal steuern; nicht pollen, Orca weckt bei Antworten.

| Auftrag | Worktree / Branch | Dispatch | Stand |
| --- | --- | --- | --- |
| B3 | `../notebook-upload-v3` / `notebook-upload-v3` | `ctx_67e121724725` | Strict TXT/MD, begrenzter privater Storage-Adapter, trusted actor-bound run_one. Checkpoint `ff84356` umfasst `0df6fcd`, `3fb6930`, `8343ef4`; Worker meldet 30 leichte Tests, weitere historische DB-Läufe separat im B3-Handoff. Main hat vier Kernmodule gelesen, kein bestätigter neuer Befund. **Noch nicht integriert**, finale Reporting-race-DB-Regression auf kube1 läuft/steht aus. Route fehlt absichtlich, wartet auf A5. |
| A5 | `../notebook-upload-data-v3` / `notebook-upload-data-v3` | `ctx_0212865c10e4` | Checkpoint `cddae38fa7a81e072628fbd359442457a42c3cbf` enthält Migration0005/API/Tests/Handoff; noch nicht final review-ready. Remote zunächst228passed/1Concurrency-Testerwartung, Anpassung/finaler Lauf offen. Cached-finalize-Race mit Zwei-Session-Test laut Worker behoben. Worker bestätigte auf Remote-Snapshot `96f194a` vier RED-Fälle für cross-actor Status-UPDATE und direkte INSERT-Zustände. Invoker-only Guard in Arbeit, keine neuen Rechte. Zusätzlich Main-Nachricht `msg_d1d3daf5ab54`: eigener/fremder Zweitjob auf uploadgebundene Version darf Projektion nicht über AFTER-Trigger fälschen; kanonische Jobbindung im Commit-Guard plus Negativtests erforderlich. |

B3 reserviert Testport 55443, A5 55444, jeweils eigene Umgebung/DB; alte Worker-Umgebungen und untracked `.pi/` erhalten. Nur Main integriert und pflegt gemeinsame Dokumente; A besitzt alle ORM-/Migrationsänderungen. Bereichsberichte in den jeweiligen Worktrees: `docs/handoffs/B3.md` und `A5.md`, sobald angelegt/aktualisiert. Meldungen/aktuelle SHAs erneut prüfen, nicht aus dieser Momentaufnahme ableiten.

## Nächste Schritte und Freischaltungsgrenzen

- **Unmittelbar weiter:** A5 muss Invoker-Statusguard, kanonischen Zweitjob-Schutz und endgültige Remote-Regression belegen; B3 führt eigene Reporting-race-Regression remote aus und darf Main-Dependencycommit `bac1ad6` für scoped Multipart-/Spooltests cherry-picken. Noch kein A5-Integrations-/Routeauftrag. Danach Kandidaten/Tests prüfen, A5 vor B3 integrieren und enge privilegierte Seams unabhängig reviewen lassen.
- Main hat `python-multipart==0.0.33` in `bac1ad6` mit minimalem Lockdiff gepinnt, offizielle PyPI-Metadaten verifiziert und API-Image daraus auf kube1 gebaut. Echter Starlette1.10.0/FastAPI0.162.4 Multipart-Parse plus Spoolclose grün; kein Raw-Limit-/Abbruch-/HTTP-Uploadnachweis. Main-Testprojekt danach beendet.
- A5 liefert durable Upload-Intents, geschützte Zustände/Receipts, atomare Finalisierung mit Commit-Guard, enge I/O-/Verifikationsrollen und member-sichtbaren aktuellen Versionsstatus ohne raw-job-RLS-Ausweitung. B3 setzt den [genehmigten Vertrag](../architecture/contracts.md#b3a5-upload-lifecycle-approved-design-not-integrated) um. Exakte APIs aus A5-Code/Handoff verifizieren.
- B3-I/O: actor reserve/commit → I/O claim/commit → frische Actor-/Intent-/Lease-Prüfung → private bucket/non-upsert exact-key write, 30s Gesamtdeadline → begrenzter Readback/hash/bytes → gefencetes Receipt → autorisierte atomare Finalisierung/commit vor 202. Keine offenen DB-Transaktionen über Netzwerk-I/O.
- Ambige/abgebrochene Writes bleiben dauerhaftes Reconciliation-Thema. Timeout/Leaseende beweist keine Remote-Quieszenz; alte Keys nicht neu vergeben, Cleanup-Tombstones für spätere Orphan-/Abwesenheitsprüfungen behalten. Echte Storage-Policies und Cleanup-Runtime noch nachzuweisen.
- Globalquoten **offen**: outstanding 10 Actor/Notebook, 50 Notebook; aktiv 2 Actor, 4 Notebook. Actor-private Counts genügen nicht. Kein spontanes globales Job-SELECT/BYPASSRLS/Counter-Recht. Separater Entwurf/Review vor Freigabe.
- Zunächst nur neue TXT-/Markdown-Dokumente; keine PDF-/Embedding-/Replacement-/Delete-/Retry-HTTP-Routen. Upload bleibt fail-closed/unregistriert und Dokument-/Chat-Capabilities aus, bis Lifecycle/Storage/Actor-Dispatch/Quoten geprüft sind. PDF braucht isolierten Parser. Danach UI/Chat, E3-Betrieb und unabhängige Full-Flow-Abnahme koordinieren.
- Weitere Produktarbeit: Registrierung neuer Konten/Google OAuth/Recovery, Session-Refresh, Einladungsliste/-widerruf, Audit/Rate-Limits, persönliche Standards/Key-Ersetzung, Vektor-/Graphretrieval. Nicht als fertig darstellen.

## Betrieb und erhaltene Bestände

**Neue Nutzervorgabe:** Docker-Testmaschinen/-builds ausschließlich kube1, Laptop zu schwach. SSH/Docker/Compose verifiziert und private Bereiche `/home/jre/notebook-tests/{main,b3,a5}` angelegt. [Testbetriebs-Runbook](../operations/testing-kube1.md) ist maßgeblich für Projekt-/Portzuordnung und Snapshot-Transfer. Foundation-Snapshot `c1214957df79` remote gebaut und per HTTP/Runtime-Metadaten geprüft, eigenes Main-Testprojekt anschließend beendet; [Prüfprotokoll](../orchestration/integration-log.md) beachten. Worker zum geordneten Stoppen eigener lokaler nativer DBs nach Abschluss laufender Tests angewiesen; Daten/Logs erhalten und Stilllegung im jeweiligen Handoff bestätigen lassen. Auf dem Laptop waren beide Notebook-Foundation-Container bereits gestoppt; fremde laufende Container erhalten.

Kein Produktionsrollout, Live-Supabase-/Bitwarden-/Provider-Erfolg oder GitHub-Workflow-Lauf nachgewiesen. Kein Remote eingerichtet. Frühere lokale Vorschau `http://127.0.0.1:18080`, Compose `notebook-foundation-verify`, war Welle-2-Stand ohne Live-Auth-Konfiguration; aktuell nicht erneut geprüft oder neu gebaut. Non-root ist kein Rootless-Nachweis.

Bitwarden bleibt verbindlich; persönliche Secrets nur serverseitig/nutzergebunden, Host-Secrets über gepinnte bws CLI. Vor Infrastrukturarbeit die Homeserver-READMEs gemäß AGENTS.md lesen. Fehlende produktive Konfiguration/Release-Gates stehen im Plan/Integrationslog; keine geheimen Werte im Chat anfordern.

Ursprünglichen Chat-Worktree `../gemini-nb-clone` samt untracked `docs/diagrams/` erhalten; auch `/home/jre/orca/projects/gemini-nb-clone` (main) nicht als Integrationsbereich verwenden. Historische Worker-/Reviewberichte bleiben in Git und unter `handoffs/`/`review/`; sie sind standbezogen, keine aktuellen Arbeitsanweisungen. Excalidraw-Szene auf `draw.yokoma.de` ist Architekturplanung, kein Laufzeitnachweis. Diagrammbackups im ursprünglichen Worktree erhalten; Library-Import war offen. Flüchtige Browser-/MCP-IDs und `/tmp`-Hilfsskripte vor Wiederverwendung prüfen.
