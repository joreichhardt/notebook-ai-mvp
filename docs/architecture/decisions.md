# Architektur

## Anwendung

React/TypeScript im Browser, FastAPI als Backend-for-Frontend. Das Backend verwaltet Sitzungen, Autorisierung und Providerzugriffe. Supabase liefert PostgreSQL und Authentifizierung. pgvector, Volltextsuche und relationale Graphdaten bilden die geplante Retrieval-Grundlage.

Für das MVP ist privater persistenter Dateispeicher vorgesehen. `LocalStorage` in `backend/app/documents/local_storage.py` implementiert das Originaldatei-Backend: serverbestimmte UUID-Pfade, private Dateirechte, atomare Veröffentlichung ohne Überschreiben und erneute Autorisierung vor/nach I/O. TXT, Markdown und PDF werden als unverarbeitete Bytes gespeichert; das ist kein PDF-Parsing-Nachweis. Die TXT-/Markdown-HTTP-Anbindung in `app.main` ist explizit opt-in: `NOTEBOOK_LOCAL_UPLOADS_ENABLED=true`, ein privates persistentes `NOTEBOOK_LOCAL_STORAGE_ROOT` und eine geschützte `NOTEBOOK_STORAGE_IO_DATABASE_URL_FILE` mit eigenem eingeschränktem NOINHERIT-Login für `notebook_storage_io`. Kein Rückgriff auf App-/Admin-Credentials. Ungültige Konfiguration hält die Dokumentfunktion geschlossen; DB-Rollen und Nutzerrechte werden bei jeder Aktion geprüft. Das Login-only-Profil `app.poc` bleibt unverändert ohne Dokumentrouten. Geheimniswechsel benötigen einen kontrollierten Neustart. Die bestehende TXT-/Markdown-Uploadoberfläche und Quellenliste sind über dieses Feature-Flag bereits angebunden. Eine ergänzte Textansicht liest ausschließlich extrahierte Abschnitte der aktuellen, nicht gelöschten Version nach frischer Zulassungs-/Notebookprüfung: maximal20 Abschnitte mit je4000 Zeichen pro Seite, gekürzte Ausschnitte markiert, keine Cache-Speicherung und keine Job-/Storage-Metadaten. React zeigt den Text inert statt als HTML; Nutzer-/Notebookwechsel und Schließen brechen ausstehende Abrufe ab. Worker-Laufzeit und PDF-Extraktion stehen noch aus: Uploads liefern vorerst einen ehrlich als `queued` markierten Job, keine behauptete Verarbeitung. Die Textansicht ersetzt keinen Extraktionsnachweis. Später folgt ein austauschbarer Cloud-/Object-Storage-Adapter.

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
