# Architektur

## Anwendung

React/TypeScript im Browser, FastAPI als Backend-for-Frontend. Das Backend verwaltet Sitzungen, Autorisierung und Providerzugriffe. Supabase liefert PostgreSQL und Authentifizierung. pgvector, Volltextsuche und relationale Graphdaten bilden die geplante Retrieval-Grundlage.

Für das MVP ist privater persistenter Dateispeicher vorgesehen. `LocalStorage` in `backend/app/documents/local_storage.py` implementiert das Originaldatei-Backend: serverbestimmte UUID-Pfade, private Dateirechte, atomare Veröffentlichung ohne Überschreiben und erneute Autorisierung vor/nach I/O. TXT, Markdown und PDF werden als unverarbeitete Bytes gespeichert; das ist kein PDF-Parsing-Nachweis. Die TXT-/Markdown-HTTP-Anbindung in `app.main` ist explizit opt-in: `NOTEBOOK_LOCAL_UPLOADS_ENABLED=true`, ein privates persistentes `NOTEBOOK_LOCAL_STORAGE_ROOT` und eine geschützte `NOTEBOOK_STORAGE_IO_DATABASE_URL_FILE` mit eigenem eingeschränktem NOINHERIT-Login für `notebook_storage_io`. Kein Rückgriff auf App-/Admin-Credentials. Ungültige Konfiguration hält die Dokumentfunktion geschlossen; DB-Rollen und Nutzerrechte werden bei jeder Aktion geprüft. Das Login-only-Profil `app.poc` bleibt unverändert ohne Dokumentrouten. Geheimniswechsel benötigen einen kontrollierten Neustart. Die bestehende TXT-/Markdown-Uploadoberfläche und Quellenliste sind über dieses Feature-Flag bereits angebunden. Eine ergänzte Textansicht liest ausschließlich extrahierte Abschnitte der aktuellen, nicht gelöschten Version nach frischer Zulassungs-/Notebookprüfung: maximal20 Abschnitte mit je4000 Zeichen pro Seite, gekürzte Ausschnitte markiert, keine Cache-Speicherung und keine Job-/Storage-Metadaten. React zeigt den Text inert statt als HTML; Nutzer-/Notebookwechsel und Schließen brechen ausstehende Abrufe ab. Der separat startbare Texteinstieg ist unten beschrieben, aber noch nicht bereitgestellt; PDF-Extraktion steht aus. Ohne aktiven Worker bleiben neue Uploads ehrlich `queued`, keine behauptete Verarbeitung. Die Textansicht ersetzt keinen Extraktionsnachweis. Später folgt ein austauschbarer Cloud-/Object-Storage-Adapter.

`python -m app.workers.local_runtime` ist ein expliziter Single-Delivery-Einstieg außerhalb des HTTP-Prozesses. `NOTEBOOK_LOCAL_TEXT_WORKER_ENABLED` bleibt standardmäßig false. Benötigt werden das private Originalverzeichnis (im Worker read-only einbinden), eine geschützte `NOTEBOOK_DATABASE_URL_FILE` für den direkten eingeschränkten `notebook_app`-Login und eine eigene `NOTEBOOK_TEXT_SCHEDULER_DATABASE_URL_FILE` für den direkten eingeschränkten `notebook_text_scheduler`-Login. Beide Dateien: aktuelle Prozess-UID, privat, regulär, einzelner Link, maximal16KiB, kein Symlink/FIFO. Keine Admin-/IO-DSN, Browseridentität, Provider-/SMTP-/BWS-Credentials oder Job-/Actor-Argumente. Die Migration lässt die Schedulerrolle absichtlich NOLOGIN: eigener Bitwarden-Eintrag und kontrollierte spätere Login-Provisionierung sind erforderlich; der Einstieg verändert keine Rollen oder Secrets. Das bestehende7er-Manifest/Recovery nicht ändern oder erneut ausführen. Dateien werden pro Prozessstart gelesen; Rotation erfordert kontrollierten Neustart.

Der Einstieg verwendet den akzeptierten `ExactTextConsumer`, einen gemeinsam begrenzten `BoundedThreadRunner` und lokale Originale mit frischer Autorisierung. Verantwortlicher Nutzer und exakter Auftrag kommen ausschließlich aus frisch DB-validierten Grants. Zwei getrennte Pools mit je1 Verbindung/0Overflow, Pooltimeout1s, Connecttimeout3s und Startup-Statement-/Locktimeouts3s/1s; keine neue Identity-Ausstellung, kein Request-Worker und kein geparkter S2b-Supervisor. Pro Prozess maximal eine Zustellung. Exit0 nur bei idle oder bestätigtem terminalem Ergebnis UND tatsächlich ruhendem Executor; ein bestätigtes `failed` ist weiterhin ein fehlgeschlagener Nutzerjob. Unsicherheit/fehlende Quittierung/ausstehender Callback => fester `held`-Status/Exit75, keine Wiederholung oder Jobrücksetzung. Keine IDs, Grants, Texte, DSNs oder Treiberfehler im CLI-Output. Abgeschaltete Konfiguration => Exit78. Betrieb nur mit externer harter Prozess-/Containerdeadline: Cancellation oder Thread-Close beweisen keine physische Terminierung; bei held/nonzero keine automatischen Neustarts. Systemd-/Containeraktivierung, neue Credentials und native Laufzeitabnahme sind mit diesem Quellcode noch nicht geliefert.

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
