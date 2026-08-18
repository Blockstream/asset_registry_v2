import type { SignerBase } from "../signer/signer.js";
import { canonicalJson } from "../utils/canonicalJson.js";
import type { HttpClient } from "../utils/http.js";

export abstract class BaseClient {
  protected readonly httpClient: HttpClient;
  protected readonly signer?: SignerBase;

  constructor(httpClient: HttpClient, signer?: SignerBase) {
    this.httpClient = httpClient;
    this.signer = signer;
  }

  protected getSigner(): SignerBase {
    if (!this.signer) {
      throw new Error("A signer must be configured for this action");
    }
    return this.signer;
  }

  protected async signedJsonRequest<T>(
    method: "POST" | "PUT",
    path: string,
    body: unknown,
    signatureHeader: string
  ): Promise<T> {
    const canonical = canonicalJson(body);
    const signature = await this.getSigner().signData(canonical);
    const options = { headers: { [signatureHeader]: signature }, retry: true };
    const canonicalBody = JSON.parse(canonical) as unknown;
    return method === "PUT"
      ? this.httpClient.put<T>(path, canonicalBody, options)
      : this.httpClient.post<T>(path, canonicalBody, options);
  }
}
