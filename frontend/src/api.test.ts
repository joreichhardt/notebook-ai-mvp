import { afterEach, describe, expect, it, vi } from "vitest";
import { api, request, setCsrfToken } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken();
});
describe("API security boundary", () => {
  it("gives invitation-specific guidance on a rejected acceptance without exposing server text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "invitation_or_membership_invalid", message: "private upstream detail" },
    }), { status: 409 })));
    await expect(api.acceptInvitation("fixture")).rejects.toMatchObject({
      status: 409, code: "invitation_or_membership_invalid",
      message: "Die Einladung konnte nicht angenommen werden. Prüfe, ob du mit der bestätigten Empfängeradresse angemeldet bist. Ist das der Fall, wende dich an die einladende Person.",
    });
  });
  it("uploads exactly one typed file with cookie, CSRF, key and browser multipart boundary", async () => {
    const document = { id: "d", notebook_id: "n/id", version_id: "v", filename: "Notes.md", status: "queued", error_code: null };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ document, job: { id: "private" }, storage_key: "private" }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    setCsrfToken("csrf-test");
    const signal = new AbortController().signal;
    await expect(api.uploadDocument("n/id", new File(["# Notes"], "Notes.md"), "test-key", signal)).resolves.toEqual({ document });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/api/notebooks/n%2Fid/documents");
    expect(options).toMatchObject({ method: "POST", credentials: "same-origin", signal,
      headers: { Accept: "application/json", "X-CSRF-Token": "csrf-test", "Idempotency-Key": "test-key" } });
    expect(options.headers).not.toHaveProperty("Content-Type");
    expect([...options.body.keys()]).toEqual(["file"]);
    expect(options.body.get("file").type).toBe("text/markdown");
    expect(options.body.get("file").name).toBe("Notes.md");
  });
  it.each(["", "bad key", "é", "comma,key", "newline\n", "x".repeat(129)])("rejects invalid upload key %j without fetch", async (key) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(api.uploadDocument("n", new File(["text"], "n.txt"), key)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    ["n.pdf", "application/pdf"], ["n.txt", "application/octet-stream"], ["n.md", "text/plain"],
  ])("rejects unsupported file %s before fetch", async (name, type) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(api.uploadDocument("n", new File(["text"], name, { type }), "key")).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("enforces the actual byte bound and accepts the exact 25 MiB boundary", async () => {
    const document = { id: "d", notebook_id: "n", version_id: "v", filename: "n.txt", status: "queued", error_code: null };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ document }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    const bytes = new Uint8Array(25 * 1024 * 1024);
    await expect(api.uploadDocument("n", new File([bytes, "x"], "n.txt"), "key")).rejects.toThrow("25 MiB");
    expect(fetch).not.toHaveBeenCalled();
    await api.uploadDocument("n", new File([bytes], "n.txt"), "key");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].body.get("file").size).toBe(25 * 1024 * 1024);
    expect(fetch.mock.calls[0][1].body.get("file").type).toBe("text/plain");
  });
  it.each([200, 204, 409, 413, 422, 429, 503])("does not accept a non-202 upload response %s", async (status) => {
    const fetch = vi.fn().mockResolvedValue(new Response(status === 204 ? null : "private-detail", { status }));
    vi.stubGlobal("fetch", fetch);
    await expect(api.uploadDocument("n", new File(["text"], "n.txt"), "key")).rejects.not.toThrow("private-detail");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each(["notes.txt", "notes.md", "notes.markdown"])("derives an empty browser MIME type for %s", async (name) => {
    const document = { id: "d", notebook_id: "n", version_id: "v", filename: name, status: "queued", error_code: null };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ document }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    await api.uploadDocument("n", new File(["notes"], name), "key");
    expect(fetch.mock.calls[0][1].body.get("file").type).toBe(name.endsWith(".txt") ? "text/plain" : "text/markdown");
  });
  it("preserves cancellation without retrying a POST", async () => {
    const controller = new AbortController();
    controller.abort();
    const aborted = new DOMException("aborted", "AbortError");
    const fetch = vi.fn().mockRejectedValue(aborted);
    vi.stubGlobal("fetch", fetch);
    await expect(api.uploadDocument("n", new File(["notes"], "n.txt"), "key", controller.signal)).rejects.toBe(aborted);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects a cross-notebook receipt without consuming raw job data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      document: { id: "d", notebook_id: "other", filename: "n.txt", version_id: "v", status: "queued", error_code: null },
      job: { lease_token: "private-detail" },
    }), { status: 202 })));
    await expect(api.uploadDocument("n", new File(["text"], "n.txt"), "key")).rejects.toThrow("nicht bestätigt");
  });
  it.each([true, false, undefined])("consumes only explicit documents capability %s", async (documents) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      auth: { password_login: true }, invitations: true, members: true, documents,
    }))));
    expect((await api.authConfig()).documents).toBe(documents === true);
  });
  it("fetches only the contracted document list with cancellation and no mutation headers", async () => {
    const signal = new AbortController().signal;
    const fetch = vi.fn().mockResolvedValue(new Response('{"items":[]}'));
    vi.stubGlobal("fetch", fetch);
    setCsrfToken("csrf-test");
    await expect(api.documents("notebook/id", signal)).resolves.toEqual({ items: [] });
    expect(fetch).toHaveBeenCalledWith("/api/notebooks/notebook%2Fid/documents", expect.objectContaining({
      method: "GET", credentials: "same-origin", signal, headers: { Accept: "application/json" },
    }));
  });
  it("sends CSRF on mutations and keeps credentials same-origin", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: "n" })));
    vi.stubGlobal("fetch", fetch);
    setCsrfToken("csrf-test");
    await api.create("workspace/id", "Notebook");
    expect(fetch).toHaveBeenCalledWith(
      "/api/workspaces/workspace%2Fid/notebooks",
      expect.objectContaining({
        credentials: "same-origin",
        method: "POST",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-test" }),
        body: '{"title":"Notebook"}',
      }),
    );
  });
  it("never displays arbitrary error body or leaked upstream secret", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("secret-do-not-display", { status: 503 }),
        ),
    );
    await expect(request("/auth/session")).rejects.toThrow(
      "gerade nicht erreichbar",
    );
  });
  it("handles empty successful deletes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    await expect(api.remove("notebook")).resolves.toBeUndefined();
  });
  it("does not send CSRF on reads", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"user":null}'));
    vi.stubGlobal("fetch", fetch);
    setCsrfToken("csrf-test");
    await api.session();
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty("X-CSRF-Token");
  });
});
