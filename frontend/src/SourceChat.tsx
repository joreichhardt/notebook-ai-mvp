import { useEffect, useState } from "react";
import { api, request, type Connection, type SourceDocument } from "./api";

type Answer = { answer: string; citation: { notebook_id: string; document_id: string; version_id: string; chunk_id: string; ordinal: number; quote: string } };

export function SourceChat({ source }: { source: SourceDocument }) {
  const [connections, setConnections] = useState<Connection[]>([]), [id, setId] = useState("");
  const [question, setQuestion] = useState(""), [result, setResult] = useState<Answer>(), [passage, setPassage] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => { const c = new AbortController(); api.connections(c.signal).then(r => {
    if (!c.signal.aborted) setConnections(r.items.filter(v => v.status === "active" && v.model));
  }).catch(() => { if (!c.signal.aborted) setError("Verbindungen konnten nicht geladen werden."); }); return () => c.abort(); }, []);
  async function ask() {
    const connection = connections.find(c => c.id === id);
    if (!connection?.model || busy || !question.trim()) return;
    setBusy(true); setError(""); setResult(undefined); setPassage("");
    try {
      const r = await request<Answer>(`/notebooks/${encodeURIComponent(source.notebook_id)}/chat`, "POST", {
        document_id: source.id, connection_id: id, model: connection.model, question,
      });
      if (r.citation.notebook_id !== source.notebook_id || r.citation.document_id !== source.id || r.citation.version_id !== source.version_id || !r.citation.quote?.trim() || typeof r.answer !== 'string') throw Error();
      setResult(r);
    } catch { setError("Keine belegte Antwort verfügbar."); } finally { setBusy(false); }
  }
  async function verify() {
    if (!result || busy) return;
    setBusy(true); setPassage(""); setError("");
    try { const c = result.citation;
      const page = await api.sourceText(source.notebook_id, source.id, source.version_id, c.ordinal);
      const chunk = page.items.find(i => i.id === c.chunk_id && i.ordinal === c.ordinal && i.text.includes(c.quote));
      if (!chunk) throw Error(); setPassage(chunk.text);
    } catch { setResult(undefined); setError("Der Beleg ist nicht mehr verfügbar."); } finally { setBusy(false); }
  }
  return <section aria-label="Quellenchat"><h4>Frage an diese Quelle</h4><form onSubmit={e => { e.preventDefault(); void ask(); }}>
    <label>Persönliche KI-Verbindung<select value={id} disabled={busy} onChange={e => setId(e.target.value)}><option value="">Verbindung wählen</option>{connections.map(c => <option key={c.id} value={c.id}>{c.label} · {c.model}</option>)}</select></label>
    <label>Frage<textarea maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} /></label>
    <p>Die Frage und Auszüge aus {source.filename} werden an die ausgewählte Verbindung gesendet.</p>
    <button disabled={busy || !id || !question.trim()}>Frage senden</button></form>
    {error && <p role="alert">{error}</p>}{result && <article><p>{result.answer}</p><button disabled={busy} onClick={verify}>Beleg in {source.filename} prüfen: „{result.citation.quote}“</button></article>}{passage && <blockquote>{passage}</blockquote>}
  </section>;
}
