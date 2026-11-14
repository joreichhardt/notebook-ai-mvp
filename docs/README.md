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
- [Task6 dauerhafte Jobdelegation](handoffs/Task6-delegation.md): uninstallierter0007-/Consumer-Slice,37leichte Tests unabhängig bestätigt; neue kube1-DB-Prüfung offen. Kein Worker-/Startup-/Readinesspass.
- [Task6 isoliertes Prüfpaket](handoffs/Task6-packet.md): unabhängiger Re-Review ohne offene Befunde,78Pakettests bestätigt; authentifizierter Empfang, exaktes Testkonto und evidenzunabhängiges Cleanup. Native Abnahme HOLD: Migration1/Delegation23pass, Concurrency1fail; weitereRegression UNRUN. Modellrendering nach versionsbezogenemReview geklärt; P1-Testfalse-negative lokal belegt; [Packet-Korrektur](handoffs/Task6-concurrency-correction.md) mit220lokalenTests unabhängig bestätigt, unabhängiger Implementierungsreview ohne Befunde. Neue native Abnahme noch nicht freigegeben. Separate lokale Node-Wrapper-Prüfung39passed/1CacheSkip, kein gepinnterContainer-/Storagepass.
- [Task6 Supervisions-/Startupplan](architecture/task6-supervision-plan.md): unabhängig ohne Befunde reviewed, nur konkreter lokaler Entwurf. Consumer-eigene Deliveryrenewal, tatsächliche Future-/Shutdownzustände und frische Readinessentscheidung; Runtimeimplementierung erst nach nativer0007-Abnahme.
- [Cloudflare-Vorbereitung](operations/cloudflare-automation.md): lokal integriert, fail-closed Readiness-/Release-/Accessgates; keine Liveaktivierung. [Unabhängiger Review](review/e-cloudflare-delivery.md), [E-Handoff](handoffs/E-cloudflare-automation.md).
- [Bereichsübergaben](handoffs/CONTINUE.md): Einstieg zu den relevanten Worker-Berichten; diese sind technische Detailnachweise, keine zusätzliche Bedienungsanleitung.

## Dokumentationsprinzip

Aktuelle Anweisungen stehen am Anfang ihres maßgeblichen Dokuments. Prüfhistorie bleibt im Integrationslog, nicht als mehrfach kopierter Status in jedem Dokument. Änderungen an Funktionen, Verträgen und Betrieb werden zusammen mit der Implementierung dokumentiert. Nur tatsächlich geprüfte Abläufe als funktionsfähig beschreiben; lokale Fixtures, reale lokale Dienste und Produktionsnachweise auseinanderhalten.

Dieser Index ist keine Produkt- oder Produktionsfreigabe. Der jeweils aktuelle Umfang und die offenen Freischaltungsgrenzen stehen im Aufgabenplan.
