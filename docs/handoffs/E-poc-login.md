# E · MVP-Zwischenstufe Login/Workspace: lokaler Kandidat, reale Inbetriebnahme offen

**Aktuelle Bezeichnung: MVP.** Der Nutzer hat das Vorhaben von PoC in MVP umbenannt. Dieser Login-/Workspace-Slice ist eine Zwischenstufe, noch nicht das vollständige MVP; Sicherheits-, Funktions- und Abnahmekriterien bleiben unverändert. Der technische Dateiname `E-poc-login.md` und andere `poc`-Kennungen bleiben für stabile Verweise bestehen. Die folgende Prüfhistorie behält ihre damaligen Bezeichnungen und Aussagen.

## Identitätsdelta · Repository/GHCR auf MVP umgestellt (lokal eingefroren)

Arbeitsbasis dieses engen Folgeslices: `0913410b19f81b778b7d18b3671348d97529d4c3`. Neuer ausschließlicher Appname: `joreichhardt/notebook-ai-mvp`, Imagepräfix `ghcr.io/joreichhardt/notebook-ai-mvp-{api,frontend}`. Geändert sind nur CI-Repositorybedingung/Registrypräfix/Provenance-Subjectnamen/Anzeigename sowie beide exakten GHCR-Allowlisten und deren Testfixtures. Alte oder gemischte PoC-Namespaces werden abgelehnt, keine Übergangskompatibilität. Vendorpins, Digestpflicht, Profile, APIpfade, Interlock, private App-/BWS-Namen und technische `poc`-Dateinamen bleiben unverändert. Die drei Produktions-/Konfigurationsdateien wurden bytegenau gegen HEAD mit ausschließlich diesen Stringersetzungen verglichen.

**GitHub-Umbenennung, Push und Public-Schaltung liegen beim Parent.** Dieser Worker hat keine GitHub-/Credential-/Infrastrukturaktion, keinen Commit und kein Staging ausgeführt. Neue Nutzernamenshinweise oben und alle früheren Belege bleiben erhalten; die folgende Historie wird nicht rückwirkend umgeschrieben.

Neue exklusive Belege: `.pi/poc-login/mvp-identity/` (0700, Dateien0600), unveränderter Originalguard `4d1e74bb…`, Precollection-I/O-/DB-/SQLAlchemy-Sperren, Executorjoin, `env -i`, logischer `.venv/bin/python -B`, deaktivierte Plugin-Autoload-/Pytest-Caches.

- `namespace-red.{json,log}`: **4 echte FAIL /4 PASS**,20 bestandene/4 fehlgeschlagene Phasen,0 externe IO/0 Executors. Altes Produktionsverhalten akzeptierte beide alten Imagerollen und verweigerte das neue MVP-Paar, jeweils an beiden Helpergrenzen. Die vier gemischten Fälle waren bereits wegen der jeweils anderen MVP-Rolle abgelehnt; deren frühes PASS ist kein eigenständiger Altrollen-Ablehnungsbeweis.
- `namespace-green.{json,log}`: **8 PASS /24 bestandene Phasen /0 IO/0 Executors** nach minimaler Präfixänderung. Positive neue Paare plus Ablehnung alter API-, Frontend- und beider Rollen.
- `combined.{json,log}`: **140 PASS /420 bestandene Phasen /0 IO/0 Executors**,2,34s, genau ein abschließender aktueller Sechs-Dateien-Lauf. App9/Setup29/Runtime8/Publication19/Ops34/Deploy41; sechs zusätzliche Namespacefälle gegenüber134. Keine alten190-/Nativefälle, Skips oder xfails. Zehn relevante Quellen-/Guardhashes und Modi vor/nach Combined identisch.
- Scoped Ruff default und explizit E,F,I,B,BLE auf vier Pythondateien PASS; Formatcheck4PASS, alle explizit `--no-cache`. `yamllint .github/workflows/ci.yml` PASS. **actionlint nicht verfügbar** in PATH bzw. den gezielt geprüften lokalen Standardpfaden; nicht installiert/ausgeführt. YAMLlint ersetzt weder Actions-Semantikprüfung noch tatsächliche CI. Rohlogs: `ruff.log`, `ruff-extra.log`, `format.log`, `yamllint.log`.
- Rohadvisories unverändert gesichert in `lens-advisories.json`: `cq:6fa9986966b5` und `cq:7aa63c2cc4c4`, tree-sitter/python-path-traversal an `test_poc_ops.py:198/212`. Beide betreffen unveränderte Testbodies, die synthetische Archive unter `tmp_path` schreiben, nicht neue Pfadverarbeitung. Keine Marks/Suppressionen/Scannerpolicyänderungen; kein blanket-LSP-/full-CI-PASS behauptet. Fremde Wizarddiagnostik wurde ausschließlich an Parent gemeldet, nicht bearbeitet.

