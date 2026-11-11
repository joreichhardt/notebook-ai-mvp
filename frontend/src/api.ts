export type User = { id: string; email: string };
export type Session = { user: User | null; csrf_token?: string };
export type Workspace = {
  id: string;
  name: string;
  role: "owner" | "editor" | "viewer";
};
export type Notebook = { id: string; workspace_id: string; title: string };
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
let csrfToken: string | undefined;
export function setCsrfToken(token?: string) {
  csrfToken = token;
}
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(
      0,
      "network",
      "Der Server ist gerade nicht erreichbar. Bitte versuche es erneut.",
    );
  }
  if (!response.ok) {
    // Do not render arbitrary server text: it may include upstream diagnostics or secrets.
    const messages: Record<number, string> = {
      401: "Bitte melde dich erneut an.",
      403: "Du hast für diese Aktion keine Berechtigung.",
      404: "Dieser Inhalt ist nicht mehr verfügbar.",
      409: "Der Inhalt wurde inzwischen geändert. Bitte lade ihn neu.",
      422: "Bitte prüfe deine Eingaben.",
      429: "Zu viele Anfragen. Bitte warte einen Moment.",
      503: "Der Dienst ist noch nicht eingerichtet oder vorübergehend nicht verfügbar.",
    };
    throw new ApiError(
      response.status,
      "request_failed",
      messages[response.status] ??
        "Die Anfrage konnte nicht abgeschlossen werden.",
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const api = {
  session: (signal?: AbortSignal) =>
    request<Session>("/auth/session", "GET", undefined, signal),
  login: (email: string, password: string) =>
    request("/auth/login", "POST", { email, password }),
  logout: () => request("/auth/logout", "POST"),
  workspaces: (signal?: AbortSignal) =>
    request<{ items: Workspace[] }>("/workspaces", "GET", undefined, signal),
  notebooks: (id: string, signal?: AbortSignal) =>
    request<{ items: Notebook[] }>(
      `/workspaces/${encodeURIComponent(id)}/notebooks`,
      "GET",
      undefined,
      signal,
    ),
  create: (workspace: string, title: string) =>
    request<Notebook>(
      `/workspaces/${encodeURIComponent(workspace)}/notebooks`,
      "POST",
      { title },
    ),
  rename: (id: string, title: string) =>
    request<Notebook>(`/notebooks/${encodeURIComponent(id)}`, "PATCH", {
      title,
    }),
  remove: (id: string) =>
    request<void>(`/notebooks/${encodeURIComponent(id)}`, "DELETE"),
};
