# Projektdokumentation

## Schnell einsteigen

| Frage | Maßgebliches Dokument |
| --- | --- |
| Was soll das Produkt können? | [Produktspezifikation](product-spec.md) |
| Wie starte und prüfe ich lokal? | [Repository-README](../README.md), [Betriebsgrundlage](operations/foundation.md) |
| Was ist fertig, was läuft, was blockiert? | [Aktueller Aufgabenplan](orchestration/plan.md) |
| Wo setze ich nach einer Pause fort? | [Aktueller Handoff](handoffs/CONTINUE.md), [gesicherter Neustartstand](handoffs/RESTART-PAUSE.md) |
| Welche Prüfungen wurden auf welchem Stand ausgeführt? | [Integrations- und Prüfprotokoll](orchestration/integration-log.md) |

## Architektur und Sicherheit

- [Schnittstellenverträge](architecture/contracts.md): Auth, Rollen, Provider, Dokumente, Jobs und Streaming. Geplante Schnittstellen sind ausdrücklich von implementierten getrennt.
- [Architekturentscheidungen](architecture/decisions.md): Gründe und verbindliche Präzisierungen.
- [Bestandsaufnahme](architecture/inventory.md): ermittelter Infrastrukturstand; kein Ersatz für aktuelle Live-Prüfungen.
- [Sicherheitsregeln und Betriebsgrenzen](../SECURITY.md).
- [F4 Disconnect-/Discovery-Grenze](review/disconnect-boundary.md): integrierte Revalidierung/feste Fehlercodes; begrenzte kube1-Abnahme mit 30 PostgreSQL-/ASGI-HTTP- und 135 fokussierten Fällen, verbleibende Liveprovider-/Transport-/Modelljobgrenzen.
- [Mainreview Quoten0006/B3](review/quota-v6.md): Rollen-/RLS-/Gateprüfung, Altlease-Preflightkorrektur, getrennte Quieszenzgrenze und noch offene kombinierte Callertests.
- [Review des Daten-/Provider-Durchstichs](review/seam-v3.md): Befund, Reproduktion und unabhängige Nachprüfung. Weitere Reviews unter `review/` beziehen sich auf ihre jeweiligen Kandidaten.

## Entwicklung und Betrieb

