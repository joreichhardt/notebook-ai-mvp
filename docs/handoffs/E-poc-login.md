# Login-/Workspace-Stack

Profil: `login-workspace-v1` in `compose.poc.yaml`. Der Deploymenthelper verwendet bereits Rootless Podman und einen gepinnten Compose-Provider. Laufzeit-Secrets kommen dienstspezifisch aus Bitwarden.

Offen sind die echte Bereitstellung, eigene SMTP-Konfiguration, E-Mail-Bestätigung und der erste Adminlogin. Dokumente, Chat, Registrierung, Recovery und Google-Login bleiben in diesem Profil deaktiviert. Die volle Produktionsreadiness bleibt 503.

## Dauerhafte Sperre und Completion-Vertrag

Vor Änderungen hält der Hosthelper den Deploymentlock und schreibt einen dauerhaften Pendingmarker. Ausschließlich bestätigter synchroner Adminabschluss, Cleanup und abschließendes `up` erlauben dessen Entfernung. Timeout, Transportfehler und unbekannte Ausgänge sperren weitere Rollouts; benötigte Adminfiles bleiben erhalten. Kein automatischer Retry oder Reset. Das tatsächliche Wait-/Exitverhalten unter dem vorgesehenen Appkonto ist vor dem Rollout zu prüfen.

[Arbeitsstand](../orchestration/plan.md) · [Betrieb](../operations/foundation.md)
