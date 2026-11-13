export type Provider = {
  id: string;
  label: string;
  kind: string;
  requires_api_key: boolean;
};
export type Connection = {
  id: string;
  provider_id: string;
  label: string;
  model: string | null;
  status: string;
  last_tested_at: string | null;
};
export type Role = "owner" | "editor" | "viewer";
export type Member = { user_id: string; role: Role; email?: string };
export type AuthConfig = {
  password_login: boolean;
  invitations: boolean;
  members: boolean;
  documents?: boolean;
};
export type User = { id: string; email: string };
export type Session = {
  user: User | null;
  csrf_token?: string;
  admitted?: boolean;
};
export type Workspace = {
  id: string;
  name: string;
  role: "owner" | "editor" | "viewer";
};
export type Notebook = { id: string; workspace_id: string; title: string };
export type SourceDocument = {
  id: string;
  notebook_id: string;
  filename: string;
  version_id: string;
  status: "queued" | "processing" | "ready" | "failed";
  error_code: string | null;
};
// The UI consumes only the member-safe document projection, never the job.
export type UploadReceipt = { document: SourceDocument };
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export function uploadMediaType(file: File): string {
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  const expected = extension === ".txt" ? "text/plain"
    : extension === ".md" || extension === ".markdown" ? "text/markdown" : "";
  if (!expected || (file.type && file.type.toLowerCase().trim() !== expected))
    throw new ApiError(422, "unsupported_media", "Bitte wähle eine TXT- oder Markdown-Datei.");
  if (file.size > MAX_UPLOAD_BYTES)
    throw new ApiError(413, "upload_too_large", "Die Datei darf höchstens 25 MiB groß sein.");
  return expected;
}
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
  upload?: { key: string },
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
  if (upload) headers["Idempotency-Key"] = upload.key;
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
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
      503: "Der Dienst ist gerade nicht erreichbar. Bitte versuche es später erneut.",
    };
    throw new ApiError(
      response.status,
      "request_failed",
      messages[response.status] ??
        "Die Anfrage konnte nicht abgeschlossen werden.",
    );
  }
  if (upload && response.status !== 202)
    throw new ApiError(0, "upload_unconfirmed", "Der Upload konnte nicht bestätigt werden.");
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const api = {
  admission: (signal?: AbortSignal) =>
    request<{ admitted: boolean }>("/auth/admission", "GET", undefined, signal),
  providers: (signal?: AbortSignal) =>
    request<{ items: Provider[] }>("/providers", "GET", undefined, signal),
  connections: (signal?: AbortSignal) =>
    request<{ items: Connection[] }>("/connections", "GET", undefined, signal),
  connect: (provider_id: string, label: string, api_key: string) =>
    request<Connection>("/connections", "POST", {
      provider_id,
      label,
      api_key,
    }),
  testConnection: (id: string) =>
    request<{ models: { id: string }[]; status: string }>(
      `/connections/${encodeURIComponent(id)}/test`,
      "POST",
    ),
  disconnect: (id: string) =>
    request<{ status: string; cleanup_pending: boolean } | undefined>(
      `/connections/${encodeURIComponent(id)}`,
      "DELETE",
    ),
  selectModel: (id: string, model: string) =>
    request<Connection>(`/connections/${encodeURIComponent(id)}`, "PATCH", {
      model,
    }),
  authConfig: (signal?: AbortSignal): Promise<AuthConfig> =>
    request<{
      auth: { password_login: boolean };
      invitations: boolean;
      members: boolean;
      documents?: boolean;
    }>("/capabilities", "GET", undefined, signal).then((c) => ({
      password_login: c.auth.password_login,
      invitations: c.invitations,
      members: c.members,
      documents: c.documents === true,
    })),
  members: (id: string, signal?: AbortSignal) =>
    request<{ items: Member[] }>(
      `/workspaces/${encodeURIComponent(id)}/members`,
      "GET",
      undefined,
      signal,
    ),
  changeRole: (workspace: string, user: string, role: Role) =>
    request(
      `/workspaces/${encodeURIComponent(workspace)}/members/${encodeURIComponent(user)}`,
      "PATCH",
      { role },
    ),
  removeMember: (workspace: string, user: string) =>
    request(
      `/workspaces/${encodeURIComponent(workspace)}/members/${encodeURIComponent(user)}`,
      "DELETE",
    ),
  invite: (email: string, workspace_id: string | null, role: Role) =>
    request<{ id: string; token: string; expires_at: string }>(
      "/invitations",
      "POST",
      { email, workspace_id, role },
    ),
  acceptInvitation: (token: string) =>
    request<{ workspace_id: string; invited_workspace_id: string | null }>(
      "/invitations/accept",
      "POST",
      { token },
    ),
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
  documents: (id: string, signal?: AbortSignal) =>
    request<{ items: SourceDocument[] }>(
      `/notebooks/${encodeURIComponent(id)}/documents`,
      "GET",
      undefined,
      signal,
    ),
  uploadDocument: async (id: string, file: File, key: string, signal?: AbortSignal): Promise<UploadReceipt> => {
    const media = uploadMediaType(file);
    if (!key.length || key.length > 128 || /[^A-Za-z0-9._:-]/.test(key))
      throw new ApiError(422, "invalid_key", "Der Upload konnte nicht vorbereitet werden.");
    const form = new FormData();
    // Browser types may be empty; preserve bytes without decoding their contents.
    form.append("file", file.type === media ? file : file.slice(0, file.size, media), file.name);
    const receipt = await request<UploadReceipt>(
      `/notebooks/${encodeURIComponent(id)}/documents`, "POST", form, signal, { key },
    );
    const doc = receipt?.document;
    if (!doc || doc.notebook_id !== id || typeof doc.id !== "string" ||
        typeof doc.version_id !== "string" || typeof doc.filename !== "string" ||
        !["queued", "processing", "ready", "failed"].includes(doc.status) ||
        !(doc.error_code === null || typeof doc.error_code === "string"))
      throw new ApiError(0, "upload_unconfirmed", "Der Upload konnte nicht bestätigt werden.");
    return { document: { id: doc.id, notebook_id: doc.notebook_id, filename: doc.filename,
      version_id: doc.version_id, status: doc.status, error_code: doc.error_code } };
  },
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
