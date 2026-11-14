# Native Readiness-Imageprüfung — isolierter Default503-Lauf bestanden

## Umfang und aktueller Stand

`tests/security/readiness_image/build_image.py` bereitet einen begrenzten Legacy-
Build samt anschließender Imageprüfung vor. `verify_image.py` prüft alternativ
**ein bereits gebautes, explizit eigenes API-Image**, nicht das Produkt oder eine
Veröffentlichung.
Lokale Tests verwenden einen falschen Docker-Adapter; sie bauen/starten keine
Container. **150 lokale Tests /2.31s/Ruff sauber/aktiveLSP6clean** auf7a986f1.
Darauf folgte genau ein nativer begrenzter Build-/Imagelauf auf kube1: **PASS**,
unabhängig read-only nachgeprüft. Vollständige Dependency-/Worker-Readiness und
Ausfall-/Restartabnahme bleiben **UNRUN**. Die positive native Aussage ist
absichtlich Liveness200 plus **Readiness503**, nicht Produktbereitschaft.

## Tatsächlicher nativer Nachweis · 2026-11-14

- Exakter Sourcecommit `7a986f143b845527e93a4ae1fbf7fea893991b79`, ausschließlich
  Integrations-Gitarchiv;243Dateien einschließlich aller Modi/UIDs/Hashes vor und
  nach dem Lauf unabhängig überprüft. Kein uncommitteter/projektfremder Root.
- UTC `2026-11-14T15:36:07.715576+00:00` bis
  `2026-11-14T15:36:34.397918+00:00`, **26.682s**, Build-/Operator-Exit0.
  Effektiver Buildlimitmarker genau einmal, vor dem Dependencyinstall.
- Finale **lokale Image-ID/Configdigest**, nicht Registry-Manifestdigest:
  `sha256:36c331edbb63dda423fbd869d0dca8bb859d9f78b788c8b2134d4e781e3ca88a`.
  USER und beide Source-/OCI-Revisionslabels unabhängig überprüft. Keine
  NOTEBOOK-/BWS-Runtimekonfiguration eingebaut, kein Push/RepoDigest.
- Runtime-Smoke mit tatsächlichem internem HTTP und effektiven Limits erfolgreich:
  live200, defaultready503/no-store, fester Imagestempel trotz widersprüchlichem
  Runtime-Env, nonroot, cgroupv2/RAM/CPU/Swap/PIDs, tmpfs/readonly/CapEff/NNP/Seccomp.
- Alle **16** im Buildlog erfassten Zwischencontainer unabhängig absent bestätigt;
  eigener Runtimecontainer entfernt. Aktueller eigener Namensraum unabhängig
  **0Container/0Netze/0Volumes**, keine laufenden Notebooktestcontainer. Image und
  geschützte Evidence bleiben erhalten; keine fremden Ressourcen angefasst.
- A5/B5-Combined, D4 oder andere Teststacks nicht erneut gestartet.

Remote `/home/jre/notebook-tests/readiness/` enthält `<fullSHA>/` (Source),
`.source-<fullSHA>.{tar.gz,json}`, `.build-<fullSHA>/`, `.runtime-<fullSHA>/` und
`.verified-<fullSHA>.json`. Source-/Evidencewurzeln0700, Logs/JSON/Archiv/Lock0600
und UID1001 unabhängig bestätigt. Main-Nachprüfung war rein lesend hinsichtlich
Docker/Source; nur ihr neuer0600-Nachweis wurde angelegt.

| Artefakt | SHA256 |
|---|---|
| Gitarchiv | `33b6d833445fb83806669b657c112178a1894178cf91e9d3da32a9721fde4dc5` |
| Source-Dateimanifest | `398d4f9144c73f684689c22ec07ec067c80af12a0f2fb2e0dc86f0dd5f89f1f9` |
| Ausgeführter Buildoperator | `2c97b8ec647ce32106ccd355388c4f87843928e0934a7fad4d38b0212340a527` |
| Ausgeführter Runtimeoperator | `ba09878c030809e4dba549d6046583e0bf360625c4b5953d4e30b0973784390e` |
| Buildlog | `dbfd9d7ec9b27139210e118a318b0ee72168e755bdbbf810f9ba64d1a52e76b1` |
| Build-results.json | `57f01bf0503cbb0496fc9a6a9cc3edfe8b64efc864f62d438779e5cf3e33f6e9` |
| Runtime-results.json | `d216bd170495aee8cd9335b531770d486c7221b16ca4696f66e03d7a5c91bd13` |

Die lokale Transferhilfe `.pi/readiness-transfer.py` und
`.pi/readiness-<fullSHA>/source.{tar.gz,json}` erhalten das exakte Paket;
Remote-Manifest/Evidence sind maßgeblich für den bereits abgeschlossenen Lauf.

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
