# Docker-Tests auf kube1

## Verbindlicher Ort und Isolation

Docker-Builds, Compose-Teststacks und containerisierte Integrationstests laufen auf **kube1**, nicht auf dem Laptop. Leichte Entwicklungsprüfungen können lokal bleiben. Dies ist Testbetrieb, kein Produktionsrollout und keine Freigabe von Upload/Chat.

SSH-Zugang `ssh kube1` wurde nichtinteraktiv geprüft: Docker 29.1.3, Compose 2.40.3+ds1-0ubuntu1, 12 logische CPUs, rund 31 GiB RAM. Diese Bestandsaufnahme ist kein dauerhafter Kapazitätsnachweis; vor neuen Tests Last und Ports prüfen. Der vorhandene Docker-Daemon ist kein Rootless-Nachweis. Bestehenden Daemon und produktive Dienste unverändert lassen.

| Bereich | Ausschließlich eigener Serverpfad | Compose-Projekt | Host-Port |
| --- | --- | --- | --- |
| Main Foundation | `/home/jre/notebook-tests/main/<commit>` | `notebook-test-main` | `127.0.0.1:18190` |
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

### Zusätzliche synthetische Test-Logins

Bei SCRAM mit rollenbezogenen `0600`-pgpass-Dateien übernimmt ein nachträglich angelegter Login nicht das Passwort oder die pgpass-Zeile des Setup-Admins. Solche Fixtures erzeugen ein eigenes zufälliges Testpasswort, übergeben es ohne Ausgabe an die Rolle und verwenden eine **ab Erstellung `0600`** geschützte, korrekt escapte eigene pgpass-Datei. Die SQLAlchemy-URL enthält kein Passwort; `connect_args.passfile` wählt die Datei explizit. Direkten `current_user`, Dateirechte und Rollenbeschränkungen prüfen; Datei/Engine/Rolle auch im Fehlerpfad bereinigen. Kein `trust`-Fallback und keine Produktionszugänge.

Main korrigiert so ausschließlich `test_fresh_admission_check_and_python_storage_helpers`; echte kube1-RED/GREEN-/Full-Suite-Evidenz wird erst nach dem Worker-Rerun im Integrationslog eingetragen. Für den parametrisierten DDL-Aufruf nutzt die Fixture den offiziellen [Psycopg-ClientCursor](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#client-side-binding-cursors), da PostgreSQL DDL nicht serverseitig mit `$1` bindet. Offizielle Quellreferenz: `psycopg/psycopg`, `docs/advanced/cursors.rst` (Dokumentationswebsite beim Prüfen HTTP403, offizielles GitHub-Original erfolgreich gelesen).

## Nachweis und Aufräumen

Snapshot-SHA, Compose-Projekt, Prüfkommandos, Resultate und Grenzen im [Integrationslog](../orchestration/integration-log.md) bzw. Worker-Handoff festhalten. Container nach dem Test stoppen; Volumes nur nach geprüftem Bedarf und explizit im eigenen Projekt entfernen. Kein `docker system prune`, kein pauschales Containerstoppen und kein fremdes `compose down`.

Aus demselben Snapshot-Verzeichnis auf kube1:

```sh
NOTEBOOK_TEST_PORT=18190 docker compose --env-file /dev/null \
  -p notebook-test-main -f compose.yaml -f compose.test.yaml down
```

`down` ohne `-v` erhält etwaige Volumes. Snapshot zunächst als Prüfnachweis behalten. Auf dem Laptop waren bei der Umstellung beide `notebook-foundation-verify`-Container bereits gestoppt; fremde laufende Container wurden nicht verändert. Alte native PostgreSQL-Workerumgebungen sind keine Docker-Migration: ihr Status bleibt im jeweiligen Handoff, neue schwere Tests werden remote ausgeführt.
