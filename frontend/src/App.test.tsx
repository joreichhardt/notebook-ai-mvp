import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api } from "./api";
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
