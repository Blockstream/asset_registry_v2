import { RegistryError } from "./RegistryError.js";

export interface ValidationDetails {
  field?: string;
  reason?: string;
  [key: string]: unknown;
}

/**
 * Error thrown when input validation fails.
 */
export class ValidationError extends RegistryError {
  constructor(message: string, details?: ValidationDetails) {
    super(message, {
      code: "validation_error",
      details: details as Record<string, unknown> | undefined,
    });
    this.name = "ValidationError";
  }
}
