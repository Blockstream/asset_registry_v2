import { RegistryError } from "./RegistryError.js";

/** Error thrown when an HTTP request fails. */
export class HttpError extends RegistryError {
  readonly statusCode: number;
  private readonly responseBody?: unknown;

  constructor(
    message: string,
    statusCode: number,
    body?: unknown,
    options?: { code?: string; details?: Record<string, unknown> }
  ) {
    super(message, {
      code: options?.code ?? "http_error",
      statusCode,
      details: options?.details,
    });
    this.name = "HttpError";
    this.statusCode = statusCode;
    Object.defineProperty(this, "responseBody", {
      value: body,
      enumerable: false,
      writable: false,
      configurable: false,
    });
  }

  get body(): unknown {
    return this.responseBody;
  }

  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      message: this.message,
      code: this.code,
      statusCode: this.statusCode,
      details: this.details,
    };
  }

  [Symbol.for("nodejs.util.inspect.custom")](): string {
    return JSON.stringify(this.toJSON(), null, 2);
  }

  isRetryable(): boolean {
    return (
      this.statusCode === 0 ||
      this.statusCode === 408 ||
      this.statusCode === 429 ||
      (this.statusCode >= 500 && this.statusCode < 600)
    );
  }
}

export function httpErrorFromResponse(statusCode: number, statusText: string, body: unknown): HttpError {
  const fallback = `HTTP ${statusCode}${statusText ? `: ${statusText}` : ""}`;
  if (!isRecord(body)) {
    return new HttpError(fallback, statusCode, body);
  }

  if (typeof body.error === "string") {
    return new HttpError(typeof body.message === "string" ? body.message : fallback, statusCode, body, {
      code: body.error,
      details: isRecord(body.details) ? body.details : undefined,
    });
  }

  if (typeof body.detail === "string") {
    return new HttpError(body.detail, statusCode, body);
  }

  if (Array.isArray(body.detail)) {
    const validationErrors = body.detail.filter(isRecord).map((error) => {
      const location = Array.isArray(error.loc) ? error.loc.map(String).join(".") : undefined;
      const message = typeof error.msg === "string" ? error.msg : undefined;
      return [location, message].filter(Boolean).join(": ");
    });
    return new HttpError(validationErrors.filter(Boolean).join("; ") || fallback, statusCode, body, {
      code: "validation_error",
      details: { validation_errors: validationErrors },
    });
  }

  return new HttpError(fallback, statusCode, body);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
