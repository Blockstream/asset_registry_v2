import { RegistryError } from "./RegistryError.js";

export interface SignatureErrorDetails {
  reason?: string;
  expectedContext?: string;
  [key: string]: unknown;
}

/**
 * Error thrown when signature verification fails.
 */
export class SignatureError extends RegistryError {
  constructor(message: string, details?: SignatureErrorDetails) {
    super(message, {
      code: "signature_error",
      details: details as Record<string, unknown> | undefined,
    });
    this.name = "SignatureError";
  }
}
