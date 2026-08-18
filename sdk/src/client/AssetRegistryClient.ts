import type { SignerBase } from "../signer/signer.js";
import { Signer } from "../signer/signer.js";
import type { IssuerAction } from "../types/actions.js";
import type { V2AssetContract } from "../types/index.js";
import { canonicalJson } from "../utils/canonicalJson.js";
import { DefaultHttpClient } from "../utils/http.js";
import {
  generateKeyPair,
  signData,
  verifySignature,
  type SignOptions,
  type VerifyOptions,
} from "../utils/signatures.js";
import { LegacyClient } from "./LegacyClient.js";
import { V2Client } from "./V2Client.js";

export interface ClientConfig {
  baseUrl: string;
  signer?: SignerBase;
  issuerPrivateKey?: string | Uint8Array;
  timeout?: number;
  maxRetries?: number;
  retryDelay?: number;
}

export class AssetRegistryClient {
  readonly legacy: LegacyClient;
  readonly v2: V2Client;
  readonly config: Readonly<ClientConfig>;

  constructor(config: ClientConfig) {
    if (!config.baseUrl) throw new Error("baseUrl is required");
    if (config.signer && config.issuerPrivateKey) {
      throw new Error("Configure either signer or issuerPrivateKey, not both");
    }

    this.config = Object.freeze({ ...config });
    const signer = config.signer ?? (config.issuerPrivateKey ? new Signer(config.issuerPrivateKey) : undefined);
    const httpClient = new DefaultHttpClient(config.baseUrl, {
      timeout: config.timeout,
      retry: { maxRetries: config.maxRetries, retryDelay: config.retryDelay },
    });
    this.legacy = new LegacyClient(httpClient);
    this.v2 = new V2Client(httpClient, signer);
  }

  static canonicalJson(value: unknown): string {
    return canonicalJson(value);
  }

  static normalizedRegistrationContractJson(contract: V2AssetContract): string {
    return V2Client.normalizedRegistrationContractJson(contract);
  }

  static async sign(data: string | Uint8Array | object, options: SignOptions): Promise<string> {
    return signData(data, options);
  }

  static async verify(data: string | Uint8Array | object, options: VerifyOptions): Promise<boolean> {
    return verifySignature(data, options);
  }

  static async generateKeyPair(): Promise<{ privateKey: Uint8Array; pubkey: string }> {
    return generateKeyPair();
  }

  static async signIssuerAction(
    action: IssuerAction,
    privateKey: string | Uint8Array
  ): Promise<{ canonicalJson: string; signature: string }> {
    const canonical = canonicalJson(action);
    const signature = await signData(canonical, { privateKey });
    return { canonicalJson: canonical, signature };
  }
}
