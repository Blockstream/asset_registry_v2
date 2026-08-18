import { AssetRegistryClient } from "../src/index.js";

const client = new AssetRegistryClient({ baseUrl: "http://localhost:8001" });

await client.legacy.register({
  asset_id: "07c22bef610db8776bf377f885acc13711eede0f918f33e480b94be3ff40513f",
  contract: {
    entity: { domain: "unspent.info" },
    issuer_pubkey: "027c8ac4997d39582bca97bed1015385c15e237054e0c9606125be8c9b9cc1a506",
    name: "unspent.info",
    precision: 0,
    ticker: "UNSPENT",
    version: 0,
  },
  domain_verification_method: "dns",
});
