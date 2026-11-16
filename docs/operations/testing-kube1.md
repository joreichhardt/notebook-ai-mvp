# kube1-Betrieb für Entwicklungsaufgaben

Container auf kube1 mit Rootless Podman ausführen. Compose verwendet den gepinnten Provider über den benutzereigenen Podman-Socket. Auf dem Laptop nur leichte Prüfungen ohne Containerlast.

- SSH: `jre@kube1`, vorhandener Schlüssel, strikte Hostprüfung; Remoteumgebung bereinigen.
- Je Auftrag eigene Verzeichnisse, Projektnamen, Netze und Volumes sowie begrenzte CPU/RAM/PIDs.
- Ports ausschließlich nach Zuweisung auf Loopback binden; fremde Dienste und Ressourcen erhalten.
- Nur synthetische Daten und eigene Credentials verwenden.
- Nach unerwartetem Fehler oder Verbindungsabbruch anhalten, kein automatischer Retry oder Cleanup.
- Ausschließlich nachweislich eigene Ressourcen aufräumen; keine globalen Prune-Befehle.

[Arbeitsanweisungen](../../AGENTS.md)
