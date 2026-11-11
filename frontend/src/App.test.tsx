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
  it("keeps provider connections explicitly unavailable", async () => {
    render(<App />);
    await screen.findByText("Platz für deine nächste Idee");
    await userEvent.click(screen.getByRole("button", { name: /Meine KI/ }));
    expect(screen.getAllByText("Noch nicht verfügbar")).toHaveLength(4);
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
