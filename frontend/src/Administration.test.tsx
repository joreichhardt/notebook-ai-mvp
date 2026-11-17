import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Administration } from "./Administration";
import { api } from "./api";

vi.mock("./api", () => ({ api: { invite: vi.fn(), administrators: vi.fn(), grantAdministrator: vi.fn() } }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.administrators).mockResolvedValue({ items: [] });
});

it("creates a distinct app invitation with no shared workspace or admin rights", async () => {
  vi.mocked(api.invite).mockResolvedValue({ id: "fixture", token: "synthetic-invitation-token", expires_at: "2030-01-01" });
  render(<Administration role="admin" />);
  expect(screen.queryByRole("heading", { name: "Admins ernennen" })).not.toBeInTheDocument();
  expect(api.administrators).not.toHaveBeenCalled();
  await userEvent.type(screen.getByLabelText("E-Mail für die App-Einladung"), "recipient@example.test");
  await userEvent.click(screen.getByRole("button", { name: "App-Einladungslink erstellen" }));
  await waitFor(() => expect(api.invite).toHaveBeenCalledWith("recipient@example.test", null, "viewer"));
  expect(await screen.findByLabelText("Persönlicher App-Einladungslink")).toHaveValue(`${window.location.origin}/invite#token=synthetic-invitation-token`);
});

it("shows no invitation link when the server rejects creation", async () => {
  vi.mocked(api.invite).mockRejectedValue(new Error("Keine Berechtigung"));
  render(<Administration role="admin" />);
  await userEvent.type(screen.getByLabelText("E-Mail für die App-Einladung"), "recipient@example.test");
  await userEvent.click(screen.getByRole("button", { name: "App-Einladungslink erstellen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Keine Berechtigung");
  expect(screen.queryByLabelText("Persönlicher App-Einladungslink")).not.toBeInTheDocument();
});

it("superadmins can appoint an existing user as admin but cannot choose superadmin", async () => {
  const uid = "00000000-0000-4000-8000-000000000001";
  vi.mocked(api.grantAdministrator).mockResolvedValue({ user_id: uid, role: "admin" });
  render(<Administration role="superadmin" />);
  await screen.findByRole("list", { name: "App-Administratoren" });
  await userEvent.type(screen.getByLabelText("Nutzer-ID für Adminrechte"), uid);
  await userEvent.click(screen.getByRole("button", { name: "Als Admin ernennen" }));
  expect(await screen.findByText("Adminrechte wurden vergeben.")).toBeInTheDocument();
  expect(api.grantAdministrator).toHaveBeenCalledWith(uid);
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByRole("list", { name: "App-Administratoren" })).toHaveTextContent(uid);
});

it("never claims a rejected admin grant succeeded", async () => {
  vi.mocked(api.grantAdministrator).mockRejectedValue(new Error("Nur Superadmins dürfen Adminrechte verwalten."));
  render(<Administration role="superadmin" />);
  await screen.findByRole("list", { name: "App-Administratoren" });
  await userEvent.type(screen.getByLabelText("Nutzer-ID für Adminrechte"), "00000000-0000-4000-8000-000000000002");
  await userEvent.click(screen.getByRole("button", { name: "Als Admin ernennen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Nur Superadmins");
  expect(screen.queryByText("Adminrechte wurden vergeben.")).not.toBeInTheDocument();
});
