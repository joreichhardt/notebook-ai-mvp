import { useEffect, useState, type FormEvent } from "react";
import { api, type Connection, type Provider } from "./api";
const message = (e: unknown) =>
  e instanceof Error
    ? e.message
    : "Die Aktion konnte nicht abgeschlossen werden.";
export function Connections() {
  const [providers, setProviders] = useState<Provider[]>([]),
    [connections, setConnections] = useState<Connection[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState(""),
    [models, setModels] = useState<Record<string, string[]>>({}),
    [removing, setRemoving] = useState<Connection | null>(null),
    [replacing, setReplacing] = useState<Connection | null>(null),
    [reload, setReload] = useState(0),
    [notice, setNotice] = useState("");
  useEffect(() => {
    const c = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([api.providers(c.signal), api.connections(c.signal)])
      .then(([p, r]) => {
        if (!c.signal.aborted) {
          setProviders(p.items);
          setConnections(r.items);
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
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function connect(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget,
      data = new FormData(form);
    const key = String(data.get("api_key") ?? "");
    const label = String(data.get("label") ?? "").trim();
    form.reset();
    await run(async () => {
      const result = await api.connect(selected, label, key);
      setConnections((list) => [...list, result]);
      setSelected("");
      setNotice(
        "Verbindung gespeichert. Prüfe jetzt den Zugang und wähle ein Modell.",
      );
    });
  }
  return (
    <>
      <p className="eyebrow">EINSTELLUNGEN</p>
      <h1>
        Meine KI<span className="accent">.</span>
      </h1>
      <p className="lead">Verbinde deine persönlichen Modellzugänge.</p>
      <p className="muted">
        Deine Zugänge bleiben dir zugeordnet. Andere Workspace-Mitglieder können
        sie nicht verwenden.
      </p>
      {error && (
        <>
          <p className="error" role="alert">
            {error}
          </p>
          <button onClick={() => setReload((r) => r + 1)}>Erneut laden</button>
        </>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <p role="status">Verbindungen werden geladen …</p>
      ) : (
        <>
          {connections.length > 0 && (
            <section aria-label="Deine Verbindungen" className="provider-grid">
              {connections.map((c) => (
                <article className="provider" key={c.id}>
                  <h2>{c.label}</h2>
                  <p>
                    {providers.find((p) => p.id === c.provider_id)?.label ??
                      c.provider_id}
                  </p>
                  {c.status === "disconnected" && (
                    <p className="notice">
                      Getrennt. Die gespeicherten Zugangsdaten müssen noch
                      entfernt werden.
                    </p>
                  )}
                  <p className="connection-meta">
                    {c.last_tested_at
                      ? "Zuletzt geprüft: " +
                        new Date(c.last_tested_at).toLocaleString("de-DE")
                      : "Zugang noch nicht geprüft"}
                  </p>
                  {c.model && (
                    <p>
                      Modell: <strong>{c.model}</strong>
                    </p>
                  )}
                  <div className="button-row">
                    <button
                      disabled={busy || c.status === "disconnected"}
                      onClick={() =>
                        run(async () => {
                          const r = await api.testConnection(c.id);
                          setModels((m) => ({
                            ...m,
                            [c.id]: r.models.map((m) => m.id),
                          }));
                          setNotice(
                            "Verbindung geprüft. " +
                              r.models.length +
                              " Modelle gefunden.",
                          );
                          const list = await api.connections();
                          setConnections(list.items);
                        })
                      }
                    >
                      Verbindung testen
                    </button>
                    {c.status !== "disconnected" && (
                      <button disabled={busy} onClick={() => setReplacing(c)}>
                        Schlüssel ersetzen
                      </button>
                    )}
                    <button disabled={busy} onClick={() => setRemoving(c)}>
                      {c.status === "disconnected"
                        ? "Entfernen erneut versuchen"
                        : "Trennen"}
                    </button>
                  </div>
                  <p className="fine">
                    Der Test liest die Modellliste über diesen Zugang; er
                    erzeugt keine Antwort.
                  </p>
                  {c.status !== "disconnected" && models[c.id]?.length > 0 && (
                    <label>
                      Modell auswählen
                      <select
                        aria-label={"Modell für " + c.label}
                        disabled={busy}
                        value={c.model ?? ""}
                        onChange={(e) => {
                          const model = e.target.value;
                          if (model)
                            void run(async () => {
                              const result = await api.selectModel(c.id, model);
                              setConnections((list) =>
                                list.map((x) =>
                                  x.id === result.id ? result : x,
                                ),
                              );
                            });
                        }}
                      >
                        <option value="" disabled>
                          Modell auswählen
                        </option>
                        {models[c.id].map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </article>
              ))}
            </section>
          )}
          {providers.length > 0 ? (
            <section className="editor">
              <h2>Modellzugang hinzufügen</h2>
              <label>
                Anbieter
                <select
                  value={selected}
                  disabled={busy}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  <option value="">Anbieter auswählen</option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              {selected && (
                <form key={selected} onSubmit={connect}>
                  <label>
                    Name der Verbindung
                    <input
                      name="label"
                      required
                      maxLength={120}
                      placeholder="Zum Beispiel: Mein Modellzugang"
                    />
                  </label>
                  {providers.find((p) => p.id === selected)
                    ?.requires_api_key && (
                    <label>
                      API-Schlüssel
                      <input
                        name="api_key"
                        type="password"
                        required
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                  )}
                  <p className="fine">
                    Verwende deinen eigenen Zugang. Beim Speichern wird noch
                    keine Modellanfrage ausgeführt.
                  </p>
                  <button className="primary" disabled={busy}>
                    {busy ? "Speichern …" : "Verbindung speichern"}
                  </button>
                </form>
              )}
            </section>
          ) : (
            !error && (
              <section className="empty">
                <h2>Kein Anbieter verfügbar</h2>
                <p>
                  Bitte den Betreiber, einen Anbieter für deinen Modellzugang
                  freizugeben.
                </p>
              </section>
            )
          )}
        </>
      )}
      {replacing && (
        <section key={replacing.id} className="editor" role="dialog" aria-labelledby="replace-key-title">
          <h2 id="replace-key-title">Schlüssel für „{replacing.label}“ ersetzen</h2>
          <form onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const key = String(new FormData(form).get("replacement_key") ?? "");
            form.reset();
            void run(async () => {
              const result = await api.replaceConnectionKey(replacing.id, key);
              setConnections((list) => list.map((item) => item.id === result.id ? result : item));
              setModels((items) => ({ ...items, [result.id]: [] }));
              setReplacing(null);
              setNotice("Schlüssel ersetzt. Prüfe jetzt die Verbindung erneut.");
            });
          }}>
            <label>
              Neuer API-Schlüssel
              <input name="replacement_key" type="password" autoComplete="off" required maxLength={16384} />
            </label>
            <div className="button-row">
              <button disabled={busy}>Neuen Schlüssel speichern</button>
              <button type="button" disabled={busy} onClick={() => setReplacing(null)}>Abbrechen</button>
            </div>
          </form>
        </section>
      )}
      {removing && (
        <section
          className="editor"
          role="alertdialog"
          aria-labelledby="disconnect-title"
        >
          <h2 id="disconnect-title">„{removing.label}“ trennen?</h2>
          <p>
            Dieser Zugang kann danach hier nicht mehr verwendet werden. Einen
            beim Anbieter erstellten API-Schlüssel kannst du dort zusätzlich
            widerrufen.
          </p>
          <div className="button-row">
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const result = await api.disconnect(removing.id);
                  setConnections((list) =>
                    result?.cleanup_pending
                      ? list.map((c) =>
                          c.id === removing.id
                            ? { ...c, status: "disconnected" }
                            : c,
                        )
                      : list.filter((c) => c.id !== removing.id),
                  );
                  setRemoving(null);
                  setNotice(
                    result?.cleanup_pending
                      ? "Verbindung getrennt. Bitte versuche das Entfernen der gespeicherten Zugangsdaten erneut."
                      : "Verbindung getrennt.",
                  );
                  const list = await api.connections();
                  setConnections(list.items);
                })
              }
            >
              Verbindung trennen
            </button>
            <button disabled={busy} onClick={() => setRemoving(null)}>
              Abbrechen
            </button>
          </div>
        </section>
      )}
    </>
  );
}
