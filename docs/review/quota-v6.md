# Mainreview · Quoten0006 vor Kombination mit B3

## Umfang und Nachweisstand

- Main-B3-Code: `5c41641036644171509920a990b479d1cbaaa6c9`, unabhängig auf eigenem kube1-SCRAM-Stack mit314Defaulttests/Ruff/Exit0 und scopedCleanup geprüft.
- Geprüfter A5-Kandidat: `b02d7793468b15d82ef6d5a68e16b88e85211ddd`, ursprünglicher Docs-HEAD `e513db812bde1294aa813a0f5205c0ae315facb4`. Worker281Defaulttests/38gezielteTests mit Exit0 sind **kein kombinierter Main-Nachweis**.
- Main las vollständiges0006-SQL, Pythonmigration, Quotahelper, Bootstrap-/Lease-/Upload-/ORM-Diff und Anpassungen der bestehenden Sicherheitsfixtures. B3 prüfte unabhängig die API-/Callerfolgen read-only; Bericht im B3-Handoff, Docscommit `532b4e522b8755e8f2fcac36840862e7c2d729ad`.

## Nachvollzogene Grenzen

- Fester invoker-only Gate ohne Callerargumente, READ COMMITTED und nonblocking Advisorylock. Exakt fünf bestehende EXECUTE-Rollen, PUBLIC widerrufen. BEFOREtrigger fragen keine fremde Kapazität ab; Capacity/Mirror laufen erst nach RLS/WITHCHECK.
- Definer an vorhandenen NOLOGIN/NOBYPASSRLS-I/O-Owner gebunden, feste pg_catalog-Suchpfade und qualifizierte Relationsnamen. Temporäre Schema-CREATE-Rechte widerrufen; nur fünf zusätzliche Mirror-UPDATE-Spalten, reduzierte App-SELECT-Allowlist und deferred ORM-Felder. Keine neuen Rawjob-/Provider-/Secretrechte festgestellt.
- Bestehende RPC-Transformation verwendet ausschließlich feste Signaturen und genau einmal vorhandene Textanker; Eigentümer/ACLs/Authkörper bleiben ansonsten erhalten. Historische0002/0003/0005 unverändert. Bei abweichenden Ankern Migration abort statt stiller Teiländerung.
- Exakte SQLSTATE+message_primary-Whitelist, ausschließlich echte Psycopg-Errors/optional SQLAlchemy-DBAPIError.orig; keine Klassifikation nach beliebigem Fehlertext und kein I/O-/Commit-/Retry im DBhelper.
- Alte Ausführungs-/Fencingregressionen bleiben teils ausdrücklich0005-version-pinned, weil0006 ungebundene aktive/providergebundene Fixtures absichtlich ablehnt. Neue Headfälle müssen die kanonischen Rollen-/Fencing-/Quotaassertions separat belegen; nicht als unveränderte Head-Kompatibilität beschreiben.

## Q6-R1 · Preflight akzeptiert inkompatibel lange Altlease

**Status: Q6-R1 geschlossen für DB-/Migrationsscope durch eigene Mainprüfung200Tests. Gesamte B3-/HTTP-Callerabnahme weiterhin offen. Historische Severity: mittel.**

`backend/migrations/versions/0006_quota.sql:7-29` prüft bei bestehenden runningJobs Token/Deadline/Attempts, aber keine maximale Restlaufzeit. Der neue Laufzeitguard (`:148-151`) verweigert dagegen eine neue Deadline weiter als300Sekunden in der Zukunft.

A5 reproduzierte auf exaktb02 im eigenen isolierten kube1-Projekt: kanonischer3600sAltlease → UpgradeExit0, Mirror unverändert/active1. Das ist **kein demonstrierter Untercount oder Privilegbypass**, sondern eine Inkonsistenz zwischen Migrationszulassung und unterstützten Laufzeitmetadaten. Bisherige281Tests deckten sie nicht ab.

