# Docker-Tests auf kube1

## Verbindlicher Ort und Isolation

Docker-Builds, Compose-Teststacks und containerisierte Integrationstests laufen auf **kube1**, nicht auf dem Laptop. Leichte Entwicklungsprüfungen können lokal bleiben. Dies ist Testbetrieb, kein Produktionsrollout und keine Freigabe von Upload/Chat.

SSH-Zugang `ssh kube1` wurde nichtinteraktiv geprüft: Docker 29.1.3, Compose 2.40.3+ds1-0ubuntu1, 12 logische CPUs, rund 31 GiB RAM. Diese Bestandsaufnahme ist kein dauerhafter Kapazitätsnachweis; vor neuen Tests Last und Ports prüfen. Der vorhandene Docker-Daemon ist kein Rootless-Nachweis. Bestehenden Daemon und produktive Dienste unverändert lassen.

| Bereich | Ausschließlich eigener Serverpfad | Compose-Projekt | Host-Port |
| --- | --- | --- | --- |
| Main Integration | `/home/jre/notebook-tests/main/<commit>` | `notebook-test-main` | Foundation: `127.0.0.1:18190`; DB/Runner: keine |
| B3 | `/home/jre/notebook-tests/b3/<commit>` | `notebook-test-b3` | keiner für DB/Runner; weitere Ports erst abstimmen |
| A5 | `/home/jre/notebook-tests/a5/<commit>` | `notebook-test-a5` | keiner für DB/Runner; weitere Ports erst abstimmen |

Je Bereich nur einen Stack gleichzeitig betreiben. Compose-Netze und Volumes mit eigenem Projektpräfix erzeugen; keine externen Netze/Volumes, Host-Netzwerke, Docker-Socket-Mounts oder Produktionsmounts. Worker-DBs und Runner bleiben im eigenen Testnetz. Synthetische Daten und eigene kurzlebige Test-Credentials verwenden, niemals Secrets anderer Anwendungen kopieren. Bei fehlendem Zugang/Kapazität Tests blockiert melden, kein Laptop-Fallback.

## Foundation-Snapshot übertragen und starten

Vorher Git-Status und den zu testenden Commit prüfen. Nur **committete** Dateien übertragen (`git archive`), keine Worktree-Kopie mit `.env`, `.pi/`, lokalen Secret-Dateien oder fremden Artefakten. Ein Snapshot ist unveränderlich; existiert das Ziel bereits, Übertragung abbrechen und prüfen. Keine globalen Docker-Kontexte umschalten.

Auf dem Laptop im Integrations-Repository (nur Archivierung, kein Containerbuild):

```sh
set -eu
rev=$(git rev-parse --short=12 HEAD)
git archive HEAD | ssh -o BatchMode=yes kube1 \
  "set -eu; umask 077; mkdir -p /home/jre/notebook-tests/main; mkdir /home/jre/notebook-tests/main/$rev; umask 022; tar -xf - -C /home/jre/notebook-tests/main/$rev"
printf 'Snapshot: %s\n' "$rev"
```

Parent und Snapshot-Wurzel bleiben `0700`; beim Entpacken absichtlich `umask 022`, damit reine Quelltextdateien im Buildkontext ihre Git-Modi behalten. Sonst kopiert Docker etwa `0600`-Rootdateien ins Image und der Non-root-Prozess kann seinen eigenen Code nicht lesen. Keine Secrets in diesen Snapshot übertragen.

Auf kube1, `<commit>` durch genau diesen Snapshot ersetzen. Vorhandenes eigenes Projekt zunächst prüfen, nicht durch einen zweiten Snapshot unbemerkt ersetzen. Port 18190 muss frei oder bereits diesem Projekt zugeordnet sein; nie den Shop-Port 18080 verwenden.

```sh
cd /home/jre/notebook-tests/main/<commit>
export NOTEBOOK_TEST_PORT=18190
# Konfiguration prüfen, keine produktive .env einlesen/rendern.
docker compose --env-file /dev/null -p notebook-test-main \
  -f compose.yaml -f compose.test.yaml config --quiet
docker compose --env-file /dev/null -p notebook-test-main \
  -f compose.yaml -f compose.test.yaml up -d --build --wait --wait-timeout 120
curl --fail --silent --show-error http://127.0.0.1:18190/api/health/live
curl --fail --silent --show-error -o /dev/null http://127.0.0.1:18190/
```

