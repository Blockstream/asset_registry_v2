import { secp256k1 } from "@noble/curves/secp256k1";
import { bytesToHex, privateKeyToBytes, signData } from "../utils/signatures.js";

export interface SignerBase {
  signData(data: string): Promise<string>;
  getPubkey?(): string;
}

export class Signer implements SignerBase {
  private readonly privateKey: Uint8Array;

  constructor(privateKey: string | Uint8Array) {
    this.privateKey = privateKeyToBytes(privateKey);
  }

  async signData(data: string): Promise<string> {
    return signData(data, { privateKey: this.privateKey });
  }

  getPubkey(): string {
    return bytesToHex(secp256k1.getPublicKey(this.privateKey));
  }
}
