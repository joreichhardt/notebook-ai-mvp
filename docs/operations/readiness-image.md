# Native Readiness-Imageprüfung — vorbereitet, noch nicht ausgeführt

## Umfang und aktueller Stand

`tests/security/readiness_image/build_image.py` bereitet einen begrenzten Legacy-
Build samt anschließender Imageprüfung vor. `verify_image.py` prüft alternativ
**ein bereits gebautes, explizit eigenes API-Image**, nicht das Produkt oder eine
Veröffentlichung.
Lokale Tests verwenden einen falschen Docker-Adapter; sie bauen/starten keine
Container. Ein echter gestempelter Imagebau, kube1-Lauf und vollständige
Dependency-/Worker-Readiness sind weiterhin **UNRUN**.

Die separat implementierten Read-only-Adapter stehen im
[Readinessvertrag](../architecture/contracts.md#readinessrelease-contract-fail-closed-foundation).
Startup installiert sie nicht. Es fehlen weiterhin ein wirklicher beaufsichtigter
Worker/Heartbeat und dessen Betriebsverdrahtung. Keine gesund behaupteten Marker,
Dummy-Probes oder Liveness-Aliasse ergänzen, um diese Sperre zu umgehen.

## Vor einem nativen Lauf

Main koordiniert den exklusiven schweren Slot nach aktuellem Orca-/kube1-Abgleich.
Bestehende fremde Dienste und Worker erhalten; A5/B5 nicht wiederholen. Es gelten
[kube1-Testregeln](testing-kube1.md). Der versehentlich angelegte separate
Projektroot `/home/jre/orca/projects/gemini-nb-clone` ist **keine Quelle**.

1. Source-/Dockerfile-/Wrapperreview auf einem vollständigen committeten
   Integrations-SHA. Nur dessen `git archive`, keine Arbeitskopie oder `.pi/`,
   übertragen. Archivhash/Dateimanifest getrennt erhalten.
2. `build_image.py` muss aus dem exakten Snapshot unter der privaten Wurzel
   `/home/jre/notebook-tests/readiness/<fullSHA>` stammen. Main prüft zuvor dessen
   vollständiges Archiv/Dateimanifest unabhängig. Der Buildoperator hält denselben
   retained-inode Lock bis nach der Runtimeprüfung, verlangt einen freien schweren
   Slot und freie Kapazität und nutzt ausschließlich eine eigene leere private
   Dockerclient-Konfiguration; keine fremden Registry-Credentials oder Bootstrap-
   Umgebung. Die drei Dockerfile-Basisreferenzen bleiben digest-gepinnt.
   Kein privilegierter Hilfsbuilder und keine globale Daemon-/Cgroupänderung.
3. Der vertrauenswürdige Build setzt den nicht geheimen Buildarg
   `NOTEBOOK_RELEASE_SHA=<vollständiger Source-SHA>` und die zusätzlichen
   Image-Labels `com.docker.compose.project=notebook-test-readiness` und
   `notebook.test.sha=<derselbe SHA>`. Der Dockerfile setzt das OCI-Revisionslabel
   und die feste Stempeldatei. Image-ID, Build-/Archivhash und Sourcebindung
   nachprüfen, nicht durch nachträgliches Metadatenfälschen herstellen.
4. Ausschließlich die finale lokale Image-ID `sha256:<64hex>` verwenden, kein
   Tag. Noch kein Registry-Push, Scanpass oder Release aus einem Build ableiten.
   Keine Runtime-Credentials, Produktionsmounts oder Bootstrap-Secrets übergeben.
5. Hostname `kube1`, normaler Testoperator UID1001 und cgroupv2 müssen tatsächlich
   zutreffen. Bei anderen Voraussetzungen abbrechen, nicht die Guards entfernen.

Erst anschließend, aus dem exakten archivierten Snapshot **auf kube1**:

```sh
python3 -I tests/security/readiness_image/verify_image.py \
  --image 'sha256:<geprüfte-64hex-image-id>' \
  --release '<vollständiger-geprüfter-source-sha>'
```

Für den kombinierten begrenzten Build-/Prüflauf statt des vorgebauten Images:

```sh
python3 -I -B tests/security/readiness_image/build_image.py \
  --release '<vollständiger-geprüfter-source-sha>'
```

Platzhalter sind absichtlich nicht ausführbar. Keine Secrets als Argumente.
Der Buildoperator setzt selbst die Source-/Projektlabels, erzeugt keinen Tag,
keinen Push und nimmt nur die finale `--iidfile`-Image-ID für die Prüfung.

## Begrenzter Build, kein privilegierter Ersatzbuilder

Nur `DOCKER_BUILDKIT=0` mit tatsächlich geprüften Legacy-Limits:1CPU/1GiB/
kein zusätzlicher Swap. `--no-cache` erzwingt den realen effektiven Limitcheck
vor dem Dependencyinstall. Dependencies laufen nun als UID/GID10001, CapEff0,
Seccomp2, mit hard/soft RLIMIT_NPROC128 und NOFILE1024; der Checker muss das
native cgroupv2-CPU-/RAM-/Swap-Profil und die rlimits bestätigen. **NPROC ist ein
Real-UID-weites Limit, keine containerbezogene cgroup-PID-Garantie**. Fremde
UID10001-Prozesse werden nicht verändert und können höchstens den Build früher
scheitern lassen. Root läuft nur für festes `chown` und stdlib-Metadatenstempeln,
nicht für heruntergeladenen Dependencycode. Standardbuilds bleiben ohne den
zusätzlichen test-only Abortcheck möglich; kein Test-Ready-Schalter entsteht.

Eigene temporäre Bridge nur für öffentliche Dependencydownloads, keine Ports,
Mounts oder Produktionszugänge. `--force-rm` und unabhängige Abwesenheitschecks
aller aus dem Buildlog erfassten Zwischencontainer vor der Imageprüfung.
Bridge nur nach exaktem ID-/Label-/SHA-/Leerheitscheck löschen. Buildtimeout,
uneindeutiges Cleanup oder fehlender effektiver Limitmarker bedeutet Fehler,
nie einen zweiten blinden Lauf. `.build-<SHA>` samt0600-Log/JSON bleibt erhalten;
Zwischenlayer/finales Image nicht durch globales Prune entfernen. Runtime folgt
erst nach erfolgreichen Buildschritten, nachgewiesener Zwischencontainerabwesenheit
und wieder leerem eigenen Namensraum, unter demselben Lock.

## Isolation und tatsächliche Prüfungen des Verifiers

- Expliziter Docker-Socket `/var/run/docker.sock`; kein globaler Kontextwechsel.
  Das ist vorhandener Testbetrieb, **kein Rootless-Nachweis**.
- Eigener Namensraum `notebook-test-readiness`, feste private Wurzel
  `/home/jre/notebook-tests/readiness` und retained-inode `.lock`/nonblocking
  flock. Alle anderen Writer müssen den Main-Slot respektieren; kein verteilter
  Lock und keine allgemeine Docker-Daemon-Sperre.
- Refuse bei vorhandenen eigenen Containern/Netzen/Volumes, fremden Image-Labels,
  falscher Revision, Image-USER≠10001:10001, impliziten Volumes oder eingebauter
  NOTEBOOK-/BWS-Runtimekonfiguration. Env-Werte werden nicht ausgegeben.
- Ein Container ohne Netz/Hostports/Volumes/Binds, nur begrenztes32MiB `/tmp`,
  RootFS readonly, USER10001:10001, CapDropALL, no-new-privileges,
  private cgroup-Namespace,1CPU/512MiB/kein zusätzlicher Swap/64PIDs.
  Inspect prüft die angeforderten Einstellungen. Der interne stdlib-Smoke prüft
  **zusätzlich effektive cgroupv2-CPU/RAM/Swap/PID-Werte**, tmpfs-Größe,
  Rootmount-ro, UID/GID, CapEff0, NoNewPrivs1 und Seccomp2.
- Nur containerinternes Loopback-HTTP per `docker exec`, keine Proxy-/Auth-/DB-
  Anbindung: `/api/health/live` muss200 sein und das unvollständig verdrahtete
  `/api/health/ready` weiterhin503/not_ready/no-store. Die feste Image-Stempeldatei
  muss zum geprüften SHA passen, obwohl die Runtime-Umgebung einen abweichenden
  synthetischen `NOTEBOOK_RELEASE_SHA` enthält. Kein BWS-Bootstrap im Container.
- Cleanup nur nach erneutem Label-/Image-/Sourcevergleich und ausschließlich mit
  der exakten inspizierten Container-ID. Kein globales Prune/Down, kein Löschen
  fremder Ressourcen. Image und Evidence bleiben erhalten.

## Nachweise, Fehler und Grenzen

Evidence ausschließlich in neuem `.runtime-<fullSHA>/results.json`, Verzeichnis0700,
Datei0600: Quelle/Image-ID, Operatorhash, UTC, feste Ergebnisbooleans und Cleanup.
Derselbe Evidencepfad wird **nie** überschrieben; auch der Lock-Inode bleibt.
Nur Operator-Exit0 **und** passed/cleanup true **und** eine unabhängige aktuelle
0/0/0-Nachprüfung akzeptieren. Ein fehlgeschlagener/unklarer Docker-Aufruf wird
nicht blind wiederholt. Bei Unterbrechung zuerst aktuellen eigenen Zustand und
Evidence prüfen; keine neue Testinstanz oder Fremd-Cleanup starten.

Die lokale Fakeprüfung ist keine echte Runtime-/Hardening-Abnahme. Selbst ein
später bestandener nativer Lauf bestätigt nur das API-Image und seine absichtlich
unbereite Konfiguration, **nicht** Auth/DB/Storage/Worker-Funktion, gesamte UI,
Rootless, Produktions-Secrets, Scans, Backup/Restore, Egress-Quieszenz, Access,
Tunnelaktivierung oder Produktionsfreigabe. Die getrennten Releasegates bleiben
vollständig bestehen.
