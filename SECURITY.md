# Sicherheit

- Browser, Dokumente, Modelltexte und externe Endpoints sind nicht vertrauenswürdig.
- Das Backend prüft Appzulassung, Workspace-Rollen und Dateizugriffe; PostgreSQL-RLS ergänzt diese Prüfung.
- Providerzugänge gehören ausschließlich ihrem Nutzer. Workspace-Mitgliedschaft gewährt keinen Zugriff auf fremde Credentials.
- Secrets bleiben serverseitig in Bitwarden bzw. geschützten Laufzeitdateien, niemals in Git, Browserantworten oder Logs.
- Parser laufen ohne Netzwerk und Credentials. Provider-Endpunkte sind administrativ freigegeben.
- Rootless Podman, getrennte App-/Runnerkonten und minimale Containerrechte begrenzen Hostzugriff. Kein Containersocket in Anwendung oder Parser.
- Öffentliche PRs erhalten weder Produktions-Secrets noch Serverzugriff. Pflichtprüfungen und unveränderliche Image-Digests gehen dem Deployment voraus.

Bei einem Vorfall betroffene Jobs stoppen, Zugänge widerrufen und rotieren, bereinigte Belege erhalten und kontrolliert wiederherstellen. Sicherheitsprobleme ohne Secret-Werte privat melden.

Die Anwendung ist noch nicht für den öffentlichen Betrieb freigegeben.
