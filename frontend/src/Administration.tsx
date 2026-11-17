import { useEffect, useState, type FormEvent } from "react";
import { api, type Administrator, type PlatformRole } from "./api";

const message = (error: unknown) => error instanceof Error
  ? error.message : "Die Aktion konnte nicht abgeschlossen werden.";

export function Administration({ role }: { role: PlatformRole }) {
  const [administrators, setAdministrators] = useState<Administrator[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(role === "superadmin");
  useEffect(() => {
    if (role !== "superadmin") return;
    const controller = new AbortController();
    setLoading(true);
    api.administrators(controller.signal).then((result) => {
      if (!controller.signal.aborted) setAdministrators(result.items);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(message(failure));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [role]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const email = String(new FormData(form).get("email") ?? "").trim();
    setBusy(true); setError(""); setNotice(""); setLink("");
    try {
      const result = await api.invite(email, null, "viewer");
      const url = new URL("/invite", window.location.origin);
      url.hash = new URLSearchParams({ token: result.token }).toString();
      setLink(url.toString());
      form.reset();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  async function grant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const userId = String(new FormData(form).get("user_id") ?? "").trim();
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.grantAdministrator(userId);
      setAdministrators((items) => [...items.filter((item) => item.user_id !== result.user_id), result]);
      setNotice(result.role === "superadmin" ? "Dieses Konto ist bereits Superadmin." : "Adminrechte wurden vergeben.");
      form.reset();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }

  return <>
    <p className="eyebrow">ADMINBEREICH · {role === "superadmin" ? "SUPERADMIN" : "ADMIN"}</p>
    <h1>Administration<span className="accent">.</span></h1>
    <p className="lead">App-Zugänge verwalten, ohne private Workspaces oder persönliche KI-Schlüssel einzusehen.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="editor">
      <h2>Zur App einladen</h2>
      <p>Die eingeladene Person erhält einen eigenen privaten Workspace. Dein Workspace wird nicht geteilt.</p>
      <p>Der Link gilt nur für die angegebene E-Mail-Adresse. Er verleiht keine Adminrechte und wird nicht automatisch versendet.</p>
      <form onSubmit={invite}>
        <label>E-Mail für die App-Einladung<input name="email" type="email" autoComplete="off" required maxLength={320} /></label>
        <button className="primary" disabled={busy}>{busy ? "Bitte warten …" : "App-Einladungslink erstellen"}</button>
      </form>
      {link && <div className="notice" role="status">
        <label>Persönlicher App-Einladungslink<input readOnly value={link} onFocus={(event) => event.currentTarget.select()} /></label>
        <p>Bitte vertraulich an die eingeladene Person weitergeben, nicht öffentlich teilen.</p>
      </div>}
    </section>
    {role === "superadmin" && <section className="editor">
      <h2>Admins ernennen</h2>
      <p>Nur Superadmins dürfen Adminrechte vergeben. Die Person muss ihre App-Einladung bereits angenommen haben.</p>
      <p>Die Nutzer-ID findest du beispielsweise in der Mitgliederliste eines gemeinsamen Workspace. Adminrechte geben keinen Zugriff auf fremde private Inhalte oder KI-Zugänge.</p>
      <form onSubmit={grant}>
        <label>Nutzer-ID für Adminrechte<input name="user_id" required autoComplete="off" placeholder="UUID des zugelassenen Nutzers" /></label>
        <button disabled={busy}>Als Admin ernennen</button>
      </form>
      {loading ? <p role="status">Adminrollen werden geladen …</p> : <ul aria-label="App-Administratoren">
        {administrators.map((administrator) => <li key={administrator.user_id}>
          {administrator.user_id} · {administrator.role === "superadmin" ? "Superadmin" : "Admin"}
        </li>)}
      </ul>}
    </section>}
  </>;
}