| Eingefrorene Identitätsquelle | SHA256 | Modus |
| --- | --- | --- |
| `.github/workflows/ci.yml` | `03b58e9f5a9fdda076a8c14871411be62cf944157ec700fca1f5c7e10be18a40` | 0664 |
| `deploy/private-ops/notebook-poc-deploy.py` | `18e0cb79fd1cd8a9e10d5086038349d3f7e87ca5fd9eb55ec4e9e46bd1dd3a44` | 0755 |
| `deploy/poc_publication.py` | `0b9beec88bc3bb2065b524eb412ef118721308b0aa03ecb337d9727f2eedd984` | 0664 |
| `deploy/tests/test_poc_ops.py` | `a085204fe082912a0d1d4b49ac27821707d8a385610d37717a28559ed9a56c67` | 0664 |
| `deploy/tests/test_poc_publication.py` | `a239964f5bd2d07c2bbc859a5c0232aa3c438772ebb7af2e999a334ec8324364` | 0664 |

**Parent-Update / separate Archivlücke:** Parent meldet tatsächliche GitHub-Umbenennung und Public-Schaltung abgeschlossen (stabiles Repository1459167079, `joreichhardt/notebook-ai-mvp`), Deployrepository weiterhin privat, noch kein Sourcepush. Dies ist Parent-Bericht, keine Worker-GitHubprüfung. Read-only lokal bestätigt: `docker/nginx.poc.conf` ist untracked, obwohl `compose.poc.yaml:124` die Datei bind-mountet; im aktuellen HEAD-Baum fehlt sie. Die Runtimeprüfung liest die dirty lokale Datei direkt (`test_poc_runtime.py:186`), während die Composeprüfung nur den erwarteten Mountstring prüft. Die Deployfixture packt nur `compose.poc.yaml` in ihr synthetisches Archiv (`test_poc_deploy.py:35–42`) und mockt Compose; der Ops-Extraktionstest nutzt ebenfalls nur synthetische Composebytes. **140PASS beweist daher keine vollständige Releasearchiv-Closure.** Keine Änderung an nginx, Fixtures oder Scope; Parent übernimmt separate Closureprüfung/Review/Integration der fehlenden Datei.

Nächster Schritt: unabhängiger Parentreview/Integration, separate Archiv-Closure und der Parent-owned Sourcepush. Lokale Umbenennung ist kein erfolgreicher Push-/Runtime-Nachweis. **Worker eingefroren; keine weitere Scopeerweiterung.**

Stand: 2026-11-15, Arbeitsbasis `9db1cb0bb2a1fc6b0e7644a6074b1d7903f5a2ec`, Worktree `notebook-integration`. Recovery des Implementierungsworkers nach Harnesslimit; kein fehlgeschlagenes Deployment. **Quellen jetzt eingefroren für Parent-Review, keine Betriebsfreigabe.** Gemeinsamer aktueller Einstieg: [CONTINUE](CONTINUE.md); Anforderungen: [Produktspezifikation](../product-spec.md).

## AKTUELL · P1-Interlock-Fix, erneut eingefroren für Review

Die beiden read-only Reviews des vorigen18-Dateien-Kandidaten sind abgeschlossen: Standards meldete **P1** (CLI-Timeout beendet nicht zwingend den daemonverwalteten Admincontainer; `flock`-Freigabe erlaubte eine zweite Migration), Spec meldete keinen weiteren Defekt. Die Reviewer führten keine Tests und keine unabhängige Hashberechnung aus; Sourcehashes vor/nach deren Review wurden vom Parent geprüft. **Der frühere115-Testlauf war kein unabhängiger Parentreplay.** P1 wurde danach ausschließlich in Helper, eigenen Deploytests und diesem Handoff bearbeitet; Task33/Worker/CI bleiben unverändert.

