import type { IconMap, LegacyAsset, LegacyAssetMap, LegacyAssetResponse } from "../types/index.js";
import type { HttpClient } from "../utils/http.js";
import { validateAssetId, validateNonEmptyString } from "../utils/validation.js";

export class LegacyClient {
  constructor(private readonly httpClient: HttpClient) {}

  async register(asset: LegacyAsset): Promise<LegacyAssetResponse> {
    return this.httpClient.post<LegacyAssetResponse>("/", asset);
  }

  async list(): Promise<LegacyAssetMap> {
    return this.httpClient.get<LegacyAssetMap>("/");
  }

  async getIcons(): Promise<IconMap> {
    return this.httpClient.get<IconMap>("/icons.json");
  }

  async get(assetId: string): Promise<LegacyAssetResponse | null> {
    validateAssetId(assetId);
    try {
      return await this.httpClient.get<LegacyAssetResponse>(`/${assetId}`);
    } catch (error) {
      if (isNotFound(error)) return null;
      throw error;
    }
  }

  async deregister(assetId: string, signature: string): Promise<string> {
    validateAssetId(assetId);
    validateNonEmptyString(signature, "signature");
    return this.httpClient.delete<string>(`/${assetId}`, { body: { signature }, retry: false });
  }
}

function isNotFound(error: unknown): boolean {
  return error instanceof Error && error.name === "HttpError" && (error as { statusCode?: number }).statusCode === 404;
}
