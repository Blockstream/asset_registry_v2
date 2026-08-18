import { ValidationError } from "../errors/ValidationError.js";

export function validateNonEmptyString(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ValidationError(`${field} must be a non-empty string`, { field });
  }
}

export function validateHexString(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || !/^[0-9a-fA-F]+$/.test(value)) {
    throw new ValidationError(`${field} must contain only hex characters`, { field });
  }
}

export function validateHexLength(value: unknown, field: string, expectedLength: number): asserts value is string {
  validateHexString(value, field);
  if (value.length !== expectedLength) {
    throw new ValidationError(`${field} must be ${expectedLength} characters, got ${value.length}`, { field });
  }
}

export function validateAssetId(assetId: unknown): asserts assetId is string {
  validateHexLength(assetId, "assetId", 64);
}

export function validateContractHash(hash: unknown): asserts hash is string {
  validateHexLength(hash, "contractHash", 64);
}

export function validatePubkey(pubkey: unknown): asserts pubkey is string {
  validateHexLength(pubkey, "pubkey", 66);
  if (!pubkey.startsWith("02") && !pubkey.startsWith("03")) {
    throw new ValidationError("pubkey must be a compressed secp256k1 public key", { field: "pubkey" });
  }
}

export function validateUuid(value: unknown, field: string): asserts value is string {
  if (
    typeof value !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
  ) {
    throw new ValidationError(`${field} must be a valid UUID`, { field });
  }
}

export function validateTimestamp(timestamp: unknown): asserts timestamp is string {
  if (typeof timestamp !== "string" || Number.isNaN(Date.parse(timestamp))) {
    throw new ValidationError("timestamp must be a valid ISO 8601 timestamp", { field: "timestamp" });
  }
}
