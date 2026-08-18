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

void registry.v2.search({ pageSize: 50, sort: "updated_at_desc", categoryTags: ["stablecoin"] });
void admin.migrateAsset("ab".repeat(32));
void ({} as AssetResponse).asset_id;
void ({} as AuditLogResponse).items;
void ({} as IssuerActionResponse).status;
