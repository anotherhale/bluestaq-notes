import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HttpNotesApi } from "./client";
import { ApiError } from "./errors";

/** Build a fake Response with a JSON body. */
function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("HttpNotesApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the demo identity header once an identity is selected", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, { id: "u1", email: "a@b.c", display_name: "A", status: "active" }),
    );
    const api = new HttpNotesApi("http://backend.test");
    api.selectIdentity("11111111-1111-1111-1111-111111111111");

    await api.getCurrentUser();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Demo-User-ID"]).toBe("11111111-1111-1111-1111-111111111111");
  });

  it("omits the demo header and reports 401 when no identity is selected", async () => {
    const api = new HttpNotesApi("http://backend.test");

    expect(await api.getCurrentUser()).toBeNull();

    await expect(api.listNotes()).rejects.toMatchObject({
      status: 401,
      code: "unauthenticated",
    });
    // No request should have been attempted without an identity.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("parses the backend error envelope into an ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        409,
        {
          error: {
            code: "conflict",
            message: "note has been modified by another client; current version is 3",
            details: [{ loc: ["body", "version"], msg: "stale", type: "conflict" }],
          },
          request_id: "req-123",
        },
        { "X-Request-ID": "req-123" },
      ),
    );
    const api = new HttpNotesApi("http://backend.test");
    api.selectIdentity("u1");

    const error = await api.getNote("note-1").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.code).toBe("conflict");
    expect(apiError.isConflict).toBe(true);
    expect(apiError.requestId).toBe("req-123");
    expect(apiError.details[0]?.loc).toEqual(["body", "version"]);
  });

  it("returns undefined for a 204 No Content response", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const api = new HttpNotesApi("http://backend.test");
    api.selectIdentity("u1");

    await expect(api.deleteNote("note-1")).resolves.toBeUndefined();
  });

  it("treats a 404 on demo identities as 'demo disabled', not an error", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { error: { code: "not_found", message: "not found" } }),
    );
    const api = new HttpNotesApi("http://backend.test");

    const result = await api.getDemoIdentities();

    expect(result.enabled).toBe(false);
    expect(result.items).toEqual([]);
  });

  it("maps a transport failure to a status-0 ApiError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const api = new HttpNotesApi("http://backend.test");
    api.selectIdentity("u1");

    const error = await api.listNotes().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
  });

  it("strips trailing slashes from the configured base URL", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { items: [], meta: { total: 0, limit: 20, offset: 0 } }));
    const api = new HttpNotesApi("http://backend.test/");
    api.selectIdentity("u1");

    await api.listNotes();

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("http://backend.test/api/v1/notes");
  });
});
