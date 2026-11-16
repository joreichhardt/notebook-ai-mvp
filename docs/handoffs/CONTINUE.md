# Fortsetzung

Repository: `../notebook-integration`, Branch `integration/notebooklm`. Veröffentlichter Code: `9ac3ea7b9eda15ffc02db3b8733b6f2254592f86`. Keine aktiven Worker.

## Auftrag

Benutzbares Notebook-MVP auf `note.yokoma.de`: zuerst echter Login/Workspace mit bestätigter E-Mail, dann persistenter privater lokaler Speicher und Upload → Text → Anzeige. Appadmin nur über das tatsächlich bestätigte freigegebene Konto einrichten.

Serverbetrieb: Rootless Podman hinter Cloudflare. Der aktive Deploymenthelper verwendet Podman bereits; fremde Docker-Dienste und historische Pakete erhalten.

## Nächster Schritt

CI-Collectionfehler beheben. Sourcepush ist erfolgt, CI-Lauf `43961848410` scheiterte bei Collection mit drei Importfehlern; Veröffentlichung der Images wurde übersprungen. Task39 betrifft diese Fehler, Task33 den separaten BLE001-/Observer-Privacy-Blocker. Keine Gates umgehen.

Danach eigene Bitwarden-Lesezugänge/Secrets und SMTP-only Credential, getrennte App-/Runnerkonten, nativer Provider-/Rootless-Nachweis und echte E-Mail-Bestätigung. Noch keine Appfreischaltung oder Laufzeitmigration ausgeführt.

## Lokale Arbeit erhalten

S1/S2a akzeptiert. S2b in `backend/app/workers/text.py`, `supervisor.py` und `test_text_supervisor_control.py` bleibt unakzeptiert/geparkt; nicht stagen oder ausführen. Ebenfalls bestehende Änderungen in B5/D4 erhalten.

Detailbelege und der vorherige lokale Übergabestand liegen geschützt unter `.pi/`; Originale der gestrafften Arbeitsdokumente unter `.pi/mvp-docs-minimal/` als nicht ausführbare `.DATA`. Dort stehen auch die vor Änderungen gesicherten Quellenidentitäten. Permanenten kube1-Lock `66306:48129473` nie ersetzen/löschen.

GitHub-Publikationszugang ist separat geschützt vorhanden, mit lokaler Nutzungsfrist; kein erneuter Login nötig. Bestehende globale GitHub-Konfiguration unverändert lassen. Kein Token in Chat, Git oder Prozessargumenten. Für weiteren Push isolierte Konfiguration und exakte Remote-SHA-Prüfung verwenden; den nur für ein leeres Remote vorgesehenen initialen Pushhelper nicht erneut ausführen.