- [Docker-Tests auf kube1](operations/testing-kube1.md): verbindlicher Testhost, isolierte Worker-Projekte, Snapshot-Übertragung und Aufräumen. Keine Containerbuilds/-teststacks auf dem Laptop.
- [Agent-Betrieb: Pi, Orca und Secret-Guard](operations/agent-runtime.md): Diagnosen, autorisierte lokale Patches, Regressionstests, Aktivierung und Rollback. Installierte Runtime-Artefakte liegen außerhalb des App-Repos; Nachweis und Grenzen stehen hier.
- [Arbeitsregeln](../AGENTS.md) und [Zuständigkeiten/Integration](../multi-agent.md).
- [Lokale Konfigurationsvorlage](../.env.example): ausschließlich Platzhalter; keine echten Werte in Dokumente, Chat oder Git übernehmen.
- [Betriebsgrundlage](operations/foundation.md): vorhandenes Setup und Prüfkommandos. Fehlende Live-, Restore-, Rootless- und Release-Nachweise bleiben im Plan und Integrationslog sichtbar.
- [D3a Quellenstatus-Oberfläche](handoffs/D3a.md): capability-gesperrte lesende UI, sichere Fehler-/Wechselzustände, exakter kube1-Nachweis von 33 Tests und TypeScript; kein Full-Flow-/Freischaltungsnachweis.
- [B5 geschlossener Upload-HTTP-Adapter](handoffs/B5.md): mit Quellen-GET in ASGI montiert, echte Identity/CSRF/kurze Preauthorization vor Body; Capability bleibtfalse, aktuelle HTTP-Key-Grammatik in den [Verträgen](architecture/contracts.md). [Unabhängiger Review](review/b5-upload-adapter.md): P2-Registrierungstestfehler durch echte Root-Kontrollen ersetzt; keine Storage-Freischaltung.
- [A5 native DB-/Storage-Abnahme](handoffs/A5.md): Direct-Storage-Fixture818deb3 bestanden und unabhängig geprüft, integriert8ba4f1a; kein Combined-/Produktionsnachweis und keine neue allgemeine DB-Diagnose.
- [D4 TXT-/Markdown-Uploadoberfläche](handoffs/D4.md): auf0dfe9eb 79Vitesttests/tsc/Vitebuild auf kube1 und synthetischer Orca-Browser-Smoke bestanden; keine echte Browser→Backend/Storage-Gesamtabnahme.
- [Readiness-Adapter und natives Imageprüfpaket](operations/readiness-image.md): vier Read-only-Adapter und150lokaleTests; begrenzter nativer Imagebau und live200/defaultready503 auf7a986f1 bestanden/0/0/0 unabhängig geprüft. Kein echter Dependency-/Releasepass; Worker-/Startupverdrahtung bleibt offen.
- [Task6 dauerhafte Jobdelegation](handoffs/Task6-delegation.md): uninstallierter0007-/Consumer-Slice; native Delegations-/ACL-Prüfung inzwischen auf5f337cc bestanden. Kein Supervisor-/Startup-/Readinesspass.
- [Task6 isoliertes Prüfpaket](handoffs/Task6-packet.md) und [native5f337cc-Abnahme](handoffs/Task6-native-5f337cc.md): N0 unabhängig akzeptiert, sechs Phasen1/23/1/20/21/37PASS, Default1735PASS/40prescribedResearchskips,40Nodeexclusions unrun. Same-SSH-Nachkontrolle Source273/Namespace0/0/0/Credentialsabsent/Lockunchanged. [Quota-ACL-Korrektur](handoffs/Task6-quota-acl-correction.md) samt GrantOptionverboten jetzt nativ geprüft; historische [Concurrency-](handoffs/Task6-concurrency-correction.md)/[Fixture-Korrekturen](handoffs/Task6-worker-fixture-correction.md) erhalten, alte Fehlerursachen UNKNOWN. Keine weiteren Remoteläufe freigegeben. NodeWrapper39passed/1CacheSkip ist keinContainer-/Storagepass.
- [Task6 S1 Lifecycle-/Cancellationbeobachtung](handoffs/Task6-supervisor-lifecycle.md): lokal unabhängig akzeptiert, Parent103Tests/309PhasenPASS,0externeIO/100Executorsgejoint. Echte Futurezustände, nach Context-Exit beobachtete Commits, Stickyfault ohne falsches Ack und Admissionblockade bei unerledigter Operation. Einzelne Diagnostiken eng reconciliert, BLE001 sichtbar; kein neuer nativer Nachweis.
- [Task6 S2a Consumer-Renewal und Lifecycle](handoffs/Task6-consumer-renewal.md): lokal unabhängig akzeptiert, Parent190Tests/570PhasenPASS/0externeIO/186Executorsgejoint. Konservative Leasegültigkeit, Retry-feste Terminaldeadline, validierte tatsächliche Zeitereignisse und separate Commitwahrheit ohne falsches Ack; finale Re-Review ohne Befunde. BLE001 sichtbar, kein nativer Nachweis für neue Bytes.
- [Task6 Supervisions-/Startupplan](architecture/task6-supervision-plan.md): N0 sowie lokales S1/S2a akzeptiert. S2b Supervisor/frische Workerreadiness/Response-Recheck und S3 Startupassembly folgen separat; echte Dependency-/Outage-/Restart-/Storage-/Wire-COMMITabnahme bleiben weitere Gates.
- [Cloudflare-Vorbereitung](operations/cloudflare-automation.md): lokal integriert, fail-closed Readiness-/Release-/Accessgates; keine Liveaktivierung. [Unabhängiger Review](review/e-cloudflare-delivery.md), [E-Handoff](handoffs/E-cloudflare-automation.md).
- [Bereichsübergaben](handoffs/CONTINUE.md): Einstieg zu den relevanten Worker-Berichten; diese sind technische Detailnachweise, keine zusätzliche Bedienungsanleitung.

## Dokumentationsprinzip

Aktuelle Anweisungen stehen am Anfang ihres maßgeblichen Dokuments. Prüfhistorie bleibt im Integrationslog, nicht als mehrfach kopierter Status in jedem Dokument. Änderungen an Funktionen, Verträgen und Betrieb werden zusammen mit der Implementierung dokumentiert. Nur tatsächlich geprüfte Abläufe als funktionsfähig beschreiben; lokale Fixtures, reale lokale Dienste und Produktionsnachweise auseinanderhalten.

Dieser Index ist keine Produkt- oder Produktionsfreigabe. Der jeweils aktuelle Umfang und die offenen Freischaltungsgrenzen stehen im Aufgabenplan.
