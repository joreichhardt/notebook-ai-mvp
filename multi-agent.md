Multi-Agent-Orchestrierung mit eigenen Git-Worktrees

Auftrag und Geltung

Diese Datei ergänzt agent.md im Repository-Root. agent.md enthält die verbindlichen Produkt-, Architektur-, Sicherheits- und Deployment-Anforderungen. Lies beide Dateien vollständig vor Arbeitsbeginn. Beachte außerdem tatsächlich geltende Repository-Anweisungen wie AGENTS.md; Dateinamen sind unter Linux case-sensitive. Gehe nicht davon aus, dass ein Tool agent.md oder diese Datei automatisch lädt: Übergib beide ausdrücklich an jeden Agenten.

Du bist der Hauptagent und Orchestrator. Teile die Umsetzung auf spezialisierte Agenten mit jeweils eigenem Branch und eigenem Git-Worktree auf. Integriere ihre Ergebnisse bis zum getesteten Gesamtprodukt. Du entscheidest über Reihenfolge, Schnittstellen, Konflikte und Integration. Delegation entbindet dich nicht von der Gesamtverantwortung.

Diese Datei erteilt ausdrücklich die Erlaubnis zur Delegation an Unteragenten für die Implementierung. Sie verlangt keine bestimmte Orca-Version oder ein bestimmtes Orchestrierungsprodukt. Verwende ausschließlich tatsächlich verfügbare Agenten-, Messaging- und Worktree-Funktionen. Erfinde keine CLI-Kommandos oder parallel laufenden Agenten. Fehlt echte Parallelisierung, bearbeite dieselben Arbeitspakete sequenziell und benenne das transparent.

Unveränderliche Leitplanken





Python/FastAPI, lokale Supabase-Docker-Instanz, pgvector plus belegter Wissensgraph, React/TypeScript.



Container auf kube1, Deployment über GitHub Actions, Cloudflare Tunnel und https://note.yokoma.de.



Einladungen, persönliche und geteilte Workspaces, strikt getrennte Rollen und private Chats.



Jeder Nutzer verbindet seine eigenen Modelle. Keine geteilten persönlichen Credentials oder Abokontingente. API- und offiziell unterstützte Subscription-Zugänge können parallel bestehen.



Non-root-Images, Rootless-Docker-Ziel, minimale Runtime, Trivy-Gates und isoliertes Parsing gemäß agent.md.



Keine Änderungen an fachlichen Anforderungen durch einen Teilagenten. Unklarheiten zuerst als konkrete Entscheidung an den Hauptagenten melden.



Eine fehlende Provider-Freigabe darf keine Attrappe oder unsichere Umgehung erzeugen. Unabhängige Arbeit fortsetzen und Blocker dokumentieren.

Phase 0: Bestand und gemeinsamer Ausgangspunkt





Hauptagent prüft Repository, Git-Status, Branches, Worktrees, vorhandene Architektur und autorisierte Infrastrukturzugänge. Nutzeränderungen erhalten; kein pauschales Reset, Clean, Stash oder Überschreiben.



Uncommittete, für das Projekt notwendige Dateien müssen vor der Verteilung bewusst gesichert und in den gemeinsamen Ausgangspunkt übernommen werden. Nur relevante Dateien gezielt stagen, keine Secrets. Keine fremden Änderungen ungefragt committen. Worktrees teilen uncommittete Dateien nicht automatisch.



Fehlt ein Repository, initialisiere eines im vorgesehenen Projektverzeichnis. Remote und Veröffentlichungsstatus nicht erfinden.



Erstelle einen Integration-Branch integration/notebooklm in einem eigenen Worktree. Produktions-Branch nicht als laufenden Integrationsbereich verwenden, damit einzelne Teilstände kein Deployment auslösen.



Lege ein minimales startbares Projektgerüst, Testkonfiguration und eine versionierte Schnittstellenbasis an. Halte diese Phase klein; keine vollständige Vorabimplementierung.



