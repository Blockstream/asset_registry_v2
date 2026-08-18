import { HttpError, httpErrorFromResponse } from "../errors/HttpError.js";
import { withRetry, type RetryOptions } from "./retry.js";

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

export interface RequestOptions {
  method: HttpMethod;
  path: string;
  body?: unknown;
  headers?: Record<string, string>;
  timeout?: number;
  retry?: boolean;
}

export interface HttpClientOptions {
  timeout?: number;
  retry?: RetryOptions;
}

export interface HttpClient {
  request<T>(options: RequestOptions): Promise<T>;
  get<T>(path: string, options?: Omit<Partial<RequestOptions>, "method" | "path">): Promise<T>;
  post<T>(
    path: string,
    body?: unknown,
    options?: Omit<Partial<RequestOptions>, "method" | "path" | "body">
  ): Promise<T>;
  put<T>(path: string, body?: unknown, options?: Omit<Partial<RequestOptions>, "method" | "path" | "body">): Promise<T>;
  delete<T>(path: string, options?: Omit<Partial<RequestOptions>, "method" | "path">): Promise<T>;
}

/** HTTP client implementation for modern browsers and Node.js 18+. */
export class DefaultHttpClient implements HttpClient {
  private readonly baseUrl: string;
  private readonly defaultTimeout: number;
  private readonly retryOptions: RetryOptions;

  constructor(baseUrl: string, options: HttpClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/g, "");
    this.defaultTimeout = options.timeout ?? 30_000;
    this.retryOptions = options.retry ?? {};
  }

  async request<T>(options: RequestOptions): Promise<T> {
    const shouldRetry = options.retry ?? options.method === "GET";
    const execute = () => this.requestOnce<T>(options);
    return shouldRetry ? withRetry(execute, this.retryOptions) : execute();
  }

  async get<T>(path: string, options?: Omit<Partial<RequestOptions>, "method" | "path">): Promise<T> {
    return this.request<T>({ method: "GET", path, ...options });
  }

  async post<T>(
    path: string,
    body?: unknown,
    options?: Omit<Partial<RequestOptions>, "method" | "path" | "body">
  ): Promise<T> {
    return this.request<T>({ method: "POST", path, body, ...options });
  }

  async put<T>(
    path: string,
    body?: unknown,
    options?: Omit<Partial<RequestOptions>, "method" | "path" | "body">
  ): Promise<T> {
    return this.request<T>({ method: "PUT", path, body, ...options });
  }

  async delete<T>(path: string, options?: Omit<Partial<RequestOptions>, "method" | "path">): Promise<T> {
    return this.request<T>({ method: "DELETE", path, ...options });
  }

  private async requestOnce<T>(options: RequestOptions): Promise<T> {
    const { method, path, body, headers = {}, timeout = this.defaultTimeout } = options;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: body === undefined ? headers : { "Content-Type": "application/json", ...headers },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const responseText = await response.text();
      const data = parseResponseBody(responseText);
      if (!response.ok) {
        throw httpErrorFromResponse(response.status, response.statusText, (data ?? responseText) || undefined);
      }
      return data as T;
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new HttpError("Request timeout", 408);
      }
      if (error instanceof HttpError) {
        throw error;
      }
      throw new HttpError(`Request failed: ${error instanceof Error ? error.message : String(error)}`, 0);
    } finally {
      clearTimeout(timeoutId);
    }
  }
}

function parseResponseBody(responseText: string): unknown {
  if (!responseText) {
    return undefined;
  }
  try {
    return JSON.parse(responseText) as unknown;
  } catch {
    return responseText;
  }
}
