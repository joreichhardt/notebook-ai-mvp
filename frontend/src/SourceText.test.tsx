import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, type SourceDocument, type SourceText } from "./api";
import { Sources } from "./Sources";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, api: { ...actual.api, documents: vi.fn(), sourceText: vi.fn() } };
});
const source: SourceDocument = { id: "d", notebook_id: "n", version_id: "v", filename: "Notes.txt", status: "ready", error_code: null };
const page: SourceText = { document: source,
  items: [{ id: "c", ordinal: 0, page: null, text: "<img src=x onerror=alert(1)>", truncated: false }], next_ordinal: null };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.documents).mockResolvedValue({ items: [source] });
  vi.mocked(api.sourceText).mockResolvedValue(page);
});
afterEach(cleanup);
async function open() {
  const view = render(<Sources notebookId="n" userId="u" role="viewer" documentsEnabled />);
  await userEvent.click(await screen.findByRole("button", { name: "Text ansehen: Notes.txt" }));
  return view;
}
it("opens authorized source text as inert text, never HTML", async () => {
  const view = await open();
  expect(await screen.findByText(page.items[0].text)).toBeVisible();
  expect(view.container.querySelector("img,script")).toBeNull();
  expect(api.sourceText).toHaveBeenCalledWith("n", "d", "v", 0, expect.any(AbortSignal));
  await userEvent.click(screen.getByRole("button", { name: "Text schließen" }));
  expect(screen.queryByRole("region", { name: "Quellentext" })).not.toBeInTheDocument();
});
it("replaces rather than accumulates pages and labels clipped text", async () => {
  vi.mocked(api.sourceText).mockResolvedValueOnce({ ...page, next_ordinal: 20 })
    .mockResolvedValueOnce({ ...page, items: [{ id: "next", ordinal: 20, page: 2, text: "Next excerpt", truncated: true }] });
  await open();
  await userEvent.click(await screen.findByRole("button", { name: "Weitere Abschnitte" }));
  expect(await screen.findByText("Next excerpt")).toBeVisible();
  expect(screen.queryByText(page.items[0].text)).not.toBeInTheDocument();
  expect(screen.getByText("Seite 2 · Abschnitt 21")).toBeVisible();
  expect(screen.getByText("Dieser Textausschnitt ist gekürzt.")).toBeVisible();
  expect(api.sourceText).toHaveBeenLastCalledWith("n", "d", "v", 20, expect.any(AbortSignal));
});
it.each(["user", "notebook", "close"])("aborts and ignores late content after %s changes", async (change) => {
  let resolve!: (value: SourceText) => void;
  vi.mocked(api.sourceText).mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = await open();
  const signal = vi.mocked(api.sourceText).mock.calls[0][4]!;
  if (change === "close") await userEvent.click(screen.getByRole("button", { name: "Text schließen" }));
  else view.rerender(<Sources notebookId={change === "notebook" ? "n2" : "n"}
    userId={change === "user" ? "u2" : "u"} role="viewer" documentsEnabled />);
  expect(signal.aborted).toBe(true);
  await act(async () => { resolve(page); });
  expect(screen.queryByText(page.items[0].text)).not.toBeInTheDocument();
});
it("shows only safe access errors, not upstream diagnostics", async () => {
  vi.mocked(api.sourceText).mockRejectedValue(new ApiError(403, "denied", "private diagnostic"));
  await open();
  expect(await screen.findByRole("alert")).toHaveTextContent("Du hast keinen Zugriff auf diesen Quellentext.");
  expect(screen.queryByText("private diagnostic")).not.toBeInTheDocument();
});
it("does not offer an extracted preview for queued sources", async () => {
  vi.mocked(api.documents).mockResolvedValue({ items: [{ ...source, status: "queued" }] });
  render(<Sources notebookId="n" userId="u" role="viewer" documentsEnabled />);
  expect(await screen.findByText("In Warteschlange")).toBeVisible();
  expect(screen.queryByRole("button", { name: /Text ansehen/ })).not.toBeInTheDocument();
  expect(api.sourceText).not.toHaveBeenCalled();
});