### Dauerhafte Sperre und Completion-Vertrag

- Unter dem tatsächlichen `deploy.lock` prüft der Helper `HOME/deployment-pending.json` **vor Extraktion, Secretabruf und Compose**. Jeder existierende Knoten (auch leere/kaputte Datei, Verzeichnis, Symlink) blockiert; kein Parser deutet einen vermeintlich erledigten Zustand als Freigabe.
- Nach Config/Pull, aber **vor dem ersten DB/Auth-`up`**, wird der Marker exklusiv mit `O_EXCL|O_NOFOLLOW`,0600 erzeugt, geschrieben, geflusht, dateisynchronisiert und anschließend das private HOME-Verzeichnis synchronisiert. Alle Schritte müssen vor dem ersten Start zurückkehren. Schreib-/Syncfehler hinterlassen den vorhandenen Marker; keine automatische Bereinigung der Sperre.
- Der Marker enthält ausschließlich Commit, Phase `service-changes-pending`, beide eindeutigen eigenen Container-Namen und den privaten Consumerpfad. Namen stammen aus dem eigenen eindeutigen Releaseverzeichnis und werden als `--name` an die beiden Adminaufrufe übergeben. Keine Credentialwerte, SQL oder Rohfehler.
- **Vor jedem Adminlaunch wird dessen Ausgang als unbekannt behandelt.** Nur normaler Exit0 des vertrauenswürdigen gepinnten Providers für den synchron angehängten `compose run --rm --no-deps --name …` zählt als bestätigter erfolgreicher Adminabschluss. Kein `--detach`, kein Background-Exec, kein ignorierter Returncode; `--rm` behält seine Restart-Policy-Override-Semantik. Timeout, Transportfehler und Nonzero sind unbekannt, **nicht** beendet. Containerabwesenheit wird niemals als Erfolgsbeweis ausgewertet.
- Bei unbekanntem Adminausgang bleiben die geschützten Adminfiles erhalten. Ihre Löschung ist weder Cancellation noch Widerruf bereits geladener DBauthority. Vor Adminlaunch bleiben bisherige Cleanupregeln erhalten; nach bestätigtem Abschluss werden Adminfiles weiterhin vor normalem APIstart entfernt, höchstens ein Cleanupversuch.
- Erst nach **beiden** bestätigten Adminabschlüssen, verifiziertem Admincleanup, erfolgreichem letztem kontrolliertem `up` und geschriebenem lokalen `started.json` wird der Marker entfernt und das HOME-Verzeichnis erneut synchronisiert. Jeder frühere Fehler hält ihn fest. Dies bescheinigt keine Appgesundheit, Veröffentlichung oder generelle Quieszenz; normale langlebige Dienste bleiben beabsichtigt aktiv.
- Unlinkfehler melden HELD mit vorhandenem Marker. Scheitert die Verzeichnissynchronisierung **nach** erfolgreichem Unlink, wird HELD/Durabilität unbekannt gemeldet, nicht fälschlich „Marker garantiert vorhanden“. Dieser Pfad ist nur nach bestätigtem Adminabschluss erreichbar; unbekannte Migrationen gelangen niemals dorthin.

