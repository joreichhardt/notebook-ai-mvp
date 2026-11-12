# Pi-Kompaktierung und Orca-Fortsetzung

## Nachgewiesene Ursache

Die Worker verwenden Pi1.5.2 mit dem virtuellen Modell `openai-codex/sol-astra-auto`. In `~/.pi/agent/settings.json` ist automatische Kompaktierung aktiv; Projektsettings überschreiben sie nicht. Reserven sind für die **physischen** Modelle konfiguriert: Sol315000 bei500000 Kontext, Astra171360 bei272000 Kontext, jeweils20000 jüngere Tokens behalten.

Die ursprüngliche AgentSession verwendete bei automatischen Prüfungen physische Kontextgrenzen, aber die Einstellungen des **virtuellen** Modells. Der Router fiel daher auf16384 Reservetokens zurück; die gewünschten modellabhängigen Schwellen griffen nicht. Dies wurde mit dem installierten SettingsManager und echten AgentSession-Kontrollpfaden offline reproduziert.

## Autorisierte lokale Korrektur

Der Nutzer hat die gezielte Korrektur samt Regressionstests ausdrücklich freigegeben. Genau eine installierte Laufzeitdatei wurde geändert:

`~/.pi/agent/install/releases/1.5.2/node_modules/@earendil-works/pi-coding-agent/dist/core/agent-session.js`

Automatische Pre-request-/Post-run-Prüfungen und Compaction-Preparation erhalten nun dasselbe physische Modell für Kontextgrenzen und Settings. Modellrouting, Auswahl, globale Konfiguration, manuelle Abbrüche und Retry-Verhalten bleiben unverändert. Schwellen: **Sol185000**, **Astra100640**, jeweils erst bei Überschreitung.

Sicherung, exakter Patch, Offline-Tests, RED/GREEN-Logs und Rollbackanleitung außerhalb des Notebook-Repos:

`~/.pi/agent/local-patches/1.5.2-physical-compaction/`

- Original SHA256: `ee756f6f74afe9f8930ebf216c95334605d913a275a0eec2c9702f101baef3c3`.
- Patch SHA256: `c76f9585431ac2f58b66ae9213cf2dc41f1b2a796873f052c74441f5084b14e9`.
- `node --check` erfolgreich; Original3passed/9failed, Patch **12passed/0failed**. Die Kontrollpfadtests nutzen synthetische Projektionen/Routes/Summary-Stubs, keine Provideraufrufe oder laufenden Worker.

```sh
node --test ~/.pi/agent/local-patches/1.5.2-physical-compaction/regression.test.mjs
```

Dies ist ein **lokaler Runtime-Patch**, keine upstream Pi-Veröffentlichung. Ein verwaltetes Pi-Update kann ihn ersetzen; danach Version/Hashes/Tests neu prüfen, nicht blind erneut anwenden. Die Source Map gehört weiterhin zur ursprünglichen Distribution.

## Aktivierung bleibt ein eigener Schritt

Bereits gestartete Pi-Prozesse behalten den importierten alten ESM-Core. **`/reload` allein lädt diesen Core nicht neu.** Vor kontrolliertem Prozesswechsel eigenen Arbeitsstand/Session sichern, nur Orcas geschützte Lifecycle-Befehle verwenden und das Ende der alten Instanz nachweisen, bevor dieselbe Aufgabe fortgesetzt wird. Keine Doppelstarts und keine ungeprüfte Identitätsübernahme.

Der Patch gilt erst für einen frisch gestarteten Pi-Prozess. Aktive Worker und Supervisor sind nicht allein durch die Dateiänderung als repariert nachgewiesen. B3 checkpointete als `4dbfb1f`; Main stoppte die exakt identifizierte idle Instanz über Orca, wies den alten PID-Exit nach und startete denselben Task im selben Worktree als frischen Pi-Prozess. Der Nachfolger bestätigte korrekten Checkpoint und Fortsetzung. Eine neue tatsächlich automatische Worker-Kompaktierung unter dem Patch ist noch nicht beobachtet. A5s separate Orca-Zustellstörung wurde nach Nutzer-Exit und tatsächlichem PID-/Worktree-Prozessnachweis durch denselben Task im selben Worktree frisch fortgesetzt. Frühere guarded stop/release ohne Prozessaktion wurden nicht als Exit umgedeutet. **Pi1.5.2 beendet mit `/quit`, nicht `/exit`** (installierte Slash-Command-Liste und Interactive-Handler geprüft). Eine Nutzerbestätigung ersetzt den tatsächlichen Prozessexit-Nachweis nicht. Der Supervisor behält seinen alten Core bis zu seinem eigenen kontrollierten Prozesswechsel. Der jeweils aktuelle Aktivierungsstand steht im [Handoff](../handoffs/CONTINUE.md).

