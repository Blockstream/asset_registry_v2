import { v4 as uuidv4 } from "uuid";
import type { SignerBase } from "../signer/signer.js";
import type {
  DeleteCustomFieldAction,
  DeregisterAction,
  IssuerAction,
  IssuerActionInput,
  ReplaceCategoryTagsAction,
  ReplaceCustomAction,
  ReplaceTradingVenuesAction,
  RotateIssuerPubkeyAction,
  SetCustomFieldAction,
  ProposeIconAction,
  IssuerIconProposalSearchRequest,
} from "../types/actions.js";
import type {
  AssetAuditParams,
  AssetResponse,
  AuditSearchParams,
  CategoryTag,
  LegacyExtraValue,
  MutableMetadata,
  SearchParams,
  SearchResponse,
  TradingVenue,
  V2Asset,
  V2AssetContract,
} from "../types/index.js";
import type {
  AuditLogResponse,
  IssuerActionResponse,
  IssuerPubkeyHistoryEntry,
  LatestActionHashResponse,
  IconProposalResponse,
  IssuerIconProposalListResponse,
} from "../types/responses.js";
import { canonicalJson } from "../utils/canonicalJson.js";
import { hashIconBytes, iconBytesToBase64 } from "../utils/icons.js";
import type { HttpClient } from "../utils/http.js";
import { validateAssetId, validatePubkey } from "../utils/validation.js";
import { BaseClient } from "./BaseClient.js";

const SIGNING_CONTEXT = "liquid-asset-registry-action-v1" as const;

export interface V2RegisterOptions {
  domainSignature?: string;
}

export interface IconProposalOptions {
  prevActionHash?: string;
  nonce?: string;
  timestamp?: string;
}

export interface IconProposalSearchOptions {
  actorPubkey?: string;
  page?: number;
  pageSize?: number;
  order?: "asc" | "desc";
  status?: "pending" | "rejected" | "approved";
  timestamp?: string;
}

export class V2Client extends BaseClient {
  constructor(httpClient: HttpClient, signer?: SignerBase) {
    super(httpClient, signer);
  }

  async register(asset: V2Asset, options?: V2RegisterOptions): Promise<AssetResponse> {
    validateAssetId(asset.asset_id);
    const headers = options?.domainSignature ? { "Asset-Registry-Signature": options.domainSignature } : undefined;
    return this.httpClient.post<AssetResponse>("/v2/assets", asset, { headers, retry: false });
  }

  static normalizedRegistrationContractJson(contract: V2AssetContract): string {
    return canonicalJson(normalizeRegistrationContract(contract));
  }

  async signRegistrationContract(
    assetOrContract: V2Asset | V2AssetContract
  ): Promise<{ canonicalJson: string; signature: string }> {
    const contract = "contract" in assetOrContract ? assetOrContract.contract : assetOrContract;
    const canonical = V2Client.normalizedRegistrationContractJson(contract);
    return { canonicalJson: canonical, signature: await this.getSigner().signData(canonical) };
  }

  async search(params: SearchParams = {}): Promise<SearchResponse> {
    const query = new URLSearchParams();
    appendQuery(query, "page", params.page);
    appendQuery(query, "page_size", params.pageSize);
    appendQuery(query, "sort", params.sort);
    appendQuery(query, "asset_id", params.assetId);
    appendQuery(query, "domain", params.domain);
    appendQuery(query, "ticker", params.ticker);
    appendQuery(query, "name", params.name);
    appendQuery(query, "asset_type", params.assetType);
    for (const categoryTag of params.categoryTags ?? []) query.append("category_tag", categoryTag);
    appendQuery(query, "trading_venue", params.tradingVenue);
    appendQuery(query, "created_after", params.createdAfter);
    appendQuery(query, "updated_after", params.updatedAfter);
    return this.httpClient.get<SearchResponse>(withQuery("/v2/assets", query));
  }

