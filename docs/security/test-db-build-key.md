# Testausnahme: öffentlicher libcamera-Buildschlüssel im DB-Image

Freigebender/Verantwortlicher: Projektbetreiber joreichhardt, 2026-11-17.
Auftrag: MVP veröffentlichen; nachgewiesene öffentliche, ungenutzte Builddateien
eng ausnehmen, niemals echte Credentials veröffentlichen. Gültigkeit bis
2026-12-01 UTC (ausschließlich), nur Testsystem, keine Produktionsfreigabe.
Gilt ausschließlich für den Freigabe-Commit unmittelbar nach
`f93fb64a593b15a7b86f297337d0d97d0c0f3762` und dessen Workflow-Wiederholungen.
Der Betreiber bestätigte diese unveränderte Ausnahme am 2026-11-18 für das private Testsystem.

## Exakter Scope

- Image: `supabase/postgres@sha256:f371b5f3f2ac0a05703f33d6e6134515fb2498cab708fb948a0aeb7481467c00`
- Pfad: `/nix/store/2ryvqnw8g2lcrvij4hqlm1ihflw2x3hd-ipa-priv-key.pem`
- Trivy: Class `secret`, RuleID `private-key`, Severity `HIGH`, höchstens ein Fund.
- Datei-SHA256: `3b3ad9dfa59a1965b74a6f81d8d3b6cad221fd0d8c06411defcf9b0cf0382cfa`

## Herkunft und tatsächliche Prüfung

Am 2026-11-17 wurde auf kube1 das lokal vorhandene exakt gepinnte Image mit
Rootless Podman nur gemountet, nicht gestartet, und anschließend wieder
unmountet. Nur Datei-Hash und Referenznamen wurden ausgegeben, kein Schlüssel.
Die Bytes stimmen mit der öffentlichen Datei in
[Nixpkgs, Commit 110944a419a04949addaa89726407fb54bc2b7b0](https://github.com/NixOS/nixpkgs/blob/110944a419a04949addaa89726407fb54bc2b7b0/pkgs/by-name/li/libcamera/ipa-priv-key.pem)
überein. Das zugehörige
[package.nix](https://github.com/NixOS/nixpkgs/blob/110944a419a04949addaa89726407fb54bc2b7b0/pkgs/by-name/li/libcamera/package.nix)
beschreibt ausdrücklich einen statischen Schlüssel für reproduzierbare
IPA-Modulsignaturen nach Strip/RPATH-Fixup (`preBuild`/`postFixup`). Der öffentliche
Nix-Cache liefert für denselben Storepfad ebenfalls eine Narinfo.

Im untersuchten Image waren nur libcamera-Derivationen vorhanden, keine
libcamera-Laufzeitinstallation. Genau eine Derivation referenziert den Pfad:
`5lh4wn54m27rgw5bi7cgsf8b0jr49gyr-libcamera-0.6.0.drv`. Keine Referenz wurde in
`etc`, `usr/local/bin` oder `docker-entrypoint-initdb.d` gefunden. Die Datei ist
somit ein öffentliches Build-Artefakt, kein Notebook-/DB-/TLS-Zugang. Diese
Prüfung behauptet keine allgemeine Ungefährlichkeit von Herstellerimages.

## Gegenmaßnahmen und begrenzte Akzeptanz

Nicht als Credential oder als Vertrauensanker der Anwendung verwenden; keine
Kamera-/IPA-Nutzung im MVP. Isolierte Testumgebung und eigene Testcredentials
beibehalten. Bei Änderung von Image, Pfad, Finding, Schweregrad oder zusätzlichem
Secret blockiert der Evaluator. Alle anderen Scan-Gates bleiben aktiv.

Der Secret-JSON-Bericht wird nur per Pipe verarbeitet. Rohmatches werden weder
in Logs noch in Dateien/Artefakten ausgegeben. Nur die Entscheidung mit
Digest/Pfad/Regel/Dateihash wird gespeichert. Scannerfehler bleiben durch
`set -euo pipefail` blockierend. Bei Ablauf oder geändertem Release ist eine
neue Prüfung/Freigabe nötig. Entfernen unnötiger Build-Artefakte aus einem
zukünftigen Herstellerimage ist spätere Härtungsarbeit, kein neues Teilprojekt.
