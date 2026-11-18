import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, uploadMediaType, type Role, type SourceDocument } from "./api";
import { SourceChat } from "./SourceChat";
import { SourceTextPreview } from "./SourceText";

function uploadError(error: unknown) {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: return "Bitte melde dich erneut an.";
      case 403: return "Du hast für diesen Upload keine Berechtigung.";
      case 404: return "Dieses Notebook ist nicht mehr verfügbar.";
      case 409: return "Der Upload steht im Konflikt mit einer früheren Anfrage.";
      case 413: return "Die Datei darf höchstens 25 MiB groß sein.";
      case 422: return "Bitte prüfe die Datei: Erlaubt sind TXT und Markdown als UTF-8-Text.";
      case 429: return "Zu viele Anfragen. Bitte warte vor einem neuen Versuch.";
    }
  }
  return "Der Upload konnte nicht bestätigt werden. Versuche dieselbe Datei erneut oder verwerfe die Auswahl.";
}

function SourceUpload({ notebookId, onCommitted }: { notebookId: string; onCommitted: () => void }) {
  const [selection, setSelection] = useState<{ file: File; key: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempted, setAttempted] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => () => {
    generation.current += 1;
    pending.current?.abort();
  }, []);
  function clear() {
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setSelection(undefined);
    setBusy(false);
    setAttempted(false);
    if (input.current) input.current.value = "";
  }
  function choose(file?: File) {
    clear();
    setError("");
    setNotice("");
    if (!file) return;
    try {
      uploadMediaType(file);
      setSelection({ file, key: crypto.randomUUID() });
    } catch (failure) {
      setError(uploadError(failure));
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selection || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const current = ++generation.current;
    setBusy(true);
    setError("");
    setNotice("");
    setAttempted(true);
    try {
      await api.uploadDocument(notebookId, selection.file, selection.key, controller.signal);
      if (controller.signal.aborted || generation.current !== current) return;
      clear();
      setNotice("Quelle wurde hochgeladen. Die Verarbeitung kann noch ausstehen.");
      onCommitted();
    } catch (failure) {
      if (controller.signal.aborted || generation.current !== current) return;
      setError(uploadError(failure));
    } finally {
      if (generation.current === current) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  return <form onSubmit={submit} aria-label="Quelle hochladen" aria-busy={busy}>
    <label>TXT- oder Markdown-Datei
      <input ref={input} type="file" accept=".txt,.md,.markdown" disabled={busy || !!selection}
        onChange={(event) => choose(event.target.files?.[0])} />
    </label>
    <p className="fine">UTF-8-Text, höchstens 25 MiB. Die Datei wird serverseitig geprüft.</p>
    {selection && <p>{selection.file.name}</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Quelle wird hochgeladen …</p>}
    <div className="button-row">
      <button type="submit" disabled={busy || !selection}>
        {attempted ? "Upload erneut versuchen" : "Quelle hochladen"}
      </button>
      {selection && <button type="button" disabled={busy} onClick={() => {
        clear(); setError(""); setNotice("");
      }}>Auswahl verwerfen</button>}
    </div>
  </form>;
}

const statusLabels = {
  queued: "In Warteschlange",
  processing: "Wird verarbeitet",
  ready: "Bereit",
  failed: "Fehlgeschlagen",
};
function sourceError(code: string | null) {
  switch (code) {
    case "not_processed":
      return "Die Quelle wurde noch nicht verarbeitet.";
    case "processing_cancelled":
      return "Die Verarbeitung wurde abgebrochen.";
    case "processing_failed":
    default:
      return "Die Quelle konnte nicht verarbeitet werden.";
  }
}
function fetchError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Du hast keinen Zugriff auf diese Quellen.";
    if (error.status === 401) return "Bitte melde dich erneut an.";
    if (error.status === 404) return "Dieses Notebook ist nicht mehr verfügbar.";
  }
  return "Die Quellen konnten nicht geladen werden. Bitte versuche es erneut.";
}

// A scope-keyed child drops all data synchronously when the notebook or user changes.
export function Sources({ notebookId, userId, role, documentsEnabled, chatEnabled }: {
  notebookId: string; userId: string; role?: Role; documentsEnabled?: boolean; chatEnabled?: boolean;
}) {
  const canUpload = documentsEnabled === true && (role === "owner" || role === "editor");
  return <SourceList key={JSON.stringify([userId, notebookId])} notebookId={notebookId} canUpload={canUpload} chatEnabled={chatEnabled === true} />;
}
function SourceList({ notebookId, canUpload, chatEnabled }: { notebookId: string; canUpload: boolean; chatEnabled: boolean }) {
  const [items, setItems] = useState<SourceDocument[]>([]);
  const [selected, setSelected] = useState<SourceDocument>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.documents(notebookId, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      // Never display a row belonging to another notebook, even in a malformed response.
      setItems(result.items.filter((item) => item.notebook_id === notebookId));
      setLoading(false);
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return;
      setItems([]);
      setError(fetchError(failure));
      setLoading(false);
    });
    return () => controller.abort();
  }, [notebookId, revision]);
  function reload() {
    setSelected(undefined);
    setItems([]);
    setError("");
    setLoading(true);
    setRevision((value) => value + 1);
  }
  return (
    <section className="sources" aria-label="Quellen" aria-busy={loading}>
      <h3>Quellen</h3>
      {canUpload && <SourceUpload notebookId={notebookId} onCommitted={reload} />}
      {loading ? <p role="status">Quellen werden geladen …</p> : error ? (
        <p role="alert">{error}</p>
      ) : items.length === 0 ? (
        <p role="status">In diesem Notebook gibt es noch keine Quellen.</p>
      ) : (
        <ul className="source-list">
          {items.map((item) => (
            <li key={item.id}>
              <span className="source-filename">{item.filename}</span>
              <span className="source-status">{Object.hasOwn(statusLabels, item.status) ? statusLabels[item.status] : "Status unbekannt"}</span>
              {item.status === "failed" && <p>{sourceError(item.error_code)}</p>}
              {item.status === "ready" && <button type="button"
                aria-label={`Text ansehen: ${item.filename}`}
                onClick={() => setSelected(item)}>Text ansehen</button>}
            </li>
          ))}
        </ul>
      )}
      {selected && <SourceTextPreview key={JSON.stringify([selected.id, selected.version_id])}
        notebookId={notebookId} source={selected} onClose={() => setSelected(undefined)} />}
      {selected && chatEnabled && <SourceChat key={JSON.stringify([selected.id, selected.version_id])} source={selected} />}
      <button type="button" disabled={loading} onClick={reload}>
        {error ? "Quellen erneut laden" : "Quellen aktualisieren"}
      </button>
    </section>
  );
}