## Autorisierte Secret-Guard-Korrektur

Zusätzlich gab der Nutzer gezielte Korrektur von `pi-secret-guard1.2.15` frei. Der breite Passwortregex las einen unquoted Nullwert samt nachfolgender Methodenfolge als Passwort. Außerdem erkannte die Aktionssuche `git -C`, aber die Verzeichnisauflösung ignorierte es und prüfte das falsche Repo.

Lokaler Patch mit Originalen, kompletter Änderung, Regressionstests, Hashes und Rollback: `~/.pi/agent/local-patches/secret-guard-correctness/`. Tatsächliche Hooktests mit ausschließlich privaten synthetischen Git-Repos einschließlich echtem linked Worktree: **Original15grün/19rot → Patch34/34grün**. Drei Node-Syntaxchecks bestanden; aktive LSP-Probe drei Dateien **inconclusive**, kein bestätigter Typecheck. Keine Provideraufrufe/Produktionscredentials oder bestehenden Workspace-Reviewstates in Tests.

- Benigne unquoted Nullprimitiven erreichen weiterhin die normale Kontextprüfung; quoted Passwörter, längere ähnlich beginnende Werte und bekannte Secretformate bleiben Hardblocks.
- Literale Git-Verzeichnisse samt quoted/attached/kumulativem `-C` werden korrekt aufgelöst; unbekannte/dynamische Kontexte fail-closed. Staging und Commit in **getrennten Toolcalls**, vorzugsweise führendes `cd` ins genaue Repo; keine Env-/Konfig-Overrides oder Commit-Pathspecs. Das ist eine begrenzte POSIX-Grammatik, keine allgemeine Shell-Sandbox.
- Aktuelle Hardregeln werden vor Cachefreigabe geprüft. Softreview verlangt unveränderten Diff/Repo/Aktion/TTL. Keine gefälschten Freigaben, Ausnahmen oder deaktivierten Regeln.
- Fehlgeschlagene Diffabfragen und unbestimmbare Pushbaseline blockieren. Eine gültige leere Baseline bleibt erlaubt. Explizite Mergevorbereitung ohne Commit wird nicht für den separat zu prüfenden finalen Commit gehalten.

Extensions benötigen im Gegensatz zum Core nur `/reload` im Idle oder einen kontrollierten frischen Prozess. B3 wurde nach Checkpoint/guardedstop/Exitnachweis im selben Task frisch gestartet und bestätigte echte Softreview plus normalen erfolgreichen Commit; anschließend314Tests grün. Auch A5 wurde nach committed HOLD-Checkpoint, bestätigtem Idle und guardedstop/Exitnachweis im selben Task/Worktree frisch gestartet; der Nachfolger bestätigte Checkpoint und selbstständige Weiterarbeit. Auch im Supervisor wurde der neue Extensioncallback nachgewiesen: Eine absichtlich read-only Git-Dry-run-Probe wurde vom neuen spezifischen Kontextvalidator vor Ausführung blockiert. Der Guard-Aktivierungsblocker ist geschlossen; der Pi-Core benötigt weiterhin einen eigenen Prozesswechsel. Updates können den lokalen Patch ersetzen: Version/Hashes/Tests erneut prüfen, nicht blind reapply.

## Drei unterschiedliche Ereignisse

1. **Automatische Kompaktierung:** Beide Worker haben persistierte Compaction-Einträge; A5 hat danach weitergearbeitet. Das beweist grundsätzlich vorhandene Funktion, nicht die korrekte Schwelle vor dem Patch.
2. **Manueller Abbruch/Compact:** Der Nutzer bestätigte B3s Abbruch gegen07:56UTC als manuell. Pi bricht beim manuellen Compact den laufenden Turn ab und setzt ihn absichtlich nicht automatisch fort. Ein bewusster Stop wird durch den Patch nicht rückgängig gemacht.
3. **Orca-Readiness:** A5-Follow-up `ctx_19d0c7a4c97e` scheiterte mit `agent_readiness: timeout`; `worker-read` meldete anschließend `worker_identity_changed`. Das ist kein belegter Kompaktierungsfehler. Prozessexistenz allein beweist weder passende Dispatch-Identität noch sichere Wiederverwendung. Diagnose/Recovery gemäß versionspassendem Orca-Guide, keine blinden Send-/Stop-/Retry-Versuche.