  async getAll(): Promise<Record<string, AssetResponse>> {
    return this.httpClient.get<Record<string, AssetResponse>>("/v2/assets/all.json");
  }

  async get(assetId: string): Promise<AssetResponse | null> {
    validateAssetId(assetId);
    try {
      return await this.httpClient.get<AssetResponse>(`/v2/assets/${assetId}`);
    } catch (error) {
      if (isNotFound(error)) return null;
      throw error;
    }
  }

  async submitIssuerAction(action: IssuerActionInput): Promise<IssuerActionResponse> {
    validateAssetId(action.asset_id);
    const prevActionHash = action.prev_action_hash ?? (await this.getLatestActionHash(action.asset_id)).action_hash;
    const actionWithMeta = {
      ...action,
      prev_action_hash: prevActionHash,
      nonce: action.nonce ?? uuidv4(),
      timestamp: action.timestamp ?? new Date().toISOString(),
    } as IssuerAction;
    const canonical = canonicalJson(actionWithMeta);
    const signature = await this.getSigner().signData(canonical);
    return this.httpClient.post<IssuerActionResponse>(
      `/v2/assets/${action.asset_id}/actions`,
      JSON.parse(canonical) as unknown,
      { headers: { "Asset-Registry-Signature": signature }, retry: true }
    );
  }

  async getLatestActionHash(assetId: string): Promise<LatestActionHashResponse> {
    validateAssetId(assetId);
    return this.httpClient.get<LatestActionHashResponse>(`/v2/assets/${assetId}/actions/latest`);
  }

