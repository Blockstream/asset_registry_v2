import { sha256 } from "@noble/hashes/sha2";
import { ValidationError } from "../errors/ValidationError.js";
import { bytesToHex } from "./signatures.js";

const MAX_ICON_BYTES = 75_000;
const BASE64_CHUNK_BYTES = 0x8000;

export function hashIconBytes(bytes: Uint8Array): string {
  validateIconBytes(bytes);
  return bytesToHex(sha256(bytes));
}

export function iconBytesToBase64(bytes: Uint8Array): string {
  validateIconBytes(bytes);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK_BYTES) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK_BYTES));
  }
  return btoa(binary);
}

function validateIconBytes(bytes: Uint8Array): void {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0) {
    throw new ValidationError("Icon must be supplied as non-empty Uint8Array bytes", { field: "icon" });
  }
  if (bytes.byteLength >= MAX_ICON_BYTES) {
    throw new ValidationError(`Icon must be smaller than ${MAX_ICON_BYTES} bytes`, { field: "icon" });
  }
}