**Vertrags-/Quellenbasis:** Parent hat den synchronen Wait-/Exitcodevertrag ausdrücklich genehmigt; Referenz ist [Docker Compose run](https://docs.docker.com/reference/cli/docker/compose/run/) (kein neuer Dokumentations-Netzabruf in diesem Slice). `docker/poc-admin.py` ruft Alembic `command.upgrade` synchron auf; `backend/migrations/env.py` verlässt den Transaktionskontext vor Rückkehr. Die Rollenaktivierung verlässt den psycopg-Verbindungs-/Transaktionskontext vor Exit0. Es gibt keine neue Receipt-Registry oder angenommene Callbackbestätigung. **Das tatsächliche Verhalten des gepinnten Providers unter dem zukünftigen Rootless-Appactor bleibt nativ unbewiesen.**

**Operatorrecovery bleibt absichtlich manuell und separat freizugeben:** kein `--force`, Reset-/Recoveryendpoint, automatischer Retry, Stop/Kill, Rollback oder Daemonwatcher. Vor einer Freigabe müssen der tatsächliche Zustand der exakten eigenen Container und der DB-/Migrationszustand geprüft sein; kein blindes Entfernen des Markers. Dieser Slice stellt keinen Resetbefehl bereit. Auch nach Hostneustart darf ein verbliebener Marker nicht als veraltete Datei weggeräumt werden.

### Neue tatsächliche lokale Belege

Neue exklusive Artefakte `.pi/poc-login/recovery-interlock/` (0700/0600), unveränderter starker Guard4d1e74bb, gleiche bereinigte Umgebung wie unten. Alle alten115-/RED-/Fixturebelege bleiben unverändert.

| Beleg | Ergebnis | Grenze |
| --- | --- | --- |
| `countermodel-red.{json,log}` | **2 echte FAIL**,4 Setup/Teardown PASS,0 IO/0 Executors,0,32s | Vor Produktionsfix: tatsächlicher Helper/`run()`-Fehlerpfad und echter lokaler flock; unabhängiger Fake-Daemon behält laufenden Admin/geladene Authority nach CLI-Timeout bzw. Transportfehler, zweite Invocation erreicht Compose. Keine API-Abwesenheits-/Fixture-REDs. |
| `interlock-green.{json,log}` | **41 PASS /123 bestandene Phasen /0 IO/0 Executors**,0,73s | Eigene Deploytestdatei: neun Daemon-Gegenmodelle (Initial-up/beide Adminaktionen × Timeout/Transport/Nonzero), bestehende/kaputte/Symlinkmarker, Marker-Sync-Reihenfolge/-Fehler, Completion-Unlink-/Sync-/Recordfehler und bestehende Kontrollen. Erweiterte Fälle nach Fix erstmals GREEN, nicht rückwirkend als RED behauptet. |
| `combined.{json,log}` | **134 PASS /402 bestandene Phasen /0 IO/0 Executors**,1,92s | Ein gemeinsamer aktueller Lauf aller sechs neuen PoC-Testdateien nach Formatierung. Keine alten Worker-/Nativefälle, Skips oder xfails. |

Aufteilung: App9/Setup29/Runtime8/Publication16/Ops31/Deploy41. Gegenüber115 sind19 Fälle hinzugekommen; die alte Migrationsfehler-Cleanup-Erwartung wurde ausdrücklich auf **unbekannt ⇒ Files behalten/Sperre halten** korrigiert und entsprechend benannt, nicht übersprungen. Alle23 Source-/Guard-/geparkten Inventarzeilen waren vor/nach Combined byte-/modegleich; der Handoff wurde anschließend aktualisiert. `combined.json` SHA256: `219857c43bbd6a0123fc477acd566525044d8bf7324d005ece3e2fc47f48a5c0`.

Default-Ruff elf PoC-Dateien PASS; explizit E,F,I,B,BLE Helper/Deploytests PASS; Formatcheck beider Dateien PASS, alle mit `--no-cache`. Belege im neuen Interlockverzeichnis `ruff-final.log`, `ruff-extra.log`, `format-final.log`. Kein neues Suppression-/Guard-/Policy-Mark, keine full-CI-/Nativefreigabe. Erneutes unabhängiges Review der finalen Bytes noch offen.

## Historischer erster Recovery-Slice (vor P1-Review)

Nur `deploy/private-ops/notebook-poc-deploy.py`, neues `deploy/tests/test_poc_deploy.py` und dieser Handoff wurden seit Recovery geändert/angelegt. Bestehendes `deploy/tests/test_poc_ops.py` bleibt bytegleich; es hatte bereits 31 dedizierte Helperfälle. Die frühere Aussage „keine dedizierten Tests“ war falsch. Neu sind 22 Orchestrierungs-/Descriptorfälle, keine Umbenennung alter Tests.

Zwei nach echten REDs freigegebene Helperkorrekturen:

- `trusted_file` prüft **vor dem Lesen am geöffneten Descriptor** reguläre Datei, Root-Eigentümer und fehlende Gruppen-/Welt-Schreibrechte. Vorher wurde dort nur der Dateityp geprüft. Bestehende Elternpfad-/Symlink-, Größen- und Digestprüfungen bleiben erhalten.
- Damals wurden temporäre One-shot-Admincredentials nach dem letzten Adminverbraucher vor normalem API-Start und per `finally` auch bei Migrationsfehlern entfernt. **Diese unbedingte Fehlerbereinigung ist durch den obigen P1-Fix ersetzt: unbekannter Adminausgang behält die Dateien.** Nur der fest abgeleitete eigene `consumers/admin`-Pfad wird über `podman unshare /usr/bin/rm -r -- …` entfernt. `lstat` prüft den Knoten und anschließend tatsächliche Abwesenheit. **Höchstens ein Cleanupversuch**, kein stiller Retry. Cleanupfehler ergeben HELD ohne Erfolgsmarker; übrige Consumerdateien und partieller eigener Zustand bleiben zur Inspektion erhalten.

Kein automatischer Rollback. HELD bedeutet weder „alte Version gesund“ noch „Dienste gestoppt“, „Datenbank zurückgesetzt“ oder „Credentials sicher entfernt“, wenn gerade Cleanup fehlgeschlagen ist. Auch `started.json` bedeutet nur erfolgreiche Kommandorückgaben, ausdrücklich **keine** Readiness-/Login-/Publikationsabnahme. Es wurde kein neuer Verifikationsdienst eingeführt.

## Historische lokale Nachweise des115-Test-Kandidaten

Alle Recovery-Tests: synthetische Dateien, gefälschte Hostidentität/Namespace-/Prozessgrenzen, echte Helperkontrollflüsse; keine Container, SSH, BWS, SMTP oder DB. Originaler unveränderter starker Precollection-/Audit-/DB-/SQLAlchemy-/Executorjoin-Guard:

`4d1e74bbed072bf7a8209e8d5e4ed718e7d8f1f6c7a0161d0da5f0bddc8c3033` (`.pi/task6-supervisor-lifecycle/guard.py`).

Aufrufumgebung: `env -i`, explizites `PYTHONPATH` mit diesem Guard, `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, logischer `.venv/bin/python -B`, `-p guard -p no:cacheprovider --lifecycle-evidence=<neue Datei>`. Kein Interpreter-`.resolve()`, keine alten S1/S2a/S2b-/Nativefälle. Alle neuen Artefakte unter `.pi/poc-login/recovery-deploy/`, Verzeichnis0700/Dateien0600, exklusive Namen; historische Artefakte unverändert.

| Beleg | Tatsächliches Ergebnis | Einordnung |
| --- | --- | --- |
| `red.{json,log}` | 12 PASS / 6 FAIL, 48 bestandene / 6 fehlgeschlagene Phasen, 0 externe IO / 0 Executors | Vier echte Admincleanup-REDs. Zwei Descriptorfälle erreichten DID NOT RAISE, aber deren neue globale `os.fstat`-Fixture beschädigte Pytest-Fehlerformatierung (`st_size` fehlte): **kein sauberer Descriptor-RED**. |
| `descriptor-red.{json,log}` | 2 FAIL, 4 Setup/Teardown PASS, 0 IO / 0 Executors | Nach separat freigegebener Fixturekorrektur nur diese zwei Fälle ausgeführt: vollständiges `os.stat_result`, nur modulinterne OS-Kopie überschrieben. Zwei echte saubere DID-NOT-RAISE-REDs vor Produktionsfix. |
| `green.{json,log}` | 22 PASS / 66 bestandene Phasen / 0 IO / 0 Executors | Nach freigegebenen Fixes; zusätzliche Cleanupfehler-/unvollständige-Cleanup-/partielle-Adminstagingkontrollen. |
| `combined.{json,log}` | **115 PASS / 345 bestandene Phasen / 0 IO / 0 Executors**, 1,70s | Ein gemeinsamer Lauf der sechs aktuellen neuen PoC-Testdateien nach Formatierung. Keine Skips/xfails; keine Executorarbeit erzeugt, daher kein behaupteter realer Future-/Containerbeweis. |

Combined-Aufteilung: App9, Setup29, Runtime8, Publication16, bestehende Ops31, neue Deploy22. 22 Source-/Guard-/geparkte Hashes und Modi vor/nach Combined identisch (`before-combined.json`, `after-combined.json`). Combined-JSON SHA256: `9b9997962b10160711cd6f03e9544cf400d91f512f246b3c27dff352d6172a4e`.

Orchestrierung prüft feste Pfade/Akteur/Projekt/18191/Approval-/Archiv-/Providerbindungen, vier Digestrollen über Approval und archivierte Composequelle, Rootless-Failclosed, vollständige Credentialvalidierung und geschlossene Consumerstagingreihenfolge vor Compose, exakte Admin-Kommandos/Mounts, bereinigte Childumgebung, Fetch-/Validierungsfehler ohne Serviceänderungen, Erfolg und injizierte Fehler ohne falschen Rollback/Erfolgsmarker. **Host-/Prozessgrenzen sind Fakes**; tatsächliche Rootless-UID-Abbildung und reale Entfernung müssen auf kube1 separat bewiesen werden. Kein geheimer Wert wird als Argument oder Ergebnis ausgegeben.

### Historische Belege nicht vermischen

- Frühere Setup17/App9/Publication16 Missing-Module-REDs beweisen API-Abwesenheit, nicht sämtliche Verhaltensfehler.
- Historische Setup16PASS/1FAIL war die feste Zukunftszeit-JWT-Fixture; Runtime7PASS/1FAIL verbot irrtümlich den nicht geheimen Rollennamen `service_role`. Deren ursprüngliche Dateien mit „green“ im Namen bleiben unverändert und gelten nicht nachträglich als PASS.
- Historische Setup23PASS und Runtime+Publication24PASS stammen aus anderen Zwischenständen. Die24 umfassen die Publicationfälle bereits.
- Worker `setup-ops-green` vor Timeout:60PASS mit dem **schwächeren historischen** `.pi/poc-login/poc_test_guard.py`. Kein Ersatz für aktuelle starke Abnahme.
- Parent unabhängig vor Recovery: vier Dateien62PASS/186Phasen/0IO/0Executors, **ohne Ops**. Initialer Parentlauncherfehler war Systempython nach versehentlichem Venv-Symlink-Resolve, vor Collection; kein Applikationsfehler.
- Aktuelle115 sind eine eigene gemeinsame Ausführung, **nicht** eine Summe unabhängiger historischer Nachweise. Parentreview/-replay der finalen Bytes bleibt offen.

## Diagnosehistorie und aktuelle eingefrorene Identitäten

- Default `ruff check --no-cache` auf elf PoC-Pythondateien: PASS (`ruff-final.log`).
- Explizit `ruff check --no-cache --select E,F,I,B,BLE` auf Helper/neuem Test: PASS (`ruff-extra.log`).
- `ruff format --no-cache --check` auf Helper/neuem Test:2 unverändert/PASS (`format-final.log`), vorher beide mit explizitem `--no-cache` formatiert.
- `git diff --check`: PASS; Index leer. Keine neuen Suppressions/Marks/Guardänderungen, kein full-CI-/blanket-LSP-PASS behauptet. Automatische Tool-Pythonmeldungen sind nicht unabhängige vollständige Diagnostik. Historische automatische Rufffixes hatten unbekannte Cacheflags; alle hier selbst ausgeführten Ruffbefehle enthalten `--no-cache`.

| Datei | Finaler SHA256 |
| --- | --- |
| `deploy/private-ops/notebook-poc-deploy.py` | `27f65c8fe94ddb1e316384f079f03497793aac262411c18136ba54c45b0d58f0` |
| `deploy/tests/test_poc_deploy.py` | `28879261b260cdf8a5fb4d168934d11b1df4e7a3e237d1f74c6fd5bcd90ef550` |

Von den16 Timeout-Source-Snapshots unterscheidet sich nur der genehmigte Helper. Alle21 historischen Workerartefakte bleiben hash-/modegleich mit der vorher dokumentierten einzigen Guard-Moduskorrektur0664→0600. Geparkte S2b-Quellen bleiben exakt Text`411ca979…`, Supervisor`e93e6bb0…`, Test`da9de84a…`; deren Tests wurden nicht importiert/ausgeführt. DATA-Snapshots wurden niemals ausgeführt. Der überlange erste read-only CONTINUE-Aufruf erzeugte automatisch `/tmp/pi-bash-efdd5608d8543876.log` (67854B, UID1000,0600); unverändert erhalten, keine Bereinigung.

Letzte Operation vor Timeout war am20:32:44Z das Lesen installierter SDK-Signaturen, bestehender Vaultquelle und Mailcow-Runbook. Letzte vorangehende Dateiänderung20:32:33Z: inaktives privates Workflowtemplate. Letzter damaliger Test20:32:09Z:60PASS Setup/Ops. Recovery hat keine dieser alten Aktionen erneut ausgeführt.

## Offen / Parent-owned

1. Review der finalen Quellen und Diagnostik, gemeinsamer Index/CONTINUE/Plan durch Parent, explizite Integration ohne geparkte Workeränderungen. Keine Commits/Staging/Push durch diesen Worker.
2. BWS: Parent hat **ein** eigenes Projekt `notebook-poc-kube1` erstellt; ID nur in privater `.pi/poc-startup-parent/bws-project-bootstrap.json`. Nicht neu erstellen/kein fremdes Projekt übernehmen. Dedizierte Secrets, eng berechtigter Reader/Machine Account, SDK-/Scope-/Lebenszyklusnachweise und geschützte Bereitstellung noch offen; kein globaler Token in Runner/Runtime. Kein neuer SDK-/Mailcowhelper in diesem Recovery-Slice.
3. SMTP-only Appcredential, echte Bestätigungsmail und tatsächlicher Johannes-Ownerbootstrap fehlen. Keine SMTP-/Account-/Admissionaktion durch diesen Worker; keine manuelle Bestätigung. Registrierung bleibt geschlossen, daher kein vollständiger neuer Nutzer-Onboardingnachweis.
4. Root-owned Helper-/Approval-/Archivinstallation, getrennte App-/Runner-User, Rootless-Socket, tatsächliche UID/GID-Abbildung und effektive Ressourcenlimits: Parent-Zielsystemprüfung offen. Parent hat ausschließlich im isolierten Vendor-`id`-Container PG UID100/GID101 für den gepinnten Digest bewiesen; nicht Appactor/DB-/Authreadiness. Providerpfad bekannt, tatsächliche Ownership/Checksumme und exakte `version --short`-Ausgabe unter Appactor noch abzugleichen; kein automatisches Provider-Suchen/Installieren.
5. Privates Workflowtemplate ist keine aktive Deploymentlane; Runner/Sudoers/Repositoryrestriktionen und tatsächliche Reviewergate-Verfügbarkeit noch offen. API-Environment-GET200 mit0Einträgen beweist keine GitHub-Planfunktion.
6. Hosted CI/Scans/SBOM/immutable GHCR-Publikation nicht gelaufen. Bestehende Scannerpolicy-Präzisierung und Task33-Observer/BLE-Konflikt offen; keine Scopeausweitung/Policyabschwächung hier. Vollständige CI nicht grün behaupten.
7. Native DB/Auth/API-/Restart-/Persistenz-/Logout-/Crossuser-/Origin-CSRF-/Secretpermissionabnahme und reale HTTPS-Mailbestätigung fehlen. Produktionsreadiness bleibt503, Documents/Chat aus. Öffentliche Route erst nach separater echter releasegebundener Abnahme; Publikationshelper liefert lediglich einen Vorschlag.
8. Hostneustart/BWS-Ausfall/Rotation/Restore und gegebenenfalls ausdrücklich genehmigter Rollback benötigen eigene reale Nachweise. Runtime-secret-Verzeichnisse dürfen nicht pauschal entfernt werden, solange Verbraucher sie benötigen. Kein Autoupdate-/Quadlet-/Monitoringausbau.

**Nächster Schritt:** Parentreview des eingefrorenen lokalen Slices; erst danach neue eng begrenzte Freigabe für die jeweils abhängigen echten Betriebsschritte. Dieser Handoff erklärt keine Produktionsreife.
