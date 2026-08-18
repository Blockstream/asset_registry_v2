import { AssetRegistryClient, Signer } from "../src/index.js";
import type { AssetResponse, SearchResponse } from "../src/types/index.js";
import type { IssuerActionResponse } from "../src/types/responses.js";

const BASE_URL = "http://localhost:8001";
const ASSET_ID = "1200000000000000000000000000000000000000000000000000000000000022";

const { privateKey } = await AssetRegistryClient.generateKeyPair();
const signer = new Signer(privateKey);
const client = new AssetRegistryClient({ baseUrl: BASE_URL, signer });

function printResponse(label: string, response: unknown): void {
  console.log(`\n${label}`);
  console.log(JSON.stringify(response, null, 2));
}

function summarizeSearch(response: SearchResponse): unknown {
  return {
    page: response.page,
    page_size: response.page_size,
    total_count: response.total_count,
    items: response.items.map((asset) => ({
      asset_id: asset.asset_id,
      name: asset.contract.name,
      ticker: asset.contract.ticker,
      status: asset.status,
      icon_url: asset.icon ? new URL(asset.icon.href, BASE_URL).toString() : null,
    })),
  };
}

function summarizeAsset(asset: AssetResponse): unknown {
  return {
    asset_id: asset.asset_id,
    contract: {
      name: asset.contract.name,
      ticker: asset.contract.ticker,
      entity: asset.contract.entity,
    },
    status: asset.status,
    mutable: asset.mutable,
    icon_url: asset.icon ? new URL(asset.icon.href, BASE_URL).toString() : null,
  };
}

function summarizeAction(
  response: IssuerActionResponse,
  changedAssetFields: (asset: AssetResponse) => unknown
): unknown {
  return {
    status: response.status,
    audit: {
      audit_id: response.audit_entry.audit_id,
      action_hash: response.audit_entry.action_hash,
      operation: response.audit_entry.action.operation,
    },
    changed_asset_fields: response.asset ? changedAssetFields(response.asset) : null,
  };
}

const initialSearch = await client.v2.search({ name: "unspent", pageSize: 25 });
printResponse("Search results before registration:", summarizeSearch(initialSearch));

// Make the example repeatable when the same asset is already active locally.
try {
  await client.v2.deregisterAsset(ASSET_ID);
} catch {
  // The asset may not exist yet or may already be deregistered.
}

const registeredAsset = await client.v2.register({
  asset_id: ASSET_ID,
  contract: {
    entity: { domain: "unspent5.info" },
    initial_issuer_pubkey: signer.getPubkey(),
    name: "unspent5.info",
    precision: 0,
    ticker: "UNSPENT",
    version: 1,
  },
  domain_verification_method: "dns",
});
printResponse("Registered asset:", summarizeAsset(registeredAsset));

const asset = await client.v2.get(ASSET_ID);
printResponse("Asset returned by lookup:", asset ? summarizeAsset(asset) : null);

// Issuer action helpers fetch this automatically and include it as prev_action_hash before signing.
const latestAction = await client.v2.getLatestActionHash(ASSET_ID);
printResponse("Latest issuer action hash used to chain the next action:", latestAction);

const searchAfterRegistration = await client.v2.search({ name: "unspent", pageSize: 25 });
printResponse("Search results after registration:", summarizeSearch(searchAfterRegistration));

const tradingVenueResponse = await client.v2.replaceTradingVenues(ASSET_ID, [
  {
    venue: "sideswap",
    url: "https://coinbase.com",
  },
]);
printResponse(
  "Trading venues after replacement:",
  summarizeAction(tradingVenueResponse, ({ mutable }) => ({ trading_venues: mutable.trading_venues }))
);

const categoryTagResponse = await client.v2.replaceCategoryTags(ASSET_ID, ["bond"]);
printResponse(
  "Category tags after replacement:",
  summarizeAction(categoryTagResponse, ({ mutable }) => ({ category_tags: mutable.category_tags }))
);

const deregistrationResponse = await client.v2.deregisterAsset(ASSET_ID);
printResponse(
  "Asset status after deregistration:",
  summarizeAction(deregistrationResponse, ({ status }) => ({ status }))
);
