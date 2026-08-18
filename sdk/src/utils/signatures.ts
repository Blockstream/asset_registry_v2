import { secp256k1 } from "@noble/curves/secp256k1";
import { hexToBytes, utf8ToBytes } from "@noble/curves/utils";
import { sha256 } from "@noble/hashes/sha2";
import { SignatureError } from "../errors/SignatureError.js";
import { ValidationError } from "../errors/ValidationError.js";
import { canonicalJson } from "./canonicalJson.js";

const BITCOIN_SIGNED_MESSAGE_MAGIC = utf8ToBytes("Bitcoin Signed Message:\n");
const COMPACT_SIZE_UINT16 = 0xfd;
const COMPACT_SIZE_UINT32 = 0xfe;
const COMPACT_SIZE_UINT64 = 0xff;
const UINT16_MAX = 0xffff;
const UINT32_MAX = 0xffffffff;
const UINT32_SIZE = 0x100000000;

export interface SignOptions {
  privateKey: string | Uint8Array;
}

export interface VerifyOptions {
  signature: string;
  pubkey: string;
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function bytesToBase64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes));
}

export function privateKeyToBytes(privateKey: string | Uint8Array): Uint8Array {
  let bytes: Uint8Array;
  try {
    bytes = typeof privateKey === "string" ? hexToBytes(privateKey) : privateKey;
  } catch {
    throw new ValidationError("Private key must be a 64-character hex string or 32 bytes", {
      field: "privateKey",
    });
  }
  if (bytes.byteLength !== 32) {
    throw new ValidationError("Private key must be 32 bytes", { field: "privateKey" });
  }
  return Uint8Array.from(bytes);
}

export async function generateKeyPair(): Promise<{ privateKey: Uint8Array; pubkey: string }> {
  const privateKey = secp256k1.utils.randomPrivateKey();
  return { privateKey, pubkey: bytesToHex(secp256k1.getPublicKey(privateKey)) };
}

export async function signData(data: string | Uint8Array | object, options: SignOptions): Promise<string> {
  const privateKey = privateKeyToBytes(options.privateKey);
  try {
    const signature = secp256k1.sign(bitcoinSignedMessageDigest(messageBytes(data)), privateKey);
    return bytesToBase64(signature.toBytes());
  } catch (error) {
    throw new SignatureError(`Signing failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

export async function verifySignature(data: string | Uint8Array | object, options: VerifyOptions): Promise<boolean> {
  if (!options.pubkey) {
    throw new ValidationError("Public key required for verification", { field: "pubkey" });
  }
  if (!options.signature) {
    throw new ValidationError("Signature required", { field: "signature" });
  }

  try {
    return secp256k1.verify(
      base64ToBytes(options.signature),
      bitcoinSignedMessageDigest(messageBytes(data)),
      hexToBytes(options.pubkey)
    );
  } catch {
    return false;
  }
}

function messageBytes(data: string | Uint8Array | object): Uint8Array {
  if (typeof data === "string") return utf8ToBytes(data);
  if (data instanceof Uint8Array) return data;
  return utf8ToBytes(canonicalJson(data));
}

function base64ToBytes(base64: string): Uint8Array {
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

function bitcoinSignedMessageDigest(message: Uint8Array): Uint8Array {
  return sha256(sha256(bitcoinSignedMessagePayload(message)));
}

function bitcoinSignedMessagePayload(message: Uint8Array): Uint8Array {
  const encodedLength = compactSize(message.byteLength);
  const payload = new Uint8Array(
    1 + BITCOIN_SIGNED_MESSAGE_MAGIC.byteLength + encodedLength.byteLength + message.byteLength
  );
  let offset = 0;
  payload[offset] = 0x18;
  offset += 1;
  payload.set(BITCOIN_SIGNED_MESSAGE_MAGIC, offset);
  offset += BITCOIN_SIGNED_MESSAGE_MAGIC.byteLength;
  payload.set(encodedLength, offset);
  offset += encodedLength.byteLength;
  payload.set(message, offset);
  return payload;
}

function compactSize(value: number): Uint8Array {
  if (value < COMPACT_SIZE_UINT16) return new Uint8Array([value]);
  if (value <= UINT16_MAX) {
    return new Uint8Array([COMPACT_SIZE_UINT16, value & 0xff, (value >> 8) & 0xff]);
  }
  if (value <= UINT32_MAX) {
    return new Uint8Array([
      COMPACT_SIZE_UINT32,
      value & 0xff,
      (value >> 8) & 0xff,
      (value >> 16) & 0xff,
      (value >> 24) & 0xff,
    ]);
  }
  const low = value >>> 0;
  const high = Math.floor(value / UINT32_SIZE);
  return new Uint8Array([
    COMPACT_SIZE_UINT64,
    low & 0xff,
    (low >> 8) & 0xff,
    (low >> 16) & 0xff,
    (low >> 24) & 0xff,
    high & 0xff,
    (high >> 8) & 0xff,
    (high >> 16) & 0xff,
    (high >> 24) & 0xff,
  ]);
}