  async proposeIcon(
    assetId: string,
    pngBytes: Uint8Array,
    options: IconProposalOptions = {}
  ): Promise<IconProposalResponse> {
    validateAssetId(assetId);
    const prevActionHash = options.prevActionHash ?? (await this.getLatestActionHash(assetId)).action_hash;
    const action = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "propose_icon",
      icon_hash: hashIconBytes(pngBytes),
      prev_action_hash: prevActionHash,
      nonce: options.nonce ?? uuidv4(),
      timestamp: options.timestamp ?? new Date().toISOString(),
    } satisfies ProposeIconAction;
    const canonicalAction = canonicalJson(action);
    const signature = await this.getSigner().signData(canonicalAction);
    return this.httpClient.post<IconProposalResponse>(
      `/v2/assets/${assetId}/icon-proposals`,
      { action: JSON.parse(canonicalAction) as ProposeIconAction, icon: iconBytesToBase64(pngBytes) },
      { headers: { "Asset-Registry-Signature": signature }, retry: true }
    );
  }

  async listIconProposals(
    assetId: string,
    options: IconProposalSearchOptions = {}
  ): Promise<IssuerIconProposalListResponse> {
    validateAssetId(assetId);
    const signer = this.getSigner();
    const actorPubkey = options.actorPubkey ?? signer.getPubkey?.();
    if (!actorPubkey) {
      throw new Error("actorPubkey is required when the signer cannot provide its public key");
    }
    validatePubkey(actorPubkey);
    const query = {
      signing_context: "liquid-asset-registry-issuer-query-v1",
      actor_pubkey: actorPubkey.toLowerCase(),
      asset_id: assetId,
      operation: "list_icon_proposals",
      timestamp: options.timestamp ?? new Date().toISOString(),
      page: options.page ?? 1,
      page_size: options.pageSize ?? 20,
      order: options.order ?? "desc",
      status: options.status,
    } satisfies IssuerIconProposalSearchRequest;
    return this.signedJsonRequest<IssuerIconProposalListResponse>(
      "POST",
      `/v2/assets/${assetId}/icon-proposals/search`,
      query,
      "Asset-Registry-Signature"
    );
  }

  async replaceCategoryTags(assetId: string, categoryTags: CategoryTag[]): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "replace_category_tags",
      mutable_schema_version: 1,
      category_tags: categoryTags,
    } satisfies Omit<ReplaceCategoryTagsAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async replaceTradingVenues(assetId: string, tradingVenues: TradingVenue[]): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "replace_trading_venues",
      mutable_schema_version: 1,
      trading_venues: tradingVenues,
    } satisfies Omit<ReplaceTradingVenuesAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async replaceCustom(assetId: string, custom: NonNullable<MutableMetadata["custom"]>): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "replace_custom",
      mutable_schema_version: 1,
      custom,
    } satisfies Omit<ReplaceCustomAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async setCustomField(assetId: string, key: string, value: LegacyExtraValue): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "set_custom_field",
      mutable_schema_version: 1,
      custom_key: key,
      value,
    } satisfies Omit<SetCustomFieldAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async deleteCustomField(assetId: string, key: string): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "delete_custom_field",
      mutable_schema_version: 1,
      custom_key: key,
    } satisfies Omit<DeleteCustomFieldAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async deregisterAsset(assetId: string): Promise<IssuerActionResponse> {
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "deregister",
    } satisfies Omit<DeregisterAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async rotateIssuerKey(assetId: string, newPubkey: string): Promise<IssuerActionResponse> {
    validatePubkey(newPubkey);
    const action: IssuerActionInput = {
      signing_context: SIGNING_CONTEXT,
      asset_id: assetId,
      operation: "rotate_issuer_pubkey",
      new_issuer_pubkey: newPubkey.toLowerCase(),
    } satisfies Omit<RotateIssuerPubkeyAction, "prev_action_hash" | "timestamp" | "nonce">;
    return this.submitIssuerAction(action);
  }

  async getAssetAudit(assetId: string, params: AssetAuditParams = {}): Promise<AuditLogResponse> {
    validateAssetId(assetId);
    const query = new URLSearchParams();
    appendQuery(query, "limit", params.limit);
    appendQuery(query, "since_audit_id", params.sinceAuditId);
    appendQuery(query, "order", params.order);
    return this.httpClient.get<AuditLogResponse>(withQuery(`/v2/assets/${assetId}/audit`, query));
  }

  async searchAudit(params: AuditSearchParams = {}): Promise<AuditLogResponse> {
    const query = new URLSearchParams();
    appendQuery(query, "limit", params.limit);
    appendQuery(query, "since_audit_id", params.sinceAuditId);
    appendQuery(query, "asset_id", params.assetId);
    appendQuery(query, "operation", params.operation);
    appendQuery(query, "actor", params.actor);
    appendQuery(query, "from_server_received_at", params.fromServerReceivedAt);
    appendQuery(query, "to_server_received_at", params.toServerReceivedAt);
    appendQuery(query, "order", params.order);
    return this.httpClient.get<AuditLogResponse>(withQuery("/v2/audit", query));
  }

  async getIssuerPubkeyHistory(assetId: string): Promise<IssuerPubkeyHistoryEntry[]> {
    validateAssetId(assetId);
    const asset = await this.httpClient.get<AssetResponse>(`/v2/assets/${assetId}`);
    return asset.issuer_pubkey_history ?? [];
  }
}

function normalizeRegistrationContract(contract: V2AssetContract): Record<string, unknown> {
  return stripNullish({
    ...contract,
    initial_issuer_pubkey: contract.initial_issuer_pubkey?.toLowerCase(),
    issuer_pubkey: contract.issuer_pubkey?.toLowerCase(),
  }) as Record<string, unknown>;
}

function stripNullish(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripNullish);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, entry]) => entry !== null && entry !== undefined)
        .map(([key, entry]) => [key, stripNullish(entry)])
    );
  }
  return value;
}

function appendQuery(query: URLSearchParams, name: string, value: string | number | undefined): void {
  if (value !== undefined) query.append(name, String(value));
}

function withQuery(path: string, query: URLSearchParams): string {
  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}

function isNotFound(error: unknown): boolean {
  return error instanceof Error && error.name === "HttpError" && (error as { statusCode?: number }).statusCode === 404;
}
