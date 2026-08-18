import { HttpError, httpErrorFromResponse } from "../../src/errors/HttpError.ts";
import { inspect } from "node:util";

describe("HttpError", () => {
  it("creates error with status code", () => {
    const error = new HttpError("Bad Request", 400);
    expect(error.message).toBe("Bad Request");
    expect(error.statusCode).toBe(400);
  });

  it("has correct name", () => {
    const error = new HttpError("Bad Request", 400);
    expect(error.name).toBe("HttpError");
  });

  it("has http_error code", () => {
    const error = new HttpError("Bad Request", 400);
    expect((error as any).code).toBe("http_error");
  });

  it("stores response body", () => {
    const body = { error: "validation_failed", field: "email" };
    const error = new HttpError("Validation Error", 422, body);
    expect(error.body).toEqual(body);
  });

  it("preserves structured registry errors without exposing the raw body", () => {
    const body = {
      error: "validation_error",
      message: "issuer action failed validation",
      details: {
        errors: [
          {
            type: "unsupported_trading_venue",
            loc: ["replace_trading_venues", "trading_venues", 0, "venue"],
            ctx: { available_trading_venues: ["bitfinex", "sideswap"] },
            msg: "unsupported trading venue",
          },
        ],
      },
    };
    const error = httpErrorFromResponse(422, "Unprocessable Entity", body);

    expect(error.code).toBe("validation_error");
    expect(error.message).toBe("issuer action failed validation");
    expect(error.details).toEqual(body.details);
    expect(Object.keys(error)).not.toContain("body");
    expect(Object.keys(error)).not.toContain("responseBody");
    expect(JSON.stringify(error)).toContain('"available_trading_venues":["bitfinex","sideswap"]');
    expect(JSON.stringify(error)).not.toContain('"body"');
    expect(inspect(error)).toContain('"available_trading_venues"');
  });

  it("isRetryable returns true for 429", () => {
    const error = new HttpError("Rate Limited", 429);
    expect(error.isRetryable()).toBe(true);
  });

  it("isRetryable returns true for 500", () => {
    const error = new HttpError("Internal Server Error", 500);
    expect(error.isRetryable()).toBe(true);
  });

  it("isRetryable returns true for 503", () => {
    const error = new HttpError("Service Unavailable", 503);
    expect(error.isRetryable()).toBe(true);
  });

  it("isRetryable returns false for 400", () => {
    const error = new HttpError("Bad Request", 400);
    expect(error.isRetryable()).toBe(false);
  });

  it("isRetryable returns false for 401", () => {
    const error = new HttpError("Unauthorized", 401);
    expect(error.isRetryable()).toBe(false);
  });

  it("isRetryable returns false for 403", () => {
    const error = new HttpError("Forbidden", 403);
    expect(error.isRetryable()).toBe(false);
  });

  it("isRetryable returns false for 404", () => {
    const error = new HttpError("Not Found", 404);
    expect(error.isRetryable()).toBe(false);
  });

  it("isRetryable returns false for 422", () => {
    const error = new HttpError("Unprocessable Entity", 422);
    expect(error.isRetryable()).toBe(false);
  });
});
