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

Der Patch gilt erst für einen frisch gestarteten Pi-Prozess. Aktive Worker und Supervisor sind nicht allein durch die Dateiänderung als repariert nachgewiesen. B3 checkpointete als `4dbfb1f`; Main stoppte die exakt identifizierte idle Instanz über Orca, wies den alten PID-Exit nach und startete denselben Task im selben Worktree als frischen Pi-Prozess. Der Nachfolger bestätigte korrekten Checkpoint und Fortsetzung. Eine neue tatsächlich automatische Worker-Kompaktierung unter dem Patch ist noch nicht beobachtet. A5s separate Orca-Zustellstörung bleibt offen: guarded stop/release führten ausdrücklich zu keiner Prozessaktion (`alreadySettled` / `no_owned_resource`); hierfür ist ein operatorseitiger geordneter Exit oder eine Identitätsreparatur nötig. Der Supervisor behält seinen alten Core bis zu seinem eigenen kontrollierten Prozesswechsel. Der jeweils aktuelle Aktivierungsstand steht im [Handoff](../handoffs/CONTINUE.md).

## Drei unterschiedliche Ereignisse

1. **Automatische Kompaktierung:** Beide Worker haben persistierte Compaction-Einträge; A5 hat danach weitergearbeitet. Das beweist grundsätzlich vorhandene Funktion, nicht die korrekte Schwelle vor dem Patch.
2. **Manueller Abbruch/Compact:** Der Nutzer bestätigte B3s Abbruch gegen07:56UTC als manuell. Pi bricht beim manuellen Compact den laufenden Turn ab und setzt ihn absichtlich nicht automatisch fort. Ein bewusster Stop wird durch den Patch nicht rückgängig gemacht.
3. **Orca-Readiness:** A5-Follow-up `ctx_19d0c7a4c97e` scheiterte mit `agent_readiness: timeout`; `worker-read` meldete anschließend `worker_identity_changed`. Das ist kein belegter Kompaktierungsfehler. Prozessexistenz allein beweist weder passende Dispatch-Identität noch sichere Wiederverwendung. Diagnose/Recovery gemäß versionspassendem Orca-Guide, keine blinden Send-/Stop-/Retry-Versuche.
