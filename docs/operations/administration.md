# Plattformrollen und App-Einladungen

## Berechtigungen

| Aktion | Workspace-Owner | Admin | Superadmin |
|---|---|---|---|
| Eigenen Workspace verwalten/einladen | Ja | Nur bei eigener Mitgliedschaft | Nur bei eigener Mitgliedschaft |
| Zur App ohne geteilten Workspace einladen | Nein | Ja | Ja |
| App-Adminrollen auflisten | Nein | Nein | Ja |
| Zugelassenen Nutzer zum Admin ernennen | Nein | Nein | Ja |
| Superadminrechte im Browser vergeben | Nein | Nein | Nein |
| Fremde private Inhalte oder KI-Zugänge verwenden | Nein | Nein | Nein |

Der Adminbereich wird anhand von `platform_role` aus `/api/auth/admission`
angezeigt, nicht anhand von E-Mail-Adresse oder Workspace-Rolle. Die eigentliche
Autorisierung erfolgt erneut serverseitig und in den PostgreSQL-Funktionen.
`POST /api/admin/administrators` akzeptiert ausschließlich eine stabile
`user_id` und niemals ein frei wählbares Rollenfeld. Die Zielperson muss bereits
zur App zugelassen sein. Ein erneuter Grant ist idempotent und stuft einen
vorhandenen Superadmin nicht herab.

App-Einladungen sind eine eigene Aktion im Adminbereich. Sie verwenden
`workspace_id=null`, teilen keinen vorhandenen Workspace und verleihen keine
Adminrechte. Nach Annahme erhält die Person ihren eigenen privaten Workspace.
Der Link wird nur zum Kopieren angezeigt, nicht automatisch versendet.
Workspace-Einladungen verbleiben bei **Mitglieder** und benötigen dort einen
Owner. Auch deren Annahme legt einen persönlichen Workspace an.

## Betreiber-Schritte

Revision `0008` ergänzt die Rolle mit Standard `admin`: Bestandskonten werden
nicht automatisch hochgestuft. Der gesonderte Funktionsbesitzer ist `NOLOGIN`,
hat keinen Runtime-Rollenmitgliedschaftspfad und keine `UPDATE(role)`-Berechtigung.
Seine auf `user_id` begrenzte UPDATE-Berechtigung wird für PostgreSQL-Zeilensperren
benötigt. Runtime-Verbindungen dürfen die Rollentabelle weiterhin nicht ändern.

Ein frischer, einmaliger `app.auth.bootstrap` legt den ersten bestätigten
Administrator als Superadmin an. **Bei einer bereits initialisierten App den
Bootstrap nicht wiederholen.** Für das bestehende bestätigte Betreiberkonto
verwendet `docker/poc-admin.py promote-confirmed-superadmin` stattdessen
`promote_confirmed_superadmin`: exakte konfigurierte Betreiber-E-Mail nachschlagen,
Bestätigung, bestehende App-Zulassung und vorhandene Adminrolle prüfen; danach
nur die Rolle dieser stabilen Auth-ID ändern. Kein zusätzlicher Workspace,
kein neues Passwort und keine Änderung persönlicher Verbindungen.

Der Schritt benötigt dieselbe getrennte, kurzlebige DB-Betreiberautorität wie
Migrationen. Nicht über HTTP oder im laufenden API-Container freischalten;
keine privilegierten DB-Zugänge oder Bitwarden-Schreibrechte an normale Dienste
verteilen. Vor Live-Migration Daten sichern, freigegebene unveränderliche Images
und Quellstände abgleichen; anschließend die Ziel-ID, Rolle und unveränderte
Zulassung prüfen, ohne Credentials auszugeben. Privilegierte Laufzeitdateien und
Einmal-Container nach dem Schritt entfernen. Ein Browser-Neuladen lädt danach
die neue Rolle. Der Migrations-Downgrade ist absichtlich gesperrt und benötigt
einen separat geprüften Rollback.

## Prüfungen

- `backend/tests/auth/test_administration_http.py`: eigene Rollenmetadaten,
  unbekannte Rollenfelder, CSRF/Origin und bereinigte Fehler.
- `backend/tests/auth/test_platform_roles.py`: echte PostgreSQL-Berechtigungen,
  Selbstschutz, zugelassene Ziele, Idempotenz und fehlende Runtime-Mitgliedschaft.
- `frontend/src/Administration.test.tsx` und `frontend/src/App.test.tsx`:
  App-/Workspace-Trennung, serverseitiger Rollennachweis und Fehlerzustände.

Diese Prüfungen ersetzen weder den Live-Betreiber-Schritt noch einen echten
Browsernachweis mit dem bestätigten Betreiberkonto.
