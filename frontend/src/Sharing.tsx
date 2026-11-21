import { useEffect, useState, type FormEvent } from "react";
import { api, invitationDeliveryMessage, type Member, type Role } from "./api";
const roles: Record<Role, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
};
const errorMessage = (e: unknown) =>
  e instanceof Error
    ? e.message
    : "Die Aktion konnte nicht abgeschlossen werden.";
export function Invitation({
  token,
  onAccepted,
  onCancel,
}: {
  token: string;
  onAccepted: () => Promise<void>;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [accepted, setAccepted] = useState(false);
  async function accept() {
    setBusy(true);
    setError("");
    try {
      if (!accepted) {
        await api.acceptInvitation(token);
        setAccepted(true);
      }
      await onAccepted();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="editor invitation">
      <p className="eyebrow">DU BIST EINGELADEN</p>
      <h1>Gemeinsam weiterdenken.</h1>
      <p>
        Nimm die Einladung mit dem Konto an, dessen bestätigte E-Mail-Adresse
        zur Einladung gehört.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="button-row">
        <button className="primary" disabled={busy} onClick={accept}>
          {busy ? "Bitte warten …" : accepted ? "Sitzung erneut laden" : "Einladung annehmen"}
        </button>
        {!accepted && <button disabled={busy} onClick={onCancel}>Abbrechen</button>}
      </div>
    </section>
  );
}
export function Sharing({
  workspaceId,
  currentUserId,
  onMembershipChange,
}: {
  workspaceId: string;
  currentUserId: string;
  onMembershipChange: () => void;
}) {
  const [members, setMembers] = useState<Member[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [remove, setRemove] = useState<Member | null>(null);
  useEffect(() => {
    const c = new AbortController();
    setLoading(true);
    api
      .members(workspaceId, c.signal)
      .then((r) => {
        if (!c.signal.aborted) {
          setMembers(r.items);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setError(errorMessage(e));
          setLoading(false);
        }
      });
    return () => c.abort();
  }, [workspaceId]);
  async function run(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function invite(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setNotice("");
    await run(async () => {
      const result = await api.invite(
        String(data.get("email")),
        workspaceId,
        String(data.get("role")) as Role,
      );
      setNotice(invitationDeliveryMessage(result.delivery));
    });
  }
  return (
    <>
      <p className="eyebrow">WORKSPACE</p>
      <h1>
        Gemeinsam arbeiten<span className="accent">.</span>
      </h1>
      <p className="lead">Bestimme, wer mitlesen und mitgestalten darf.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <section className="editor">
        <h2>Mitglieder</h2>
        {loading ? (
          <p role="status">Mitglieder werden geladen …</p>
        ) : (
          <ul className="member-list">
            {members.map((m) => (
              <li key={m.user_id}>
                <div>
                  <strong>{m.email ?? m.user_id}</strong>
                  {m.user_id === currentUserId && (
                    <span className="pill">Du</span>
                  )}
                </div>
                <label className="sr-only" htmlFor={"role-" + m.user_id}>
                  Rolle für {m.email ?? m.user_id}
                </label>
                <select
                  id={"role-" + m.user_id}
                  disabled={busy}
                  value={m.role}
                  onChange={(e) => {
                    const role = e.target.value as Role;
                    void run(async () => {
                      await api.changeRole(workspaceId, m.user_id, role);
                      setMembers((list) =>
                        list.map((x) =>
                          x.user_id === m.user_id ? { ...x, role } : x,
                        ),
                      );
                      if (m.user_id === currentUserId) onMembershipChange();
                    });
                  }}
                >
                  {Object.entries(roles).map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
                </select>
                <button disabled={busy} onClick={() => setRemove(m)}>
                  Entfernen
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="editor">
        <h2>Eine Person einladen</h2>
        <p className="muted">
          Der Link wird per E-Mail versendet und gilt nur für die angegebene Adresse.
        </p>
        <form onSubmit={invite}>
          <label>
            E-Mail der eingeladenen Person
            <input type="email" required name="email" autoComplete="off" />
          </label>
          <label>
            Rolle
            <select name="role" defaultValue="viewer">
              {Object.entries(roles).map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" disabled={busy}>
            Einladung senden
          </button>
        </form>
        {notice && <p role="status">{notice}</p>}
      </section>
      {remove && (
        <section
          className="editor"
          role="alertdialog"
          aria-labelledby="remove-member-title"
        >
          <h2 id="remove-member-title">Mitglied entfernen?</h2>
          <p>
            {remove.email ?? remove.user_id} verliert den Zugriff auf diesen
            Workspace.
          </p>
          <div className="button-row">
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api.removeMember(workspaceId, remove.user_id);
                  setMembers((l) =>
                    l.filter((m) => m.user_id !== remove.user_id),
                  );
                  setRemove(null);
                  if (remove.user_id === currentUserId) onMembershipChange();
                })
              }
            >
              Zugriff entfernen
            </button>
            <button disabled={busy} onClick={() => setRemove(null)}>
              Abbrechen
            </button>
          </div>
        </section>
      )}
    </>
  );
}
