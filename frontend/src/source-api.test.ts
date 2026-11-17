import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());
const document = { id: "d", notebook_id: "n", version_id: "v", filename: "Note.txt", status: "ready", error_code: null };
const item = { id: "c", ordinal: 0, page: null, text: "Extracted text", truncated: false };
const result = { document, items: [item], next_ordinal: null };
function respond(value: unknown) {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
it("fetches only the selected scope and projects away private extras", async () => {
  const fetch = respond({ ...result, job: "private", items: [{ ...item, storage_key: "private" }] });
  const signal = new AbortController().signal;
  await expect(api.sourceText("n", "d", "v", 0, signal)).resolves.toEqual(result);
  expect(fetch).toHaveBeenCalledWith("/api/notebooks/n/documents/d/text?after=0",
    expect.objectContaining({ credentials: "same-origin", method: "GET", signal }));
});
it.each(["id", "notebook_id", "version_id"])("rejects mismatched %s before display", async (field) => {
  respond({ ...result, document: { ...document, [field]: "foreign" } });
  await expect(api.sourceText("n", "d", "v")).rejects.toThrow("Quellentext konnte nicht bestätigt");
});
it.each([
  { items: Array(21).fill(item) },
  { items: [{ ...item, text: "x".repeat(8001) }] },
  { items: [{ ...item, page: 501 }] },
  { items: [{ ...item, ordinal: -1 }] },
  { next_ordinal: 0 },
])("rejects oversized or invalid page metadata", async (change) => {
  respond({ ...result, ...change });
  await expect(api.sourceText("n", "d", "v")).rejects.toThrow();
});
it("rejects invalid cursors without a request", async () => {
  const fetch = respond(result);
  await expect(api.sourceText("n", "d", "v", -1)).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
