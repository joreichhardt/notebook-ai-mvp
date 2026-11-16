# Arbeitsanweisungen

## Auftrag und Vorgehen

- Für künftig gestartete Arbeits- und Teilagenten immer Pi verwenden, einschließlich aller durch Orca gestarteten Agents (`--agent pi`). Ist Pi nicht verfügbar, die konkrete Lücke melden statt still auf einen anderen Agenten auszuweichen. Diese Vorgabe wählt den Agenten, nicht ein bestimmtes Modell.
- Der aktuelle Nutzerauftrag bestimmt den Arbeitsumfang. Die [Produktspezifikation](docs/product-spec.md) beschreibt das Zielprodukt; ein Dokumentationsauftrag startet nicht automatisch Implementierung oder Deployment.
- Vor Implementierungsarbeiten die Spezifikation lesen, insbesondere die betroffenen Abnahmekriterien. Abschnitte 11–13 konkretisieren frühere Anforderungen. Für die Wahl des Secret-Speichers gilt die Präzisierung unten.
- Zuerst Bestand und Git-Status prüfen; Nutzeränderungen erhalten. In kleinen vertikalen Schritten arbeiten und passende Prüfungen ausführen. Versionen und Befehle aus dem Projekt ableiten, externe APIs anhand offizieller Dokumentation prüfen.
- Fehlende Zugänge blockieren nur abhängige Schritte. Unabhängige Arbeit fortsetzen. Abschließend Änderungen, tatsächlich ausgeführte Tests und offene Punkte nennen; lokale Tests und Produktionsnachweise unterscheiden.

## Fortsetzen und Kompaktierung

- Nach ergänzenden Nutzeraufträgen den laufenden Hauptauftrag selbstständig wieder aufnehmen, soweit keine ausdrückliche Pause oder echte Entscheidungssperre besteht. Bestehende Worker gemäß ihren Zuständigkeiten weiterführen; der Hauptagent prüft, koordiniert und integriert statt deren Implementierung zu duplizieren.
- Vor Kontextkompaktierung [CONTINUE.md](docs/handoffs/CONTINUE.md) mit Arbeitsbranch/Commit, aktiven Worktrees/Dispatch-IDs, letztem belastbaren Prüfnachweis, offenen Entscheidungen und konkretem nächsten Schritt aktualisieren. Nach Kompaktierung diesen Einstieg und die betroffenen Regeln lesen, tatsächlichen Git-/Arbeitsstand prüfen und den bereits freigegebenen Auftrag ohne erneute Startaufforderung fortsetzen.
- Orca-Rückmeldungen gemäß Wakeup bearbeiten. Während Worker laufen unabhängige Hauptagentenarbeit fortsetzen; ist alles andere blockiert, Kontrolle abgeben und Wakeup abwarten statt Nachrichten zu pollen oder zusätzliche Ersatzworker zu starten.

Bei Kompaktierungs-/Fortsetzungsstörungen zuerst [Pi-/Orca-Betriebsdiagnose](docs/operations/agent-runtime.md) lesen: Router-Settings, automatische Kompaktierung, bewusste Abbrüche und Orca-Dispatch-Identität getrennt prüfen. Einen lokalen Core-Patch erst nach kontrolliertem Prozesswechsel als aktiv betrachten; `/reload` allein genügt dafür nicht.

## Testinfrastruktur

- Auf `kube1` **Rootless Podman** mit dem gepinnten Compose-Provider verwenden, keinen Docker-Daemon. Containerbuilds und Teststacks dort ausführen; lokal nur leichte Prüfungen ohne Containerlast. GitHub-hosted CI bleibt getrennt. Isolierung: [kube1-Betrieb](docs/operations/testing-kube1.md).
- Auf kube1 je Auftrag eigene Pfade, Compose-Projektnamen, Netze/Volumes und Ressourcenlimits verwenden; Ports nur bei Bedarf auf Loopback veröffentlichen. Ausschließlich synthetische Testdaten und eigene Test-Credentials verwenden. Bestehende Dienste, Produktions-Secrets und andere Worker-Umgebungen unverändert lassen; Aufräumen nur im explizit zugewiesenen Testprojekt, keine globalen Prune-Befehle.
- Fehlt kube1-Zugriff oder freie Kapazität, abhängige Tests als blockiert melden, statt sie auf dem Laptop zu starten. Vorhandene lokale oder native Testumgebungen nur gezielt und nach Zuständigkeitsprüfung stilllegen; keine fremden Container stoppen.

## Dokumentation laufend pflegen

- Betroffene Dokumente zusammen mit Verhalten, Schnittstellen und Betrieb aktualisieren. Knapp auf Produkt, Nutzung, Architektur und notwendige Betriebsangaben beschränken; keine Änderungsrechtfertigungen oder Prüfchroniken in Lesedokumenten. Technische Belege separat erhalten.
- Einstieg: [docs/README.md](docs/README.md). Anforderungen in der Spezifikation, Arbeitsstand im Plan, Schnittstellen in den Verträgen; Angaben nicht mehrfach kopieren. Links prüfen.
- Hauptagent pflegt gemeinsame Dokumente, Worker ihre zugewiesenen Handoffs. Keine Secrets oder privaten Einladungslinks dokumentieren.

## Infrastrukturwissen aus ~/dev/homeserver

