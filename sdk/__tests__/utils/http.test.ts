import { DefaultHttpClient, type HttpClient } from "../../src/utils/http.ts";

describe("DefaultHttpClient", () => {
  let client: HttpClient;

  beforeEach(() => {
    client = new DefaultHttpClient("https://api.example.com");
  });

  describe("constructor", () => {
    it("creates client without args", () => {
      const c = new DefaultHttpClient("");
      expect(c).toBeDefined();
    });

    it("uses custom baseUrl", () => {
      const customClient = new DefaultHttpClient("https://custom.api.com/v1");
      expect(customClient).toBeDefined();
    });

    it("uses custom timeout", () => {
      const customClient = new DefaultHttpClient("https://api.example.com", { timeout: 60000 });
      expect(customClient).toBeDefined();
    });
  });

  describe("request", () => {
    it("normalizes trailing slashes in the base URL", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), { status: 200 })
      );
      const normalizedClient = new DefaultHttpClient("https://api.example.com///");

      await normalizedClient.get("/v2/assets");

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/assets",
        expect.objectContaining({ method: "GET" })
      );
      fetchMock.mockRestore();
    });

    it("makes a GET request", async () => {
      const mockResponse = {
        ok: true,
        status: 200,
        json: jest.fn().mockResolvedValue({ success: true }),
        text: jest.fn().mockResolvedValue(JSON.stringify({ success: true })),
      };

      const clientWithFetch = new DefaultHttpClient("https://api.example.com", { timeout: 1000 });
      const originalFetch = global.fetch;
      global.fetch = jest.fn().mockResolvedValue(mockResponse);

      try {
        const result = await clientWithFetch.request({
          path: "/test",
          method: "GET",
        });

        expect(result).toEqual({ success: true });
      } finally {
        global.fetch = originalFetch;
      }
    });

    it("makes a POST request with body", async () => {
      const mockResponse = {
        ok: true,
        status: 201,
        text: jest.fn().mockResolvedValue(JSON.stringify({ created: true })),
      };

      const mockFetch = jest.fn().mockResolvedValue(mockResponse);
      const clientWithFetch = new DefaultHttpClient("https://api.example.com", { timeout: 1000 });
      const originalFetch = global.fetch;
      global.fetch = mockFetch;

      try {
        const body = { key: "value" };
        await clientWithFetch.request({
          path: "/test",
          method: "POST",
          body,
        });

        // Verify body was sent
        const callArgs = mockFetch.mock.calls[0][1];
        expect(callArgs.body).toBeDefined();
      } finally {
        global.fetch = originalFetch;
      }
    });

    it("throws HttpError on non-OK response", async () => {
      const mockResponse = {
        ok: false,
        status: 400,
        text: jest.fn().mockResolvedValue(JSON.stringify({ error: { message: "Bad Request" } })),
      };

      const clientWithFetch = new DefaultHttpClient("https://api.example.com", { timeout: 1000 });
      const originalFetch = global.fetch;
      global.fetch = jest.fn().mockResolvedValue(mockResponse);

      try {
        await expect(
          clientWithFetch.request({
            path: "/test",
            method: "GET",
          })
        ).rejects.toThrow();
      } finally {
        global.fetch = originalFetch;
      }
    });

    it("parses registry error envelopes", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: "asset_not_found", message: "asset not found", details: { asset_id: "aa" } }),
          { status: 404 }
        )
      );
      const noRetryClient = new DefaultHttpClient("https://api.example.com", {
        retry: { maxRetries: 0 },
      });

      await expect(noRetryClient.get("/missing")).rejects.toMatchObject({
        name: "HttpError",
        code: "asset_not_found",
        message: "asset not found",
        statusCode: 404,
        details: { asset_id: "aa" },
      });
      fetchMock.mockRestore();
    });

    it("retries transient GET failures but not ordinary POST requests", async () => {
      const fetchMock = jest
        .spyOn(global, "fetch")
        .mockResolvedValueOnce(new Response("failure", { status: 503 }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
        .mockResolvedValueOnce(new Response("failure", { status: 503 }));
      const retryingClient = new DefaultHttpClient("https://api.example.com", {
        retry: { maxRetries: 1, retryDelay: 0 },
      });

      await expect(retryingClient.get<{ ok: boolean }>("/retry")).resolves.toEqual({ ok: true });
      await expect(retryingClient.post("/no-retry", {})).rejects.toMatchObject({ statusCode: 503 });
      expect(fetchMock).toHaveBeenCalledTimes(3);
      fetchMock.mockRestore();
    });

    it("uses helper methods (get, post, put, delete)", async () => {
      const mockResponse = {
        ok: true,
        status: 200,
        text: jest.fn().mockResolvedValue(JSON.stringify({ success: true })),
      };

      const mockFetch = jest.fn().mockResolvedValue(mockResponse);
      const clientWithFetch = new DefaultHttpClient("https://api.example.com", { timeout: 1000 });
      const originalFetch = global.fetch;
      global.fetch = mockFetch;

      try {
        await clientWithFetch.get("/test");
        expect(mockFetch).toHaveBeenCalledTimes(1);
        expect(mockFetch.mock.calls[0][1].method).toBe("GET");
      } finally {
        global.fetch = originalFetch;
      }
    });
  });
});
