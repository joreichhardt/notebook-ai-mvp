# Supabase-Postgres: ausdrücklich akzeptiertes Risiko für diesen Testrelease

## Freigabe und Grenzen

Verantwortlicher/Freigebender: Projektbetreiber joreichhardt. Explizite
Nutzerentscheidung vom 2026-11-17: Supabase für das Testsystem freigeben,
Testfreigabe committen, pushen und veröffentlichen. Dies ist eine Risikoakzeptanz,
**kein technischer Nachweis von Irrelevanz, Ungefährlichkeit oder Behebung**.
Keine Produktionsfreigabe und keine Änderung der Repository-Sichtbarkeit.

Gültig bis ausschließlich **2026-12-01 UTC**. Scope: der unmittelbar auf
`64473c148726110480f7e14b7c669f970f5c9b14` folgende Testfreigabe-Commit und seine
Workflow-Wiederholungen. Der Betreiber bestätigte am 2026-11-18 die weitere
Freigabe derselben Befunde für private Testreleases. Der Evaluator prüft
weiterhin den ausdrücklich dokumentierten Git-Parent. Andere Digests, neue
Befunde, die öffentliche Route und eine Verlängerung sind nicht eingeschlossen.

Ausschließlich dieses Image:
`supabase/postgres@sha256:f371b5f3f2ac0a05703f33d6e6134515fb2498cab708fb948a0aeb7481467c00`.
Befundquelle: [CI 44119789187, Publish-Job 134170786705](https://github.com/joreichhardt/notebook-ai-mvp/actions/runs/44119789187).
31 OS-Funde (30 HIGH, 1 CRITICAL) und 24 HIGH-Funde in `usr/local/bin/gosu`.

## Vollständige akzeptierte Befundliste

Alle Nummern unten haben das Präfix `CVE-2026-`. Jede Kombination von Paket,
installierter Version, CVE und Schweregrad ist einzeln gebunden. Maschinenlesbare
verbindliche Liste: `deploy/test_db_scan.py`, `APPROVED` (55 Kombinationen).

| Paket | Installierte Version | Schweregrad | CVE-Suffixe |
|---|---|---|---|
| brotli-libs | 1.2.0-r0 | CRITICAL | 111497 |
| c-ares | 1.34.6-r0 | HIGH | 33630 |
| curl und libcurl (jeweils) | 8.19.0-r0 | HIGH | 11352, 11586, 12064, 5773, 6276, 8286, 8458, 8925, 8927, 92392, 9547 |
| libcrypto3 und libssl3 (jeweils) | 3.5.6-r0 | HIGH | 14456, 45447 |
| musl und musl-utils (jeweils) | 1.2.5-r23 | HIGH | 107891 |
| zlib | 1.3.2-r0 | HIGH | 85091 |
| stdlib, nur usr/local/bin/gosu | v1.26.1 | HIGH | 111943, 27145, 32280, 32281, 32283, 33810, 33811, 33814, 33818, 39820, 39821, 39822, 39836, 42499, 42504, 46600, 56853, 56858, 56859, 56860, 56862, 78667, 78669, 97031 |

## Risiko, Begründung und Gegenmaßnahmen

Begründung: Der Betreiber priorisiert einen testbaren MVP und akzeptiert diese
konkreten Herstellerimage-Funde für diesen Testrelease. Mögliche Auswirkungen
umfassen Speicherfehler/Codeausführung, Denial of Service, Informationsabfluss,
TLS-/SSH-Prüfungsumgehungen und Zertifikatsprobleme. Insbesondere ist der
CRITICAL-Brotli-Speicherfehler nicht als irrelevant nachgewiesen; der Scan nennt
`1.2.1-r0` als korrigierte Paketversion. Verfügbare Fixes sind keine Fixes des
hier freigegebenen, weiterhin unveränderten Images.

Betriebsbedingungen: isoliertes Testsystem, nur synthetische Daten und eigene
Testcredentials; keine Produktionscredentials oder Produktionsdaten übernehmen.
DB und Auth nicht direkt öffentlich exponieren; Zugriff ausschließlich über
vorgesehene interne Verbindungen/App-Routen, minimale Rechte und begrenzte
Ressourcen. Diese Bedingungen müssen vor Aktivierung am Zielsystem geprüft
werden; Veröffentlichung ist kein Nachweis ihrer Umsetzung. Bei Verdacht auf
Missbrauch Testzugang sperren, Testcredentials widerrufen und Umgebung neu
aufsetzen. Herstellerupdates/Fixes vor Ablauf prüfen und neu scannen; keine
stillschweigende Verlängerung oder automatische Imageänderung.

## Wirksame Gates und Nachweise

Kein globales Ignore und kein `continue-on-error`. Nur die genannten Tupel im
passenden Image/Testrelease dürfen passieren. Neue CVEs, andere Versionen oder
Schweregrade werden blockiert. Die bestehende separate Auth-Ausnahme bleibt
unverändert. API-/Frontend-/Source-/Konfigurationsprüfungen, Funktionstests und
Secret-Scans aller Images bleiben verbindlich, mit der separat belegten
[öffentlichen Buildschlüssel-Ausnahme](test-db-build-key.md); Scanner-Ausführungsfehler sind
Fehler. Rohbericht `db-vulnerabilities.json`, SBOMs, diese Freigabe und
`environment.txt=test-only` werden im Workflow aufbewahrt. Commit und finale
Registry-Digests stehen bei erfolgreicher Veröffentlichung im unveränderlichen
Release-Artefakt. Dies allein bestätigt weder Deployment noch Browserfunktion.
