# Betrieb

Vorgesehen ist ein lokaler Testserver mit **Rootless Podman** hinter Cloudflare unter `https://note.yokoma.de`. Die Anwendung ist noch nicht öffentlich freigeschaltet.

- `compose.yaml`: API und statisches Frontend.
- `compose.poc.yaml`: Login-/Workspace-Stack mit PostgreSQL und Auth.
- API-Image: digestgepinnte Chainguard-Python-Basis; Developer-Stage für Installation/SHA-Stempel, shellfreie Laufzeit unter UID10001. Python muss dem Projektbereich `>=3.12` entsprechen; eingefrorene native Abhängigkeiten werden beim Imagebau auch in der tatsächlichen Laufzeit importiert. Der SHA-Stempel bleibt root-owned und maßgeblich, nicht eine veränderbare Laufzeitvariable.
- Rootless-Betrieb unter einem eigenen Appkonto; Compose über den gepinnten Provider am Podman-Socket.
- Secrets aus Bitwarden über `deploy/provision_secrets.py`, nur als benötigte geschützte Dateien je Dienst. Bootstrap-Token nicht an Anwendungen vererben.
- Source/CI/Images öffentlich auf GitHub/GHCR; Deployment in einer getrennten privaten Lane.
- Rollouts nur mit freigegebenem Commit und Image-Digests. Migrationen und Updates sind serialisiert; unbekannter Ausgang bedeutet Stopp, keinen automatischen Retry.
- Daten und Dateien persistent halten, Backups verschlüsseln und Wiederherstellung vor Freigabe prüfen.

Für den ersten Start fehlen noch die dienstspezifischen Secrets, eigene SMTP-Konfiguration und die echte Konto-/E-Mail-Bestätigung.
