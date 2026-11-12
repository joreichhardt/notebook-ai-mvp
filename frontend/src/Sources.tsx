import { useEffect, useState } from "react";
import { api, ApiError, type SourceDocument } from "./api";

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
export function Sources({ notebookId, userId }: { notebookId: string; userId: string }) {
  return <SourceList key={JSON.stringify([userId, notebookId])} notebookId={notebookId} />;
}
function SourceList({ notebookId }: { notebookId: string }) {
  const [items, setItems] = useState<SourceDocument[]>([]);
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
    setItems([]);
    setError("");
    setLoading(true);
    setRevision((value) => value + 1);
  }
  return (
    <section className="sources" aria-label="Quellen" aria-busy={loading}>
      <h3>Quellen</h3>
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
            </li>
          ))}
        </ul>
      )}
      <button type="button" disabled={loading} onClick={reload}>
        {error ? "Quellen erneut laden" : "Quellen aktualisieren"}
      </button>
    </section>
  );
}
