# Befristete Supabase-Auth-Ausnahme für das Testsystem

- Freigabe/Verantwortlicher: Projektbetreiber joreichhardt, ausdrücklich am
  2026-11-17 bestätigt (gezielte Testausnahme, keine pauschale Scannerabschaltung).
- Gültigkeit: ab 2026-11-17 bis ausschließlich 2026-12-01 UTC.
- Zweck: Veröffentlichung der MVP-Testimages trotz der bekannten Auth-Funde.
  Keine Produktionsfreigabe und kein Nachweis eines erfolgten Deployments.
- Image: `supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b`.
- Umfang: ausschließlich die 15 HIGH-Funde aus CI-Lauf 44116288555.
  Die verbindliche Liste nach CVE, Paket und installierter Version steht in
  `deploy/test_auth_scan.py` (`APPROVED`). Andere Digests, Versionen, neue Funde
  und jede Einstufung als CRITICAL werden abgewiesen.

## Risiko und Gegenmaßnahmen

Akzeptiert werden bekannte Risiken einschließlich Denial of Service,
Speicherfehler mit möglicher Codeausführung und TLS-/Zertifikatsproblemen.
Eine offizielle Herstellerveröffentlichung widerlegt diese Risiken nicht.
Ein Finding (`CVE-2026-32286`, pgproto3/v2 v2.3.3) hat im Bericht keinen Fix.
Die Ausnahme ist eine bewusste Risikoübernahme für Tests, keine Bewertung
als ungefährlich oder als False Positive.

Nutzung ausschließlich in der isolierten Testumgebung mit synthetischen Daten,
eigenen Testzugängen und ohne Übernahme von Produktionscredentials. Auth und
Datenbank nicht direkt öffentlich exponieren; nur vorgesehene App-Routen
zugänglich machen. Diese Betriebsbedingungen sind vor einer Aktivierung am
Zielsystem zu prüfen, nicht durch die Veröffentlichung automatisch erfüllt.
Bei Sicherheitsvorfällen Testzugang sperren und Testcredentials widerrufen.

Source-, Secret- und alle anderen Image-Gates bleiben unverändert verbindlich.
Der Auth-Scan wird weiterhin ausgeführt; sein unveränderter JSON-Bericht und
diese Ausnahme werden als CI-Artefakte erhalten, auch bei fehlgeschlagener
Veröffentlichung. Scannerfehler werden nicht unterdrückt. Release-Metadaten
enthalten `environment.txt` mit `test-only`. Bestehende SHA-/Digest-Referenzen
bleiben unverändert; aus ihnen darf keine Produktionsfreigabe abgeleitet werden.

Vor Ablauf Herstellerupdates prüfen, gepinntes Image aktualisieren und erneut
scannen. Eine Verlängerung oder Erweiterung erfordert eine neue ausdrückliche
Freigabe; der Evaluator verweigert die Ausnahme ab dem Ablaufdatum.
