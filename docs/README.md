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
- [A5 native Phase-A-/ACL-Abnahme](handoffs/A5.md): DB-/Parserproblem gelöst; immutable `88ad94d` bestanden, Main verifizierte Evidence/69 Quelldateien/Cleanup unabhängig. Echte Storage-HTTP-/Upload-Abnahme ist der nächste separate Schritt, keine neue allgemeine DB-Diagnose.
- [Bereichsübergaben](handoffs/CONTINUE.md): Einstieg zu den relevanten Worker-Berichten; diese sind technische Detailnachweise, keine zusätzliche Bedienungsanleitung.

## Dokumentationsprinzip

Aktuelle Anweisungen stehen am Anfang ihres maßgeblichen Dokuments. Prüfhistorie bleibt im Integrationslog, nicht als mehrfach kopierter Status in jedem Dokument. Änderungen an Funktionen, Verträgen und Betrieb werden zusammen mit der Implementierung dokumentiert. Nur tatsächlich geprüfte Abläufe als funktionsfähig beschreiben; lokale Fixtures, reale lokale Dienste und Produktionsnachweise auseinanderhalten.

Dieser Index ist keine Produkt- oder Produktionsfreigabe. Der jeweils aktuelle Umfang und die offenen Freischaltungsgrenzen stehen im Aufgabenplan.