- Bei Arbeiten an kube1, Deployment, Netzwerk, Cloudflare, SMTP oder Betriebs-Secrets zuerst `~/dev/homeserver/README.md` und `~/dev/homeserver/secrets/README.md` lesen. Danach gezielt relevante Architekturdateien und Runbooks konsultieren; `services/excalidraw/README.md` beschreibt bestehende kube1-/Tunnel-Konventionen.
- Das Repo liefert Infrastrukturwissen, keine Secrets und keinen garantierten Live-Zustand. Annahmen vor Änderungen am Zielsystem prüfen. Relevante Erkenntnisse ohne geheime Werte dokumentieren.
- Homeserver-Dateien zunächst nur lesen. Änderungen dort müssen zum Nutzerauftrag gehören. Fremde Dienste, DNS-Einträge und Credentials erhalten; keine Zugangsdaten anderer Anwendungen übernehmen.
- Fehlt der Pfad oder Zugang, die konkrete Lücke melden und lokal mögliche Arbeit fortsetzen. Keine pauschale Suche nach Zugangsdaten im Home-Verzeichnis.

## Secret-Verwaltung

- Bitwarden Secrets Manager ist der verbindliche Secret-Speicher. Auf kube1 Secrets über die gepinnte `bws` CLI beziehen; Supabase Vault ist nicht vorgesehen. Supabase bleibt für Datenbank und Auth zuständig; Dateispeicherung gemäß Produktspezifikation. Bitwarden-Endpoint und Projekt explizit konfigurieren, keine Cloud-Region oder Self-Hosting-Installation voraussetzen.
- Für Anwendung und Umgebung eigene Machine Accounts mit minimalen Projektberechtigungen verwenden. Der Deployment-Abruf benötigt nur Leserechte. `BWS_ACCESS_TOKEN` als Bootstrap-Secret außerhalb Git geschützt auf kube1 bereitstellen und ausschließlich an den Abrufprozess übergeben; nicht an Anwendungscontainer vererben oder als Kommandozeilenargument ausgeben.
- Benötigte Secrets gezielt abrufen und ohne Ausgabe direkt in geschützte Laufzeitdateien oder die Umgebung des Zielprozesses übergeben. Bei Compose bevorzugt dienstspezifische Secret-Dateien verwenden, soweit das Image dies unterstützt. Keine vollständigen Secret-Projekte in alle Container injizieren. Flüchtige Dateien restriktiv anlegen und nach ihrem notwendigen Lebenszyklus entfernen.
- Vor einem Rollout alle benötigten Werte validieren, ohne Inhalte auszugeben. Bei Abruffehlern vor Änderungen an laufenden Diensten abbrechen; keine leeren oder erfundenen Ersatzwerte verwenden. Rotation benötigt eine kontrollierte Aktualisierung bzw. einen Neustart der Verbraucher. Verhalten bei Bitwarden-Ausfall und Host-Neustart dokumentieren und testen.
- Persönliche Provider-Keys und OAuth-Tokens benötigen zusätzlich einen nutzergebundenen Secret-Lebenszyklus. Vor dessen Implementierung Erstellen, Ersetzen, Refresh und Löschen über die gewählte Bitwarden-Schnittstelle prüfen. Dafür gegebenenfalls einen getrennten, eng berechtigten Schreibzugang vorsehen; dem Deployment-Abruf keine pauschalen Schreibrechte geben. Keine persönlichen Secrets als globale Container-Konfiguration verteilen.
- Anwendungstabellen speichern nur Secret-Referenzen und nicht geheime Metadaten. Referenzen gehören einer stabilen Auth-User-ID. Zugriff ausschließlich serverseitig nach Eigentümer- und Zweckprüfung; Jobs benötigen einen expliziten verantwortlichen Nutzer. Workspace-Mitgliedschaft erlaubt keine Verwendung fremder Credentials.
- Bitwarden-Zugänge und Secret-Abrufe bleiben Browsern und normalen Nutzerrollen unzugänglich. Verschlüsselung ersetzt keine Autorisierung. Die Oberfläche erlaubt Ersetzen und Trennen, kein späteres Klartext-Auslesen; auch Demo-Admins erhalten keinen Zugriff.
- Auch Supabase-Startkonfiguration, DB-Passwörter, Signaturschlüssel, service_role-Zugang, SMTP-/OAuth-Konfiguration und projektspezifische Tunnel-Credentials aus Bitwarden beziehen. Der lokale Bitwarden-Zugang bleibt die gesondert geschützte Bootstrap-Ausnahme. GitHub erhält nur minimal notwendige Deployment-Zugänge; Produktions-Secrets werden auf kube1 abgerufen.
- Eigene Credentials je Anwendung und Umgebung einsetzen. Secret-Dateien nur benötigten Dienstkonten zugänglich machen. Schlüsselmaterial getrennt von Datenbank-Backups schützen. Rotation, Widerruf, Löschung und Wiederherstellung dokumentieren und prüfen.
- Secret-Werte direkt zwischen autorisierter Quelle und Ziel übertragen, ohne sie in Toolausgaben, Chat, Logs, Screenshots, Build-Args oder Git auszugeben. Kein `cat .env`, `printenv`, Shell-Tracing oder unredigiertes Rendern produktiver Compose-Konfiguration. Für Bestandsprüfungen nur Namen, Pfade, Berechtigungen und Vorhandensein ausgeben.
- `.env.example` enthält Platzhalter. Fehlende Secrets nach Namen und Einsatzzweck melden und geschützte Bereitstellung ermöglichen; keine Klartextwerte im Chat anfordern. Bei versehentlicher Offenlegung den Wert nicht wiederholen und Rotation des betroffenen Zugangs veranlassen.
- Prüfen: fremde Nutzer können Secrets weder lesen noch verwenden; Logs bleiben frei von Credentials; Trennen verhindert neue Aufrufe und die Verwendung durch wartende Jobs; Backup/Restore erhält die kontrollierte Entschlüsselbarkeit.
