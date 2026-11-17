import { useEffect, useState } from "react";
import { api, ApiError, type SourceDocument, type SourceText } from "./api";

export function SourceTextPreview({ notebookId, source, onClose }: {
  notebookId: string; source: SourceDocument; onClose: () => void;
}) {
  const [after, setAfter] = useState(0);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<SourceText>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setResult(undefined);
    setError("");
    api.sourceText(notebookId, source.id, source.version_id, after, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setResult(value); })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setResult(undefined);
        setError(failure instanceof ApiError && failure.status === 403
          ? "Du hast keinen Zugriff auf diesen Quellentext."
          : failure instanceof ApiError && failure.status === 401
            ? "Bitte melde dich erneut an."
            : "Der Quellentext konnte nicht geladen werden. Bitte aktualisiere die Quellen.");
      });
    return () => controller.abort();
  }, [notebookId, source.id, source.version_id, after, retry]);
  function page(cursor: number) {
    setResult(undefined);
    setError("");
    setAfter(cursor);
  }
  return <section aria-label="Quellentext" className="source-preview">
    <h4>Text: {source.filename}</h4>
    <button type="button" onClick={onClose}>Text schließen</button>
    {error ? <>
      <p role="alert">{error}</p>
      <button type="button" onClick={() => setRetry((value) => value + 1)}>Text erneut laden</button>
    </> : !result ? <p role="status">Quellentext wird geladen …</p>
      : result.document.status !== "ready" ? <p role="status">Die Quelle wurde noch nicht fertig verarbeitet.</p>
      : <>
        {!result.items.length && <p role="status">Für diesen Abschnitt liegt kein verarbeiteter Text vor.</p>}
        {result.items.map((chunk) => <article key={chunk.id} id={`chunk-${chunk.id}`}>
          <h5>{chunk.page === null ? `Abschnitt ${chunk.ordinal + 1}` : `Seite ${chunk.page} · Abschnitt ${chunk.ordinal + 1}`}</h5>
          <pre className="source-excerpt">{chunk.text}</pre>
          {chunk.truncated && <p className="fine">Dieser Textausschnitt ist gekürzt.</p>}
        </article>)}
        <div className="button-row">
          {after > 0 && <button type="button" onClick={() => page(0)}>Zum Anfang</button>}
          {result.next_ordinal !== null && <button type="button" onClick={() => page(result.next_ordinal!)}>Weitere Abschnitte</button>}
        </div>
      </>}
  </section>;
}
