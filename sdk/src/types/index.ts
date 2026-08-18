import type { components, operations } from "../generated/openapi.js";

export type TradingVenue = components["schemas"]["TradingVenue"];
export type CategoryTag = NonNullable<
  NonNullable<operations["searchAssetsV2"]["parameters"]["query"]>["category_tag"]
>[number];
export type AssetType = NonNullable<NonNullable<operations["searchAssetsV2"]["parameters"]["query"]>["asset_type"]>;
export type MutableMetadata = components["schemas"]["MutableMetadata-Input"];
export type LegacyExtraValue = components["schemas"]["LegacyExtraValue-Input"];
export type V2AssetContractEntity = components["schemas"]["ContractEntity"];
export type V2AssetContract = components["schemas"]["ContractMetadata"];
export type V2Asset = components["schemas"]["RegisterAssetRequest"];
export type AssetResponse = components["schemas"]["AssetResponse"];
export type AssetListResponse = components["schemas"]["AssetListResponse"];
export type LegacyAsset = components["schemas"]["LegacyAssetRequest"];

export interface LegacyAssetResponse extends Record<string, unknown> {
  asset_id: string;
  contract: Record<string, unknown>;
  version: number;
  name: string;
  precision: number;
  entity: { domain: string };
  ticker?: string;
  issuer_pubkey?: string;
}

export type LegacyAssetMap = Record<string, LegacyAssetResponse>;
export type IconMap = Record<string, string>;

type SearchQuery = NonNullable<operations["searchAssetsV2"]["parameters"]["query"]>;

/** Ergonomic options mapped to the API's snake-case search parameters. */
export interface SearchParams {
  page?: SearchQuery["page"];
  pageSize?: SearchQuery["page_size"];
  sort?: SearchQuery["sort"];
  assetId?: SearchQuery["asset_id"];
  domain?: SearchQuery["domain"];
  ticker?: SearchQuery["ticker"];
  name?: SearchQuery["name"];
  assetType?: SearchQuery["asset_type"];
  categoryTags?: SearchQuery["category_tag"];
  tradingVenue?: SearchQuery["trading_venue"];
  createdAfter?: SearchQuery["created_after"];
  updatedAfter?: SearchQuery["updated_after"];
}

export type SearchResponse = AssetListResponse;

export interface AssetAuditParams {
  limit?: number;
  sinceAuditId?: number;
  order?: "asc" | "desc";
}

type AuditSearchQuery = NonNullable<operations["searchAuditLogV2"]["parameters"]["query"]>;

export interface AuditSearchParams extends AssetAuditParams {
  assetId?: AuditSearchQuery["asset_id"];
  operation?: AuditSearchQuery["operation"];
  actor?: AuditSearchQuery["actor"];
  fromServerReceivedAt?: AuditSearchQuery["from_server_received_at"];
  toServerReceivedAt?: AuditSearchQuery["to_server_received_at"];
}
