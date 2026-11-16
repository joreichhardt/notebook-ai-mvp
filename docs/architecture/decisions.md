# Architektur

## Anwendung

React/TypeScript im Browser, FastAPI als Backend-for-Frontend. Das Backend verwaltet Sitzungen, Autorisierung und Providerzugriffe. Supabase liefert PostgreSQL und Authentifizierung. pgvector, Volltextsuche und relationale Graphdaten bilden die geplante Retrieval-Grundlage.

Dateien liegen für das MVP persistent und privat im lokalen Dateisystem. Später folgt ein austauschbarer Cloud-/Object-Storage-Adapter. Der lokale Adapter ist noch umzusetzen.

Hintergrundaufgaben laufen in einem separaten Python-Worker aus derselben Codebasis. PostgreSQL verwaltet Jobs, Leases und Wiederaufnahme. Parser arbeiten isoliert ohne Netzwerk oder Credentials.

## Infrastruktur

Lokaler Testserver mit **Rootless Podman** hinter Cloudflare. Compose verwendet einen explizit gepinnten Provider. Bestehende fremde Dienste bleiben unverändert.

Source, CI und Images liegen auf GitHub/GHCR. Das Deployment-Repository bleibt privat; App- und Runnerkonto sind getrennt. Öffentliche PRs erhalten keinen Zugriff auf den Server.

## Sicherheit

- Sitzungen über Secure-/HttpOnly-Cookies mit Origin-/CSRF-Schutz.
- Appzulassung und Workspace-Rollen zusätzlich zur Supabase-Identität; Datenbankzugriffe mit eingeschränkter Rolle und transaktionslokalem Nutzerkontext.
- Persönliche Chats und Providerzugänge bleiben nutzergebunden. Tabellen speichern Secret-Referenzen, keine Zugangsdaten.
- Infrastruktur-Secrets über `bws`; persönliche Credentials über das offizielle Bitwarden-SDK mit getrenntem Schreibzugang.
- Unverfügbare Funktionen bleiben verborgen. Readiness und Releasefreigabe sind getrennt; unbekannte Deploymentausgänge sperren weitere Rollouts.

[Schnittstellen](contracts.md) · [Sicherheitsregeln](../../SECURITY.md)
