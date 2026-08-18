export interface RegistryErrorOptions {
  code?: string;
  statusCode?: number;
  details?: Record<string, unknown>;
}

/**
 * Base error class for all SDK errors.
 */
export class RegistryError extends Error {
  readonly code?: string;
  readonly statusCode?: number;
  readonly details?: Record<string, unknown>;

  constructor(message: string, options?: RegistryErrorOptions) {
    super(message);
    this.name = "RegistryError";
    this.code = options?.code;
    this.statusCode = options?.statusCode;
    this.details = options?.details;
  }
}
