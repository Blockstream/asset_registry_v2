import {
  AdminClient,
  AssetRegistryClient,
  Signer,
  type AssetResponse,
  type AuditLogResponse,
  type IssuerActionResponse,
} from "liquid-asset-registry-sdk";

const signer = new Signer("ab".repeat(32));
const registry = new AssetRegistryClient({ baseUrl: "https://registry.example.com", signer });
const admin = new AdminClient({ baseUrl: "https://registry.example.com", signer });

void registry.v2.search({ pageSize: 50, sort: "updated_at_desc", assetType: "stablecoin" });
// @ts-expect-error Category tag filters were retired.
void registry.v2.search({ categoryTags: ["stablecoin"] });
// @ts-expect-error Issuer category tag actions were retired.
void registry.v2.replaceCategoryTags;
// @ts-expect-error Issuer trading venue actions were retired.
void registry.v2.replaceTradingVenues;
// @ts-expect-error Responses no longer include category tags.
void ({} as AssetResponse).mutable.category_tags;
void admin.migrateAsset("ab".repeat(32));
void ({} as AssetResponse).asset_id;
void ({} as AuditLogResponse).items;
void ({} as IssuerActionResponse).status;