Halte die bestätigte Baseline-Commit-SHA fest. Erst danach starten Teilagenten von dieser SHA. Bei vorhandenem Projekt Struktur übernehmen statt alle Verzeichnisse umzubenennen.

Der Hauptagent pflegt:





docs/orchestration/plan.md: Aufgaben, Zuständigkeiten, Abhängigkeiten, Status, Branch, Worktree, Baseline, Ergebnis-SHA.



docs/architecture/contracts.md: API, Auth-Kontext, Rollen, Fehlerformat, Jobs, Provider-Schnittstelle, Streaming-Ereignisse, Quellenbelege und Indexprofile.



docs/architecture/decisions.md: kurze Architekturentscheidungen und bestätigte Abweichungen.



docs/orchestration/integration-log.md: integrierte SHAs, Prüfungen, Konflikte und verbleibende Blocker.

Nur der Hauptagent schreibt diese gemeinsamen Dateien. Agenten melden Änderungen über Nachrichten oder ihren eigenen Übergabebericht.

Rollen und Schreibbereiche

Pfadnamen sind Vorschläge. Der Hauptagent ordnet sie einmalig auf die echte Projektstruktur ab und dokumentiert eindeutige Eigentümer vor dem Parallelstart.







Rolle



Aufgabe



Primärer Schreibbereich





Hauptagent



Architektur, Verträge, gemeinsame Konfiguration, Integration



Root-Konfiguration, zentrale App-Verdrahtung, gemeinsame Verträge, zentrale Dokumentation





A: Auth und Daten



Auth, Einladungen, Workspace-/Notebook-Grundfunktionen, Rollen, ORM, RLS, Storage-Policies, alle Migrationen



backend/app/auth/, backend/app/workspaces/, backend/app/notebooks/, backend/app/db/, backend/migrations/, supabase/migrations/





B: Dokumente und RAG



Uploads, Parser, Jobs, Chunks, Retrieval, Graph, Chat und Belege



backend/app/documents/, backend/app/ingestion/, backend/app/retrieval/, backend/app/chat/, backend/app/workers/, parser/





C: Persönliche KI-Zugänge



Provider-Adapter, Verbindungstests, Secret-Lifecycle, persönliche OAuth-/API-Verbindungen, Modellfähigkeiten



backend/app/providers/, backend/app/connections/





D: Frontend



Vollständige Bedienung einschließlich Auth, Meine KI, Workspace, Chat, Quellen, Demo-Admin



frontend/





E: Betrieb und Security



Dockerfiles, Compose, Reverse-Proxy, GitHub Actions, Trivy, Deployment, Backup und Betriebsdokumentation



deploy/, docker/, .github/workflows/, docs/operations/, SECURITY.md





F: Unabhängige Abnahme



Bereichsübergreifende Tests und Sicherheitsreview des integrierten Stands



tests/e2e/, tests/security/, docs/review/

Jeder implementierende Agent schreibt passende Tests in einem separat zugewiesenen Bereich, beispielsweise backend/tests/auth/, backend/tests/retrieval/ oder backend/tests/providers/. Frontend-Tests gehören Agent D. Agent F ersetzt diese Verantwortung nicht.

Gemeinsame Dateien: pyproject.toml, Python-Lockfile, zentrale App-Registrierung, .env.example, Root-Compose und zentrale Verträge haben jeweils genau einen Eigentümer. Standard: Hauptagent, außer Compose/Dockerfiles bei E. Frontend-Paketdateien und Lockfile gehören D. Andere Agenten melden ihre benötigten Dependencies, Variablen und Router, statt dieselben Dateien parallel zu verändern. E besitzt sämtliche Dockerfiles, auch für den Parser; B liefert Laufzeitanforderungen.

Datenbank: A besitzt alle ORM-Modelle, DDL und Migrationen, auch für RAG und Provider. B/C reichen konkrete Tabellen-, Index- und Query-Anforderungen bei A ein. Keine unabhängigen Alembic-Heads oder voneinander abweichenden Modelldefinitionen. Ausgerollte Migrationen nicht umschreiben.

Worktree-Regeln