Der Foundation-Stack enthält API und Frontend, **keine** Supabase-/Storage-/Worker-Integration. Das Testoverlay setzt Neustarts auf `no` und den öffentlichen Ursprung auf `https://localhost`; vorhandene Limits bleiben pro Dienst 1 CPU, 512 MiB und 128 Prozesse, schreibgeschützte Root-Dateisysteme und Capability-Drop. Healthcheck plus HTTP-Smoke bestätigen keine echte Anmeldung oder Produktionsbereitschaft. Schwerere Runner ebenfalls explizit begrenzen und nicht gleichzeitig unkoordiniert starten.

Für eine manuelle Vorschau ohne öffentliche Freigabe vom Laptop:

```sh
ssh -N -L 18190:127.0.0.1:18190 kube1
```

Dann `http://127.0.0.1:18190` öffnen. Kein neuer DNS-Eintrag, Tunnel oder Firewall-Port. HTTP-Vorschau ersetzt den HTTPS-Auth-Test nicht.

## Datenbank-/Backend-Integration

Seit A5 ist zusätzlich ein isolierter PostgreSQL16-/Python-Teststack vorhanden. Nach obiger Snapshot-Übertragung auf **kube1** ausführen, wenn im eigenen Main-Projekt kein anderer Stack läuft:

```sh
cd /home/jre/notebook-tests/main/<commit>
docker compose --env-file /dev/null -p notebook-test-main \
  -f backend/tests/auth/compose.a5-test.yaml build runner
docker compose --env-file /dev/null -p notebook-test-main \
  -f backend/tests/auth/compose.a5-test.yaml up -d --wait db
docker compose --env-file /dev/null -p notebook-test-main \
  -f backend/tests/auth/compose.a5-test.yaml run --rm -T runner
```

Der Runner migriert die eigene synthetische DB, führt die normale Pytest-Suche und Ruff ohne Schreibcache aus. DB/Runner haben keine Hostports, ein eigenes internes Netz, 1 CPU und 512 MiB/1 GiB sowie je 128 Prozesse. Fixture-Adminzugang ist nur für dieses isolierte Testnetz; Anwendungstests prüfen separat `notebook_app`/RLS. Nach Abschluss ausschließlich dieses Testprojekt inklusive **synthetischem** DB-Volume entfernen und Projektlabels nachkontrollieren:

```sh
docker compose --env-file /dev/null -p notebook-test-main \
  -f backend/tests/auth/compose.a5-test.yaml down --volumes
```

Main-Snapshot `8862a1e2636d` wurde so frisch durch Migration0005, 238 Defaulttests und Ruff geprüft; keine vollständige Upload-/Storage-Abnahme. Befehle/Ergebnisse und Cleanup stehen im Integrationslog.

### Main-Verifikation des integrierten B3-Stands unter SCRAM

Main prüfte den committed Snapshot **`5c4164103664`** unabhängig mit `backend/tests/documents/compose.b3.yaml` und dem darin enthaltenen Runner. Explizit `--env-file /dev/null -p notebook-test-main` verwenden; der Projektname in der wiederverwendeten Datei darf nicht zur Übernahme des Worker-Projekts führen. `B3_TEST_SNAPSHOT` zeigt auf den eigenen Main-Snapshot, UID/GID auf den eigenen Non-root-Runner, `B3_TEST_PASSWORD_FILE` ausschließlich auf eine neu erzeugte synthetische Datei im eigenen privaten Laufzeitverzeichnis. Diese Variablen enthalten nur Pfade/IDs, keine Passwortwerte.

Vorher eigene Container/Netze/Volumes per Projektlabel auf leer prüfen. Operatorverzeichnis0700, Logs ab Erstellung0600; synthetisches Material ohne Ausgabe erzeugen. Die gemeinsam nur in DB/Runner eingebundene Testdatei ist für deren unterschiedliche UIDs lesbar, auf dem Host aber durch ihr privates0700Elternverzeichnis geschützt. Keine Produktions-Secret-Dateikonvention daraus ableiten. Runner schreibt eigene rollenbezogene pgpass-Dateien0600 im flüchtigen Containerbereich. Kein Hostport, keine Produktionsmounts oder Docker-Socket.

Exakter Operatoraufruf und geschützter Laufnachweis liegen unter `/home/jre/notebook-tests/main/.runtime-5c4164103664/{launch.py,run.log,cleanup.log}`; Snapshot separat unverändert. Ergebnis: gezielt2, Watchdog erwartetesRED/GREEN1, A-Fixture1, Service12, **314Defaulttests/Ruff grün**, RUN_EXIT0/CLEANUP_EXIT0, anschließend0eigeneContainer/Netze/Volumes und externe Credentialdatei entfernt. Wiederholungen benötigen einen neuen expliziten Snapshot/Laufpfad; bestehende Nachweise nicht überschreiben. Dies ist Backend-/Synthetic-Storage-Integration, keine reale Storagepolicy-, HTTP-/Produktions- oder0006-Abnahme.

