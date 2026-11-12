import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { Sources } from "./Sources";
import { api, ApiError, type SourceDocument } from "./api";
vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    api: {
      session: vi.fn(),
      authConfig: vi.fn(),
      admission: vi.fn(),
      providers: vi.fn(),
      connections: vi.fn(),
      acceptInvitation: vi.fn(),
      members: vi.fn(),
      invite: vi.fn(),
      changeRole: vi.fn(),
      removeMember: vi.fn(),
      connect: vi.fn(),
      testConnection: vi.fn(),
      disconnect: vi.fn(),
      selectModel: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
      workspaces: vi.fn(),
      notebooks: vi.fn(),
      documents: vi.fn(),
      create: vi.fn(),
      rename: vi.fn(),
      remove: vi.fn(),
    },
  };
});
const session = {
  user: { id: "u", email: "person@example.test" },
  csrf_token: "test-csrf",
};
beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
  vi.mocked(api.authConfig).mockResolvedValue({
    password_login: true,
    invitations: true,
    members: true,
  });
  vi.mocked(api.admission).mockResolvedValue({ admitted: true });
  vi.mocked(api.providers).mockResolvedValue({ items: [] });
  vi.mocked(api.connections).mockResolvedValue({ items: [] });
  vi.mocked(api.members).mockResolvedValue({ items: [] });
  vi.mocked(api.session).mockResolvedValue(session);
  vi.mocked(api.workspaces).mockResolvedValue({
    items: [{ id: "w", name: "Mein Workspace", role: "owner" }],
  });
  vi.mocked(api.notebooks).mockResolvedValue({ items: [] });
  vi.mocked(api.documents).mockResolvedValue({ items: [] });
});
describe("Notebook workspace", () => {
  it("creates a notebook only after server success", async () => {
    vi.mocked(api.create).mockResolvedValue({
      id: "n",
      workspace_id: "w",
      title: "Forschung",
    });
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    await userEvent.click(
      screen.getByRole("button", { name: /Neues Notebook/ }),
    );
    await userEvent.type(screen.getByLabelText("Titel"), "Forschung");
    await userEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(
      await screen.findByRole("button", { name: "Notebook öffnen: Forschung" }),
    ).toBeVisible();
    expect(api.create).toHaveBeenCalledWith("w", "Forschung");
  });
  it("disables writing for viewers", async () => {
    vi.mocked(api.workspaces).mockResolvedValue({
      items: [{ id: "w", name: "Team", role: "viewer" }],
    });
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    expect(
      screen.getByRole("button", { name: /Neues Notebook/ }),
    ).toBeDisabled();
  });
  it("reports unavailable service instead of a simulated login", async () => {
    vi.mocked(api.session).mockRejectedValue(
      new Error("Dienst nicht eingerichtet"),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Dienst nicht eingerichtet",
    );
    expect(
      screen.queryByRole("button", { name: /Neues Notebook/ }),
    ).not.toBeInTheDocument();
  });
  it("hides unsupported provider forms without development notices", async () => {
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    await userEvent.click(screen.getByRole("button", { name: /Meine KI/ }));
    expect(await screen.findByText("Kein Anbieter verfügbar")).toBeVisible();
    expect(
      screen.queryByText(
        /freigeschaltet|nächsten Schritt|ersten Stand|Noch nicht verfügbar/,
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API.Key/)).not.toBeInTheDocument();
  });
  it("logs in via backend and reloads session", async () => {
    vi.mocked(api.session)
      .mockResolvedValueOnce({ user: null })
      .mockResolvedValue(session);
    vi.mocked(api.login).mockResolvedValue({});
    render(<App />);
    await screen.findByLabelText("E-Mail");
    await userEvent.type(
      screen.getByLabelText("E-Mail"),
      "person@example.test",
    );
    await userEvent.type(screen.getByLabelText("Passwort"), "a-secret");
    await userEvent.click(screen.getByRole("button", { name: /Anmelden →/ }));
    await waitFor(() =>
      expect(api.login).toHaveBeenCalledWith("person@example.test", "a-secret"),
    );
    await screen.findByText("Platz für deine nächste Idee");
  });
});

