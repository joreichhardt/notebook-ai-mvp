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
  chat?: boolean;
};
export type PlatformRole = "admin" | "superadmin";
export type Administrator = { user_id: string; role: PlatformRole };
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
export type SourceText = {
  document: SourceDocument;
  items: { id: string; ordinal: number; page: number | null; text: string; truncated: boolean }[];
  next_ordinal: number | null;
};
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
    if (path === "/invitations/accept" && method === "POST" && response.status === 409) {
      const error = await response.json().catch(() => null);
      if (error?.detail?.code === "invitation_or_membership_invalid") {
        throw new ApiError(409, "invitation_or_membership_invalid",
          "Die Einladung konnte nicht angenommen werden. Prüfe, ob du mit der bestätigten Empfängeradresse angemeldet bist. Ist das der Fall, wende dich an die einladende Person.");
      }
      throw new ApiError(409, "request_failed", "Die Einladung konnte nicht angenommen werden.");
    }
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
export type InvitationDelivery = "accepted_by_smtp" | "unconfirmed";

export function invitationDeliveryMessage(delivery: InvitationDelivery): string {
  return delivery === "accepted_by_smtp"
    ? "Der Mailserver hat die Einladung angenommen. Der Postfacheingang ist nicht bestätigt."
    : "Einladung erstellt, aber der Mailversand konnte nicht bestätigt werden.";
}

export const api = {
  admission: (signal?: AbortSignal) =>
    request<{ admitted: boolean; platform_role?: PlatformRole | null }>(
      "/auth/admission", "GET", undefined, signal,
    ),
  administrators: (signal?: AbortSignal) =>
    request<{ items: Administrator[] }>("/admin/administrators", "GET", undefined, signal),
  grantAdministrator: (user_id: string) =>
    request<Administrator>("/admin/administrators", "POST", { user_id }),
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
  replaceConnectionKey: (id: string, api_key: string) =>
    request<Connection>(`/connections/${encodeURIComponent(id)}/key`, "PUT", { api_key }),
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
      chat?: boolean;
    }>("/capabilities", "GET", undefined, signal).then((c) => ({
      password_login: c.auth.password_login,
      invitations: c.invitations,
      members: c.members,
      documents: c.documents === true,
      ...(c.chat === true ? { chat: true } : {}),
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
    request<{ id: string; expires_at: string; delivery: InvitationDelivery }>(
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
  sourceText: async (notebook: string, document: string, version: string, after = 0, signal?: AbortSignal): Promise<SourceText> => {
    if (!Number.isInteger(after) || after < 0 || after > 2_000_000)
      throw new ApiError(422, "invalid_cursor", "Ungültiger Textabschnitt.");
    const result = await request<SourceText>(
      `/notebooks/${encodeURIComponent(notebook)}/documents/${encodeURIComponent(document)}/text?after=${after}`,
      "GET", undefined, signal,
    );
    const doc = result?.document;
    if (!doc || doc.id !== document || doc.notebook_id !== notebook || doc.version_id !== version ||
        !["queued", "processing", "ready", "failed"].includes(doc.status) ||
        typeof doc.filename !== "string" || !(doc.error_code === null || typeof doc.error_code === "string") ||
        !Array.isArray(result.items) || result.items.length > 20 ||
        !(result.next_ordinal === null || (Number.isInteger(result.next_ordinal) && result.next_ordinal > after && result.next_ordinal <= 2_000_000)) ||
        result.items.some((item) => !item || typeof item.id !== "string" ||
          !Number.isInteger(item.ordinal) || item.ordinal < after || item.ordinal > 2_000_000 ||
          !(item.page === null || (Number.isInteger(item.page) && item.page >= 1 && item.page <= 500)) ||
          typeof item.text !== "string" || !item.text.length || item.text.length > 8000 || typeof item.truncated !== "boolean"))
      throw new ApiError(409, "source_changed", "Der Quellentext konnte nicht bestätigt werden. Bitte lade die Quellen neu.");
    return {
      document: { id: doc.id, notebook_id: doc.notebook_id, version_id: doc.version_id,
        filename: doc.filename, status: doc.status, error_code: doc.error_code },
      items: result.items.map(({ id, ordinal, page, text, truncated }) => ({ id, ordinal, page, text, truncated })),
      next_ordinal: result.next_ordinal,
    };
  },
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