### Zusätzliche synthetische Test-Logins

Bei SCRAM mit rollenbezogenen `0600`-pgpass-Dateien übernimmt ein nachträglich angelegter Login nicht das Passwort oder die pgpass-Zeile des Setup-Admins. Solche Fixtures erzeugen ein eigenes zufälliges Testpasswort, übergeben es ohne Ausgabe an die Rolle und verwenden eine **ab Erstellung `0600`** geschützte, korrekt escapte eigene pgpass-Datei. Die SQLAlchemy-URL enthält kein Passwort; `connect_args.passfile` wählt die Datei explizit. Direkten `current_user`, Dateirechte und Rollenbeschränkungen prüfen; Datei/Engine/Rolle auch im Fehlerpfad bereinigen. Kein `trust`-Fallback und keine Produktionszugänge.

Main korrigierte so ausschließlich `test_fresh_admission_check_and_python_storage_helpers` in `9d5a9d0`. B3 verifizierte die exakte Fixture und seine volle314erSuite unter SCRAM auf `ea704f48fc81`; das ist kein eigenständiger Main-Integrationsnachweis. Echte Ergebnisse stehen im Integrationslog. Für den parametrisierten DDL-Aufruf nutzt die Fixture den offiziellen [Psycopg-ClientCursor](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#client-side-binding-cursors), da PostgreSQL DDL nicht serverseitig mit `$1` bindet. Offizielle Quellreferenz: `psycopg/psycopg`, `docs/advanced/cursors.rst` (Dokumentationswebsite beim Prüfen HTTP403, offizielles GitHub-Original erfolgreich gelesen).

## F4: isolierte Discovery-/Disconnect-Abnahme

Der eigene opt-in Runner liegt unter `tests/security/disconnect_boundary/{compose.f4.yaml,operator_f4.py,run_f4.py}`; genaue Kommandos und Grenzen stehen im [F4-Bericht](../review/disconnect-boundary.md). Kein unverändertes A5-Trustmanifest verwenden. Operator nur aus einem neu archivierten vollständigen reviewed SHA unter `/home/jre/notebook-tests/main/<sha>` auf kube1 ausführen; vorher Main-Slot und leeren eigenen Namensraum prüfen. Eigene synthetische DB `notebook_f4_<sha12>`, SCRAM und rollenbezogene0600Passfiles, interne DB-Verbindung, keine Hostports; keine Produktionszugänge. Lauf und Evidenzpfad sind exklusiv, keine Wiederverwendung/Blindreruns.

Am 2026-11-13 wurde unter ausdrücklicher Nutzerfreigabe exakt `276f1baa52cfaad812a1d47adaacc5c8e35d94ba` einmalig geprüft: Migration/Rolle0, **30 echte PostgreSQL-/In-process-ASGI-HTTP-Fälle +135fokussierte Tests**,0Fehler/0Skips,Operator0 und Cleanup0/0/0/Credentialsentfernt. Main bestätigte danach unabhängig Safe-Evidencehash und aktuellen leeren Namespace; [Prüfprotokoll](../orchestration/integration-log.md). Externe Auth/Secret/Provider bleiben synthetisch, keine vollständige Backend-/Produktionsabnahme. Diese abgeschlossene Freigabe ist kein Dauerauftrag für neue Läufe oder eine A5-/Storagefreigabe.

## Nachweis und Aufräumen

Snapshot-SHA, Compose-Projekt, Prüfkommandos, Resultate und Grenzen im [Integrationslog](../orchestration/integration-log.md) bzw. Worker-Handoff festhalten. Container nach dem Test stoppen; Volumes nur nach geprüftem Bedarf und explizit im eigenen Projekt entfernen. Kein `docker system prune`, kein pauschales Containerstoppen und kein fremdes `compose down`.

Aus demselben Snapshot-Verzeichnis auf kube1:

```sh
NOTEBOOK_TEST_PORT=18190 docker compose --env-file /dev/null \
  -p notebook-test-main -f compose.yaml -f compose.test.yaml down
```

`down` ohne `-v` erhält etwaige Volumes. Snapshot zunächst als Prüfnachweis behalten. Auf dem Laptop waren bei der Umstellung beide `notebook-foundation-verify`-Container bereits gestoppt; fremde laufende Container wurden nicht verändert. Alte native PostgreSQL-Workerumgebungen sind keine Docker-Migration: ihr Status bleibt im jeweiligen Handoff, neue schwere Tests werden remote ausgeführt.
