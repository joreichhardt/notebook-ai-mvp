# Native Readiness-Imageprüfung — vorbereitet, noch nicht ausgeführt

## Umfang und aktueller Stand

`tests/security/readiness_image/verify_image.py` prüft **ein bereits gebautes,
explizit eigenes API-Image**, nicht das Produkt oder eine Veröffentlichung.
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
2. Zuerst einen reproduzierbaren, ressourcenbegrenzten API-Imagebau auf kube1
   vorbereiten/prüfen. **Dieser Operator baut oder pullt selbst nichts**;
   unkontrollierte Builderlast oder ein privilegierter Hilfsbuilder sind kein
   Ersatz. Ein solches Buildpaket ist noch nicht geliefert.
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

Platzhalter sind absichtlich nicht ausführbar. Keine Secrets als Argumente.

## Isolation und tatsächliche Prüfungen des Operators

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
