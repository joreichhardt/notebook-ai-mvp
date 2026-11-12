import { Invitation, Sharing } from "./Sharing";
import { Connections } from "./Connections";
import { Sources } from "./Sources";
import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  setCsrfToken,
  type Notebook,
  type Session,
  type Workspace,
  type AuthConfig,
} from "./api";
const message = (e: unknown) =>
  e instanceof Error
    ? e.message
    : "Die Aktion konnte nicht abgeschlossen werden.";
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        n.
      </span>
      <span>
        notebook<span className="brand-caption">Wissen mit Quellen</span>
      </span>
    </div>
  );
}
export default function App() {
  const [inviteToken, setInviteToken] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "",
  );
  const [config, setConfig] = useState<AuthConfig>();
  const [configError, setConfigError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (new URLSearchParams(window.location.hash.slice(1)).has("token"))
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
    const c = new AbortController();
    setConfig(undefined);
    setConfigError("");
    api
      .authConfig(c.signal)
      .then((value) => {
        if (!c.signal.aborted) setConfig(value);
      })
      .catch(() => {
        if (!c.signal.aborted)
          setConfigError(
            "Die Verbindung konnte nicht geprüft werden. Bitte versuche es erneut.",
          );
      });
    return () => c.abort();
  }, [retry]);
  const [session, setSession] = useState<Session>();
  const [sessionError, setSessionError] = useState("");
  useEffect(() => {
    const c = new AbortController();
    setSessionError("");
    api
      .session(c.signal)
      .then((s) => {
        if (!c.signal.aborted) {
          setSession(s);
          setCsrfToken(s.csrf_token);
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) setSessionError(message(e));
      });
    return () => c.abort();
  }, [retry]);
  const [admitted, setAdmitted] = useState<boolean>();
  const [admissionError, setAdmissionError] = useState("");
  useEffect(() => {
    setAdmitted(undefined);
    setAdmissionError("");
    if (!session?.user) return;
    const c = new AbortController();
    api
      .admission(c.signal)
      .then((r) => {
        if (!c.signal.aborted) setAdmitted(r.admitted);
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          if (e instanceof ApiError && e.status === 403) setAdmitted(false);
          else setAdmissionError(message(e));
        }
      });
    return () => c.abort();
  }, [session]);
  async function refresh() {
    const s = await api.session();
    setSession(s);
    setCsrfToken(s.csrf_token);
  }
  if (!session || !session.user)
    return (
      <div className="welcome">
        <header>
          <Brand />
          <span className="pill">Privater Wissensraum</span>
        </header>
        <main className="welcome-grid">
          <section>
            <p className="eyebrow">DEINE QUELLEN. DEINE GEDANKEN.</p>
            <h1>
              Aus Informationen
              <br />
              <em>Verständnis machen.</em>
            </h1>
            <p className="lead">
              Ein Ort für deine Dokumente, Fragen und Zusammenhänge. Organisiere
              dein Wissen in Notebooks und behalte die Quellen im Blick.
            </p>
            <div className="principles">
              <span>
                01 <strong>Quellen sammeln</strong>
              </span>
              <span>
                02 <strong>Zusammenhänge entdecken</strong>
              </span>
              <span>
                03 <strong>Belegt antworten</strong>
              </span>
            </div>
          </section>
          <section className="login-card">
            <span className="eyebrow">WILLKOMMEN ZURÜCK</span>
            <h2>Dein Notebook wartet.</h2>
            {sessionError || configError ? (
              <>
                <ErrorBox error={sessionError || configError} />
                <button onClick={() => setRetry((r) => r + 1)}>
                  Verbindung erneut prüfen
                </button>
              </>
            ) : !session || !config ? (
              <p role="status">Zugang wird geprüft …</p>
            ) : config?.password_login ? (
              <Login onLogin={refresh} />
            ) : (
              <p>Bitte wende dich für deinen Zugang an den Betreiber.</p>
            )}
            <p className="fine">
              {inviteToken
                ? "Melde dich mit der eingeladenen E-Mail-Adresse an, um die Einladung anzunehmen."
                : "Zugang nur mit persönlicher Einladung."}
            </p>
          </section>
        </main>
        <footer>Privat organisiert. Gemeinsam weitergedacht.</footer>
      </div>
    );
  if (!config || configError)
    return (
      <div className="welcome">
        <Brand />
        <section className="editor invitation">
          {configError ? (
            <>
              <ErrorBox error={configError} />
              <button onClick={() => setRetry((r) => r + 1)}>
                Verbindung erneut prüfen
              </button>
            </>
          ) : (
            <p role="status">Zugang wird geprüft …</p>
          )}
        </section>
      </div>
    );
  if (inviteToken && config.invitations)
    return (
      <div className="welcome">
        <Brand />
        <Invitation
          token={inviteToken}
          onAccepted={async () => {
            await refresh();
            setInviteToken("");
            window.history.replaceState(null, "", "/");
          }}
          onCancel={() => setInviteToken("")}
        />
      </div>
    );
  if (!admitted)
    return (
      <div className="welcome">
        <Brand />
        <section className="editor invitation">
          {admissionError ? (
            <>
              <ErrorBox error={admissionError} />
              <button onClick={() => setRetry((r) => r + 1)}>
                Erneut prüfen
              </button>
            </>
          ) : admitted === undefined ? (
            <p role="status">Zugang wird geprüft …</p>
          ) : (
            <>
              <h1>Deine Einladung ist der erste Schritt.</h1>
              <p>
                Öffne deinen persönlichen Einladungslink mit diesem Konto. Falls
                du keinen Link hast, bitte einen Workspace-Owner oder den
                Betreiber um eine Einladung.
              </p>
            </>
          )}
          <button
            className="text-button"
            onClick={async () => {
              try {
                await api.logout();
                setSession({ user: null });
                setCsrfToken();
              } catch (e) {
                setAdmissionError(message(e));
              }
            }}
          >
            Abmelden
          </button>
        </section>
      </div>
    );
  return (
    <WorkspaceApp
      key={session.user.id}
      config={config}
      session={session}
      onLogout={async () => {
        await api.logout();
        setSession({ user: null });
        setCsrfToken();
      }}
    />
  );
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null;
}
function Login({ onLogin }: { onLogin: () => Promise<void> }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api.login(String(data.get("email")), String(data.get("password")));
      await onLogin();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      <label>
        E-Mail
        <input
          name="email"
          type="email"
          autoComplete="username"
          required
          placeholder="du@beispiel.de"
        />
      </label>
      <label>
        Passwort
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>
      <ErrorBox error={error} />
      <button className="primary" disabled={busy}>
        {busy ? "Anmelden …" : "Anmelden →"}
      </button>
    </form>
  );
}
function WorkspaceApp({
  session,
  onLogout,
  config,
}: {
  config?: AuthConfig;
  session: Session;
  onLogout: () => Promise<void>;
}) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]),
    [workspace, setWorkspace] = useState(""),
    [notebooks, setNotebooks] = useState<Notebook[]>([]),
    [selected, setSelected] = useState(""),
    [settings, setSettings] = useState<"notebooks" | "connections" | "sharing">(
      "notebooks",
    ),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [reload, setReload] = useState(0),
    [edit, setEdit] = useState<Notebook | null>(null),
    [creating, setCreating] = useState(false),
    [deleting, setDeleting] = useState<Notebook | null>(null);
  useEffect(() => {
    const c = new AbortController();
    setLoading(true);
    api
      .workspaces(c.signal)
      .then((r) => {
        if (!c.signal.aborted) {
          setWorkspaces(r.items);
          setWorkspace((w) =>
            r.items.some((i) => i.id === w) ? w : (r.items[0]?.id ?? ""),
          );
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setError(message(e));
          setLoading(false);
        }
      });
    return () => c.abort();
  }, [reload]);
  useEffect(() => {
    setNotebooks([]);
    setSelected("");
    setEdit(null);
    setCreating(false);
    setDeleting(null);
    if (!workspace) return;
    const c = new AbortController();
    setLoading(true);
    setError("");
    api
      .notebooks(workspace, c.signal)
      .then((r) => {
        if (!c.signal.aborted) {
          setNotebooks(r.items);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setError(message(e));
          setLoading(false);
        }
      });
    return () => c.abort();
  }, [workspace, reload]);
  const current = workspaces.find((w) => w.id === workspace),
    notebook = notebooks.find((n) => n.id === selected),
    canEdit = current?.role === "owner" || current?.role === "editor";
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const title = String(new FormData(e.currentTarget).get("title")).trim();
    if (!title) return;
    await action(async () => {
      const n = edit
        ? await api.rename(edit.id, title)
        : await api.create(workspace, title);
      setNotebooks((list) =>
        edit ? list.map((x) => (x.id === n.id ? n : x)) : [...list, n],
      );
      setSelected(n.id);
      setEdit(null);
      setCreating(false);
    });
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <label className="workspace-label">
          WORKSPACE
          <select
            aria-label="Workspace"
            value={workspace}
            disabled={busy}
            onChange={(e) => {
              setSelected("");
              setWorkspace(e.target.value);
              setSettings("notebooks");
            }}
          >
            {!workspaces.length && <option value="">Kein Workspace</option>}
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
        <nav aria-label="Hauptnavigation">
          <button
            className={settings === "notebooks" ? "nav active" : "nav"}
            onClick={() => setSettings("notebooks")}
          >
            ▤ <span>Meine Notebooks</span>
            <span>{notebooks.length}</span>
          </button>
          <button
            className={settings === "connections" ? "nav active" : "nav"}
            onClick={() => setSettings("connections")}
          >
            ✧ <span>Meine KI</span>
          </button>
          {config?.members && current?.role === "owner" && (
            <button
              className={settings === "sharing" ? "nav active" : "nav"}
              onClick={() => setSettings("sharing")}
            >
              ♧ <span>Mitglieder</span>
            </button>
          )}
        </nav>
        <div className="sidebar-note">
          <span className="status-dot" />
          Dein privater Wissensraum
          <p>Teilen beginnt mit einer bewussten Entscheidung.</p>
        </div>
        <div className="account">
          <span className="avatar">
            {session.user!.email.slice(0, 1).toUpperCase()}
          </span>
          <span className="email">{session.user!.email}</span>
        </div>
        <button
          className="text-button"
          disabled={busy}
          onClick={() => {
            setSelected("");
            return action(onLogout);
          }}
        >
          Abmelden
        </button>
      </aside>
      <div className="main">
        <header className="topbar">
          <span>{current?.name ?? "Workspace"}</span>
          <span className="pill">
            {current
              ? { owner: "Owner", editor: "Editor", viewer: "Viewer" }[
                  current.role
                ]
              : "Kein Zugriff"}
          </span>
        </header>
        <main>
          <ErrorBox error={error} />
          {error && (
            <button
              className="text-button"
              onClick={() => setReload((x) => x + 1)}
            >
              Erneut laden
            </button>
          )}
          {settings === "connections" ? (
            <Connections />
          ) : settings === "sharing" &&
            config?.members &&
            current?.role === "owner" ? (
            <Sharing
              key={workspace}
              workspaceId={workspace}
              currentUserId={session.user!.id}
              onMembershipChange={() => {
                setSettings("notebooks");
                setReload((x) => x + 1);
              }}
            />
          ) : (
            <>
              <div className="heading">
                <div>
                  <p className="eyebrow">DEIN WISSEN, GUT AUFGEHOBEN</p>
                  <h1>
                    Meine Notebooks<span className="accent">.</span>
                  </h1>
                  <p className="muted">
                    Ein neuer Gedanke beginnt mit einem guten Ort.
                  </p>
                </div>
                <button
                  className="primary"
                  disabled={!canEdit || busy || loading}
                  onClick={() => {
                    setCreating(true);
                    setEdit(null);
                  }}
                >
                  ＋ Neues Notebook
                </button>
              </div>
              {loading ? (
                <p role="status">Notebooks werden geladen …</p>
              ) : !workspaces.length ? (
                <Empty
                  title="Noch kein Workspace"
                  text="Für dein Konto ist noch kein Workspace verfügbar. Bitte prüfe deine Einladung mit dem Betreiber."
                />
              ) : !notebooks.length ? (
                <Empty
                  title="Platz für deine nächste Idee"
                  text={
                    canEdit
                      ? "Erstelle dein erstes Notebook und gib deinem Thema einen Namen."
                      : "In diesem Workspace gibt es noch keine Notebooks. Ein Owner oder Editor kann eines erstellen."
                  }
                />
              ) : (
                <div className="notebook-grid">
                  {notebooks.map((n, i) => (
                    <article
                      className={"notebook-card tone-" + (i % 3)}
                      key={n.id}
                    >
                      <button
                        className="card-open"
                        onClick={() => setSelected(n.id)}
                        aria-label={`Notebook öffnen: ${n.title}`}
                      >
                        <span className="book-icon" aria-hidden="true">
                          ▤
                        </span>
                        <h2>{n.title}</h2>
                        <span>
                          Notebook öffnen <span aria-hidden="true">↗</span>
                        </span>
                      </button>
                      {canEdit && (
                        <div className="card-actions">
                          <button
                            disabled={busy}
                            onClick={() => {
                              setEdit(n);
                              setCreating(false);
                            }}
                          >
                            Umbenennen
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => setDeleting(n)}
                          >
                            Löschen
                          </button>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              )}
              {notebook && (
                <section className="notebook-detail">
                  <p className="eyebrow">AUSGEWÄHLTES NOTEBOOK</p>
                  <h2>{notebook.title}</h2>
                  <p className="muted">{current?.name}</p>
                  {config?.documents === true && (
                    <Sources notebookId={notebook.id} userId={session.user!.id} />
                  )}
                  {canEdit && (
                    <button onClick={() => setEdit(notebook)}>
                      Titel bearbeiten
                    </button>
                  )}
                </section>
              )}
            </>
          )}
          {(creating || edit) && (
            <section
              className="editor"
              aria-label={edit ? "Notebook umbenennen" : "Notebook erstellen"}
            >
              <h2>
                {edit ? "Notebook umbenennen" : "Eine neue Idee festhalten"}
              </h2>
              <form onSubmit={save} key={edit?.id ?? "new"}>
                <label>
                  Titel
                  <input
                    name="title"
                    required
                    maxLength={200}
                    autoFocus
                    defaultValue={edit?.title ?? ""}
                    placeholder="Zum Beispiel: Mein nächstes Projekt"
                  />
                </label>
                <div className="button-row">
                  <button className="primary" disabled={busy}>
                    {busy ? "Speichern …" : "Speichern"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEdit(null);
                      setCreating(false);
                    }}
                  >
                    Abbrechen
                  </button>
                </div>
              </form>
            </section>
          )}
          {deleting && (
            <section
              className="editor"
              role="alertdialog"
              aria-labelledby="delete-title"
            >
              <h2 id="delete-title">„{deleting.title}“ löschen?</h2>
              <p>Das Notebook und seine zugehörigen Inhalte werden gelöscht.</p>
              <div className="button-row">
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await api.remove(deleting.id);
                      setNotebooks((l) =>
                        l.filter((n) => n.id !== deleting.id),
                      );
                      setDeleting(null);
                      if (selected === deleting.id) setSelected("");
                    })
                  }
                >
                  Endgültig löschen
                </button>
                <button disabled={busy} onClick={() => setDeleting(null)}>
                  Abbrechen
                </button>
              </div>
            </section>
          )}
        </main>
        <footer>Notebook · Dein Wissen bleibt nachvollziehbar.</footer>
      </div>
    </div>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <section className="empty">
      <div className="empty-art" aria-hidden="true">
        ▤<span>✦</span>
      </div>
      <h2>{title}</h2>
      <p>{text}</p>
    </section>
  );
}