describe("Sharing and capability discovery", () => {
  it("hides login when password auth is unavailable", async () => {
    vi.mocked(api.session).mockResolvedValue({ user: null });
    vi.mocked(api.authConfig).mockResolvedValue({
      password_login: false,
      invitations: true,
      members: true,
    });
    render(<App />);
    await screen.findByText(
      "Bitte wende dich für deinen Zugang an den Betreiber.",
    );
    expect(screen.queryByLabelText("Passwort")).not.toBeInTheDocument();
  });
  it("accepts a fragment invitation with explicit action and removes token from URL", async () => {
    window.history.replaceState(null, "", "/invite#token=private-token");
    vi.mocked(api.acceptInvitation).mockResolvedValue({
      workspace_id: "w",
      invited_workspace_id: null,
    });
    render(<App />);
    await screen.findByRole("button", { name: "Einladung annehmen" });
    expect(window.location.hash).toBe("");
    expect(api.acceptInvitation).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Einladung annehmen" }),
    );
    await screen.findByText("Platz für deine nächste Idee");
    expect(api.acceptInvitation).toHaveBeenCalledWith("private-token");
  });
  it("creates recipient-bound workspace invitations", async () => {
    vi.mocked(api.invite).mockResolvedValue({
      id: "i",
      token: "secret-token",
      expires_at: "2026-12-01",
    });
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    await userEvent.click(screen.getByRole("button", { name: /Mitglieder/ }));
    await screen.findByText("Eine Person einladen");
    await userEvent.type(
      screen.getByLabelText("E-Mail der eingeladenen Person"),
      "friend@example.test",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Einladungslink erstellen" }),
    );
    const link = await screen.findByLabelText("Persönlicher Einladungslink");
    expect(link).toHaveValue(
      window.location.origin + "/invite#token=secret-token",
    );
    expect(api.invite).toHaveBeenCalledWith(
      "friend@example.test",
      "w",
      "viewer",
    );
  });
  it("does not expose owner member management to viewers", async () => {
    vi.mocked(api.workspaces).mockResolvedValue({
      items: [{ id: "w", name: "Team", role: "viewer" }],
    });
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    expect(
      screen.queryByRole("button", { name: /Mitglieder/ }),
    ).not.toBeInTheDocument();
  });
  it("stores a personal connection and clears secret input", async () => {
    vi.mocked(api.providers).mockResolvedValue({
      items: [
        {
          id: "p",
          label: "Cloud-Anbieter",
          kind: "openai",
          requires_api_key: true,
        },
      ],
    });
    vi.mocked(api.connect).mockResolvedValue({
      id: "c",
      provider_id: "p",
      label: "Mein Zugang",
      model: null,
      status: "untested",
      last_tested_at: null,
    });
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    await userEvent.click(screen.getByRole("button", { name: /Meine KI/ }));
    await screen.findByLabelText("Anbieter");
    await userEvent.selectOptions(screen.getByLabelText("Anbieter"), "p");
    await userEvent.type(
      screen.getByLabelText("Name der Verbindung"),
      "Mein Zugang",
    );
    await userEvent.type(
      screen.getByLabelText("API-Schlüssel"),
      "never-persist",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Verbindung speichern" }),
    );
    await screen.findByText("Mein Zugang");
    expect(api.connect).toHaveBeenCalledWith(
      "p",
      "Mein Zugang",
      "never-persist",
    );
    expect(screen.queryByLabelText("API-Schlüssel")).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
  });
});