Ein aktiver Agent = ein eigener Branch = ein eigener Worktree. Niemals zwei Agenten im selben Arbeitsverzeichnis arbeiten lassen.



Worktrees in einem benachbarten Verzeichnis, beispielsweise ../notebooklm-worktrees/, ablegen. Nicht innerhalb eines anderen Worktrees verschachteln.



Branches beispielsweise agent/auth-data, agent/rag, agent/providers, agent/frontend, agent/deploy, agent/review. Bei Wiederverwendung Bestand prüfen; nicht bestehende Branches erzwingen oder löschen.



Nur der Hauptagent erstellt, registriert, integriert und entfernt Worktrees. Teilagenten bearbeiten ausschließlich ihren zugewiesenen Worktree und Branch. Vor Änderungen Working Directory und Branch prüfen.



Beispiel nach geprüfter Pfad-/Branchwahl und mit einer tatsächlich ermittelten Baseline-SHA:

git worktree list
git worktree add -b agent/auth-data ../notebooklm-worktrees/auth-data BASELINE_SHA

BASELINE_SHA ist ein zu ersetzender Platzhalter. Bei existierendem Branch den passenden bestehenden Worktree verwenden oder den Konflikt klären. Kein --force als Routine.





Separate .venv, Frontend-Abhängigkeiten und Testausgaben pro Worktree. Keine Symlinks auf schreibbare Umgebungen anderer Agenten.



Worktrees sind keine Sicherheits-Sandbox: Git-Metadaten und Hostressourcen sind gemeinsam. Produktions-Secrets nicht in Worker-Kontexte oder Dateien kopieren. Tatsächliche Isolation durch Laufzeit-/Berechtigungskonfiguration herstellen.



Paralleltests verwenden eindeutige Compose-Projektnamen, Ports, Netze, Buckets und Volumes. Explizite container_name, externe Volumes und feste Hostpfade auf Kollisionen prüfen. Kein gemeinsames Testdatenbankschema für parallele Migrationen.



Bei knappen Ressourcen vollständige Supabase-Integrationstests mit einem vom Hauptagenten vergebenen Lock serialisieren. Keine zusätzliche Supabase-Instanz pro Agent erzwingen, wenn kube1 oder der Testhost das nicht tragen kann.



Tests grundsätzlich in Entwicklungs-/Testumgebung; Teilagenten wenden keine Migrationen auf produktive Daten an. Kein docker compose down -v gegen einen fremden oder produktiven Stack.

Schnittstellen vor Parallelstart

Hauptagent und zuständige Fachagenten legen einen kleinen, versionierten Vertrag fest:





Authentifizierter Nutzerkontext, App-Zulassung, Workspace-Rolle, private Chat-Eigentümerschaft und RLS-Transaktionsgrenze.



API-Pfade, Request-/Response-Schemas, Fehlerformat, Pagination, Upload-Limits und stabile Objekt-IDs.



Streaming-Protokoll mit Antwortteilen, Belegen, Abschluss, Fehler und Abbruch; kein Client muss aus Textfragmenten Zustände erraten.



Provider-Interface für Generation/Streaming, strukturierte Ausgabe, Embeddings, Fähigkeiten und Verbindungstest. Credentials niemals als normale Frontend-Response.



Job-Payload mit Workspace, Dokumentversion, auslösendem Nutzer, persönlicher Connection-ID, Idempotenz und Berechtigungsprüfung zur Ausführungszeit. Keine Secrets in Job-Payloads.



Quellen-/Graphbelege und Embedding-Indexprofil inklusive Modell, Version und Dimension. Geteilte Notebooks gewähren keinen Zugriff auf den Embedding-Zugang eines anderen Nutzers.

Mocks dürfen Entwicklung gegen diese Verträge ermöglichen, müssen aber klar gekennzeichnet sein und vor Abnahme durch echte Integration ersetzt werden. Auth-Mocks niemals als Produktions-Fallback.

Aufgabenübergabe an jeden Agenten

Der Hauptagent gibt jedem Agenten folgenden konkreten Auftrag:

Rolle und Task-ID:
Ziel und erwartetes Nutzerverhalten:
Repository und absoluter Worktree-Pfad:
Branch und Baseline-SHA:
Lies: agent.md, MULTI_AGENT.md, geltende Repository-Anweisungen,
      docs/architecture/contracts.md und relevante Entscheidungen.
Erlaubte Schreibbereiche:
Gemeinsame Dateien, die nur ihr Eigentümer ändern darf:
Abhängigkeiten und bereits integrierte Commits:
Verfügbare Testumgebung / reservierte Ports:
Konkrete Abnahmekriterien und Prüfkommandos:
Übergabebericht: docs/handoffs/<task-id>.md

Implementiere und prüfe deinen Bereich. Erfinde keine Credentials oder
Provider-Freigaben. Melde Schnittstellenänderungen vor ihrer Umsetzung.
Committe kleine, zusammenhängende Änderungen gezielt in deinem Branch.
Kein Merge nach main, kein eigener Rollout und kein Versand an Bewerter.
Liefere Commit-SHAs, Tests, offene Punkte und Integrationshinweise.

Jeder Agent schreibt nur seinen eigenen Übergabebericht. Der Bericht enthält geänderte Dateien, Contracts/Dependencies, Migrationserfordernisse, konkrete Testbefehle und Ergebnisse, bekannte Risiken und reproduzierbare nächste Schritte. „Fertig“ bedeutet implementiert und geprüft; fehlende Credentials heißen „extern blockiert“.

Reihenfolge und Parallelität

Welle 1: Fundament

A setzt Identität, Datenmodell und Zugriffstrennung um. C klärt parallel Provider-Fähigkeiten und implementiert Adapter gegen den gemeinsamen Vertrag. D erstellt Bedienstruktur und typisierte Clients gegen den Vertrag. E baut Test-/Container-/CI-Grundlage. B definiert Dokument-/Job-/Graph-Anforderungen und beginnt isolierte Parser-/Retrieval-Module.

Mindestens ein schneller vertikaler Durchstich soll früh integriert werden: Anmeldung mit autorisiertem Testkonto, eigenes Notebook, persönliche Modellverbindung, ein Dokument und belegte Antwort. Keine wochenlange isolierte Entwicklung aller Komponenten.

Welle 2: Funktionen zusammenführen

Nach integriertem Daten-/Auth-Fundament aktualisieren B/C/D ihre Baseline kontrolliert. Upload/Worker, persönliche Verbindungen, Chat/Belege und danach Graphretrieval vervollständigen. E ergänzt Rootless-Nachweise, Trivy-Gates, Deployment und Recovery.

Welle 3: Unabhängige Abnahme

F startet vom aktuellen Integrationscommit, prüft Nutzer- und Admin-Demo, fremde Workspace-/Credential-Zugriffe, Einladung/Account-Linking, SSRF/XSS, Widerruf wartender Jobs, Parser-Isolation und Security-Gates. F liefert Findings mit Reproduktion und Schwere; zuständiger Implementierungsagent behebt, F prüft den Fix erneut. Kritische Fehler nicht durch Abschwächen von Tests „lösen“.

Beschränke Parallelität auf reale Ressourcen und verfügbare Agentenplätze. Empfohlener Start: Hauptagent plus drei bis vier Worker. Rollen sind Arbeitspakete, keine Pflicht zu sieben gleichzeitig laufenden Prozessen. Review bei voller Belegung nach abgeschlossenen Implementierungspaketen starten. Unteragenten delegieren nur nach Freigabe des Hauptagenten, damit keine unkontrollierte Agentenkaskade entsteht.

Integration und Konfliktlösung





Agent meldet eine geprüfte Commit-SHA. Hauptagent prüft Diff, Dateizuständigkeiten, Vertragsänderungen und Tests.



Nur Hauptagent integriert im Integrations-Worktree. Standard: Branch-Merge; Cherry-pick nur für bewusst getrennte Commits. Einen Commit nicht über beide Wege doppelt integrieren.