Mainruling: Test erwartet atomare `quota_preflight_required`-Ablehnung bei mehr als300Sekunden Restlaufzeit; danach enge Preflightprüfung ergänzen. Gültige1..300sSeedfälle/exakte Parität und abgelaufene kanonische Outstanding-/Active-Semantik erhalten. Keine Leaseverkürzung, automatische Retirement-/Bestandsreparatur, Fakeatteste oder historischen SQLänderungen. Auftrag im bestehenden A5-Worktree `ctx_e7f2b6474f9e`/`task_56729b2174b3` geliefert: Fix `ca6e292922f587d1ea0db59ecb13d6afc808702b`, Docs `4440804d8b7937dead34124cd67829991ea5ddac`. Main las den Diff: nur frische Clock nach Altwritersperren und maximale300sRestlaufzeit im Preflight; zusätzlicher Änderungsscope nur eigene Migrationstests/Handoff. Worker: committed RED`4cf6910`1erwarteterFehler/4positive/Exit1; GREEN14Migrationstests/Exit0, default286+Ruff/FINAL_EXIT0, aktivePythonLSP1clean/0inconclusive, scopedCleanupExit0/0Ressourcen. Nach eigener Quellprüfung seriell als Main-Verifikationskandidat übernommen, weiterhin **keine unabhängige kombinierte Mainausführung/Abnahme**. Unverändert erhalten: B3-Code, synthetische sichere Rollenfixture, Root-Docs. LokalesRuff und10aktivePythonLSP-Dateien clean. Danach eigener Main-kube1-Lauf auf exakt`41d60aa`: frische0006/SCRAMfixture, `backend/tests/auth tests/security` **200passed/1Warnung/142.98s**, Ruff/RUN_EXIT0/CLEANUP_EXIT0/0Restressourcen, Credentialdatei entfernt. Enthält Quoten-/Übergangs-/14Migrationsfälle; **kein voller B3-/Backend-Defaultlauf**. Log `.runtime-quota-41d60aa-r2/run.log`. Erster externer Operatorversuch scheiterte vor Tests an `/operator.py`/stdlib-Shadowing; nur Operatorname korrigiert, Snapshot unverändert.

## Fehlender historischer Jobmirror: bewusst separater Releasegate

Erste Main-Rückfrage wurde nach Nachlesen des ursprünglichen84ab1f7-Rulings präzisiert: gelöschte/cascadierte0005Jobs können unbekannte externe Arbeit hinterlassen, die der alte Ledger nicht rekonstruieren kann. Die genehmigte Grenze erlaubt Migration ohne erfundene Belegung, blockiert aber **Enablement bis separat bewiesener externer Quieszenz**. Das ist kein bestätigter Verstoß und kein neuer pauschaler Tombstone-Migrationsblock.

Gezielter b02-Beobachtungsbeleg: Parentcascade bzw. Softdelete+Jobdelete bei240sLease ließen Upgrade mit NULLmirror zu; finalizedIntent ohne kanonischen Job wurde atomar abgewiesen. REPRO_EXIT0 bedeutet erfolgreiche Beobachtung, nicht Quieszenzbeweis oder neue Abnahme. Keine künstliche Nullbelegungs-/Remote-Stop-Garantie daraus ableiten.

## B3/0006-Integrationsgate · vor HTTP-/Workerfreigabe

**Hoch für kombinierte Callersemantik, kein neuer DDL-Privilegbefund:** UploadService maskiert exakte Capacity/Busyerrors als generisches503; Worker lässt solche DBAPI-Fehler bei Claim/Pulse/Publish/Cancel entkommen. Alte raw-queued und direkt token-/expiry-/attempt-mutierende B3-Workerfixtures sind mit0006 nicht kompatibel.

B3 erhielt nach geprüfter0006-Quelle den engen Auftrag `ctx_fad9aac0f214`/`task_48ae4bdab9a7` im vorhandenen Worktree: frische ganze **kurze DB-Transaktionscallbacks** nur bei exakt quota_gate_busy boundedretry, Capacity opaque429, Busyerschöpfung503, ehrliches Worker-yield ohne Attemptverbrauch. **Kein Retry um Storage-I/O oder den gesamten Upload.** Kanonische Fixtures, Contentionsfälle und keine wiederholten PUTs/Reads belegen. Sticky work_cancelled nach Wiederzulassung und konservative aktive Belegung bis gültigem Leaseende explizit behandeln, keine stillschweigende Reaktivierung.

Zusätzlich bestehende Root-HTTP-Mutationen prüfen:0006gated auch Notebook-/Member-/Invite-/Admission-/Deletepfade; testlokaler Invite-Retry beweist keinen produktiven HTTP-Retry. MainTask15, A5-ReadonlyCallerreview `ctx_2729f592879f`/`task_209716493798`; keine WholeHTTP-/Networkreplays oder Änderungen am Generator-yield als Retrymechanik.

Root-/Engine-/HTTPwiring, Storagepolicies, Dispatcher, Maintenanceprincipal und Remote-write-Quieszenz bleiben separate Gates. Router und Dokument-/Chat-Capabilities bleiben aus.