it("keeps disconnected metadata visible when credential cleanup is pending", async () => {
  const connection = {
    id: "c",
    provider_id: "p",
    label: "Mein Zugang",
    model: null,
    status: "active",
    last_tested_at: null,
  };
  vi.mocked(api.connections)
    .mockResolvedValueOnce({ items: [connection] })
    .mockResolvedValue({ items: [{ ...connection, status: "disconnected" }] });
  vi.mocked(api.disconnect).mockResolvedValue({
    status: "disconnected",
    cleanup_pending: true,
  });
  render(<App />);
  await screen.findByText("Platz für deine nächste Idee");
  await userEvent.click(screen.getByRole("button", { name: /Meine KI/ }));
  await screen.findByText("Mein Zugang");
  await userEvent.click(screen.getByRole("button", { name: "Trennen" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Verbindung trennen" }),
  );
  expect(
    await screen.findByRole("button", { name: "Entfernen erneut versuchen" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Verbindung testen" }),
  ).toBeDisabled();
});

it("retries capability discovery after transient failure and reveals login", async () => {
  vi.mocked(api.session).mockResolvedValue({ user: null });
  vi.mocked(api.authConfig)
    .mockRejectedValueOnce(new Error("private upstream diagnostic"))
    .mockResolvedValue({
      password_login: true,
      invitations: true,
      members: true,
    });
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Die Verbindung konnte nicht geprüft werden. Bitte versuche es erneut.",
  );
  expect(
    screen.queryByText("private upstream diagnostic"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText("Bitte wende dich für deinen Zugang an den Betreiber."),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Verbindung erneut prüfen" }),
  );
  expect(await screen.findByLabelText("Passwort")).toBeVisible();
  expect(api.authConfig).toHaveBeenCalledTimes(2);
  expect(api.session).toHaveBeenCalledTimes(2);
});
it("shows a loading state while capabilities are pending", async () => {
  vi.mocked(api.session).mockResolvedValue({ user: null });
  vi.mocked(api.authConfig).mockReturnValue(new Promise(() => {}));
  render(<App />);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Zugang wird geprüft",
  );
  expect(
    screen.queryByText("Bitte wende dich für deinen Zugang an den Betreiber."),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Passwort")).not.toBeInTheDocument();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const source = (overrides: Partial<SourceDocument> = {}): SourceDocument => ({
  id: "d", notebook_id: "n", version_id: "v", filename: "Notizen.md",
  status: "ready", error_code: null, ...overrides,
});
function sourceSetup(documents: boolean | undefined = true) {
  vi.mocked(api.authConfig).mockResolvedValue({
    password_login: true, invitations: true, members: true, documents,
  });
  vi.mocked(api.notebooks).mockResolvedValue({ items: [
    { id: "n", workspace_id: "w", title: "Erstes" },
    { id: "n2", workspace_id: "w", title: "Zweites" },
  ] });
}
async function openNotebook(title = "Erstes") {
  await userEvent.click(await screen.findByRole("button", { name: `Notebook öffnen: ${title}` }));
}
describe("Current source list", () => {
  it("drops rows on user change and ignores stale results after unmount", async () => {
    const pending = deferred<{ items: SourceDocument[] }>();
    vi.mocked(api.documents).mockResolvedValueOnce({ items: [source()] })
      .mockReturnValueOnce(pending.promise);
    const { rerender, unmount } = render(<Sources notebookId="n" userId="first" />);
    expect(await screen.findByText("Notizen.md")).toBeVisible();
    rerender(<Sources notebookId="n" userId="second" />);
    expect(screen.queryByText("Notizen.md")).not.toBeInTheDocument();
    expect(vi.mocked(api.documents).mock.calls[0][1]!.aborted).toBe(true);
    unmount();
    expect(vi.mocked(api.documents).mock.calls[1][1]!.aborted).toBe(true);
    await act(async () => { pending.resolve({ items: [source({ filename: "Fremd.md" })] }); });
    expect(screen.queryByText("Fremd.md")).not.toBeInTheDocument();
  });
  it.each([false, undefined])("does not request or announce sources with capability %s", async (enabled) => {
    sourceSetup();
    vi.mocked(api.authConfig).mockResolvedValue({
      password_login: true, invitations: true, members: true, documents: enabled,
    });
    render(<App />);
    await openNotebook();
    expect(api.documents).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Quellen" })).not.toBeInTheDocument();
    expect(screen.queryByText(/freigeschaltet|Rollout|Noch nicht verfügbar/)).not.toBeInTheDocument();
  });
  it("renders all four German statuses and treats hostile filenames and errors as inert data", async () => {
    sourceSetup();
    const filename = '<img src=x onerror="alert(1)">.md';
    vi.mocked(api.documents).mockResolvedValue({ items: [
      source({ id: "1", status: "queued" }),
      source({ id: "2", status: "processing" }),
      source({ id: "3", status: "ready" }),
      source({ id: "4", filename, status: "failed", error_code: "<script>secret</script>" }),
    ] });
    const { container } = render(<App />);
    await openNotebook();
    for (const label of ["In Warteschlange", "Wird verarbeitet", "Bereit", "Fehlgeschlagen"])
      expect(await screen.findByText(label)).toBeVisible();
    expect(screen.getByText(filename)).toBeVisible();
    expect(screen.getByText("Die Quelle konnte nicht verarbeitet werden.")).toBeVisible();
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.queryByText(/<script>secret/)).not.toBeInTheDocument();
    expect(api.documents).toHaveBeenCalledWith("n", expect.any(AbortSignal));
  });
  it.each([
    ["not_processed", "Die Quelle wurde noch nicht verarbeitet."],
    ["processing_cancelled", "Die Verarbeitung wurde abgebrochen."],
    ["processing_failed", "Die Quelle konnte nicht verarbeitet werden."],
  ])("maps only the finite safe processing code %s", async (code, text) => {
    sourceSetup();
    vi.mocked(api.documents).mockResolvedValue({ items: [source({ status: "failed", error_code: code })] });
    render(<App />);
    await openNotebook();
    expect(await screen.findByText(text)).toBeVisible();
  });
  it("shows loading, safe fetch error, fetch retry and empty state", async () => {
    sourceSetup();
    const pending = deferred<{ items: SourceDocument[] }>();
    vi.mocked(api.documents).mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error("private diagnostics"))
      .mockResolvedValue({ items: [] });
    render(<App />);
    await openNotebook();
    expect(screen.getByText("Quellen werden geladen …")).toBeVisible();
    await act(async () => { pending.resolve({ items: [] }); });
    expect(await screen.findByText("In diesem Notebook gibt es noch keine Quellen.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Quellen aktualisieren" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Die Quellen konnten nicht geladen werden.");
    expect(screen.queryByText("private diagnostics")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Quellen erneut laden" }));
    expect(await screen.findByText("In diesem Notebook gibt es noch keine Quellen.")).toBeVisible();
  });
  it("clears previously visible sources before refetch and keeps them cleared on 403", async () => {
    sourceSetup();
    vi.mocked(api.documents).mockResolvedValueOnce({ items: [source()] })
      .mockRejectedValueOnce(new ApiError(403, "forbidden", "private diagnostic"));
    render(<App />);
    await openNotebook();
    expect(await screen.findByText("Notizen.md")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Quellen aktualisieren" }));
    expect(screen.queryByText("Notizen.md")).not.toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Du hast keinen Zugriff auf diese Quellen.");
    expect(screen.queryByText("private diagnostic")).not.toBeInTheDocument();
  });
  it("aborts old notebook requests and ignores their later response", async () => {
    sourceSetup();
    const old = deferred<{ items: SourceDocument[] }>();
    vi.mocked(api.documents).mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ items: [source({ notebook_id: "n2", filename: "Aktuell.md" })] });
    render(<App />);
    await openNotebook();
    const signal = vi.mocked(api.documents).mock.calls[0][1]!;
    await openNotebook("Zweites");
    expect(signal.aborted).toBe(true);
    expect(await screen.findByText("Aktuell.md")).toBeVisible();
    await act(async () => { old.resolve({ items: [source({ filename: "Alt.md" })] }); });
    expect(screen.queryByText("Alt.md")).not.toBeInTheDocument();
    expect(screen.getByText("Aktuell.md")).toBeVisible();
  });
  it("clears visible rows immediately on notebook and workspace switch", async () => {
    sourceSetup();
    vi.mocked(api.workspaces).mockResolvedValue({ items: [
      { id: "w", name: "Mein Workspace", role: "owner" },
      { id: "w2", name: "Anderer Workspace", role: "viewer" },
    ] });
    vi.mocked(api.documents).mockResolvedValueOnce({ items: [source()] })
      .mockReturnValue(new Promise(() => {}));
    render(<App />);
    await openNotebook();
    expect(await screen.findByText("Notizen.md")).toBeVisible();
    await openNotebook("Zweites");
    expect(screen.queryByText("Notizen.md")).not.toBeInTheDocument();
    const signal = vi.mocked(api.documents).mock.calls[1][1]!;
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Workspace" }), "w2");
    expect(signal.aborted).toBe(true);
    expect(screen.queryByRole("region", { name: "Quellen" })).not.toBeInTheDocument();
  });
  it("clears and cancels sources when logout starts, before its response", async () => {
    sourceSetup();
    const pending = deferred<unknown>();
    vi.mocked(api.logout).mockReturnValue(pending.promise);
    vi.mocked(api.documents).mockResolvedValue({ items: [source()] });
    render(<App />);
    await openNotebook();
    expect(await screen.findByText("Notizen.md")).toBeVisible();
    const signal = vi.mocked(api.documents).mock.calls[0][1]!;
    await userEvent.click(screen.getByRole("button", { name: "Abmelden" }));
    expect(screen.queryByText("Notizen.md")).not.toBeInTheDocument();
    expect(signal.aborted).toBe(true);
    await act(async () => { pending.resolve(undefined); });
  });
});