Vor Integration ist das Arbeitsverzeichnis sauber oder eigene Arbeit gesichert. Kein pauschales ours/theirs zur Konfliktlösung. Konflikte fachlich mit Eigentümern lösen.



Bei Datenmodell-/Migrationsänderungen A beteiligen. Abhängigkeiten und Lockfiles kontrolliert zusammenführen, generierte Dateien aus ihrer Quelle regenerieren.



Nach jeder Integration relevante Tests; nach einem vertikalen Meilenstein vollständigen Integrationslauf. Ergebnis mit neuer Integrations-SHA festhalten.



Agenten aktualisieren ihre Branches nur nach abgesprochener Pause und sauberem Commit-Stand durch Merge des bestätigten Integration-Commits. Kein Rebase/Reset/Force-Push auf einem Branch, an dem ein anderer Agent aktiv arbeitet.



Review/Fixes beziehen sich immer auf konkrete SHAs. Ändert sich der Kandidat, betroffene Tests und Review wiederholen. Ein Test auf alter SHA gilt nicht als Nachweis für den neuen Stand.



Hauptagent nimmt produktionsreife Änderungen erst nach vollständigen Gates auf main. Wenn ein Push auf main automatisch deployt, ist dieser Push Teil des kontrollierten Releases; keine Zwischenstände auf main.

Deployment und Abgabe

E implementiert den Deployment-Weg. Hauptagent führt den einzigen koordinierten Rollout über GitHub Actions aus, sobald der Kandidat freigegeben und die erforderlichen Zugänge vorhanden sind. Runner, Produktions-Secrets und Infrastrukturzugang werden nicht an alle Agenten verteilt.

Vor Release müssen mindestens belegt sein:





Echter Upload-bis-Antwort-Fluss mit Quellen und klarer Behandlung fehlender Belege.



Persönliche Modellverbindungen und Zugriffstrennung bei geteilten Workspaces.



Einladungs-/Rollen-/RLS-Tests, Provider-/Job-Isolation und sichere Registrierung.



Minimale Non-root-Images, Rootless-Status bzw. explizite ungelöste Abweichung, Trivy und SBOM für eingesetzte Digests.



Migration, Backup/Restore-Verfahren, Readiness, öffentlicher Smoke-Test und Rollback-Strategie.



Ehrliche Kennzeichnung externer Blocker statt fingierter OAuth-/Abo-Unterstützung.

Erstelle anschließend README, Abgabeübersicht und Loom-Skript gemäß agent.md. Echte Empfängerlinks, Einladungen und Secrets niemals in Git oder Video veröffentlichen. Der Auftrag zur Orchestrierung ist kein neuer Auftrag zum Versand von Bewerbungs-E-Mails.

Fortschritt, Wiederaufnahme und Abschluss

Hauptagent aktualisiert nach jedem Meilenstein den Plan: todo, in_progress, blocked, ready_for_review, integrated, verified. Jede Aufgabe hat genau einen verantwortlichen Eigentümer. Ressourcen- oder Schnittstellenblocker früh melden; unabhängige Arbeit fortsetzen.

Bei Kontextwechsel zuerst Plan, Verträge, Entscheidungen, Übergabeberichte und tatsächlichen Git-/Worktree-Status lesen. Nicht allein auf Gesprächserinnerungen vertrauen. Kein vollständiger Neustart und keine Wiederholung bereits belegter Arbeit.

Nach erfolgreicher Integration und Abnahme Worktrees erst entfernen, wenn ihre Commits enthalten und keine uncommitteten/ungetrackten Ergebnisse mehr nötig sind. Keine erzwungene Löschung; Branches bis zum Abschluss nachvollziehbar behalten.

Der Hauptagent beendet die Aufgabe erst mit einem integrierten, geprüften Ergebnis oder klar benannten externen Blockern. Liefere finale Commit-SHA, Workflow-Lauf, Image-Digests, Live-Status, Prüfergebnisse und offene Punkte. Teilagenten-Erfolg allein ist kein Gesamterfolg.
