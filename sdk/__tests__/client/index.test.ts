import { AdminClient, AssetRegistryClient, type ClientConfig } from "../../src/client/index.ts";
import { SignerBase } from "../../src/signer/signer.ts";

class FakeSigner implements SignerBase {
  signedData: string[] = [];

  async signData(data: string): Promise<string> {
    this.signedData.push(data);
    return "fake-signature";
  }

  getPubkey(): string {
    return `02${"ef".repeat(32)}`;
  }
}

describe("AssetRegistryClient", () => {
  const assetId = "a".repeat(64);

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe("constructor", () => {
    it("requires baseUrl", () => {
      expect(() => {
        // @ts-expect-error - baseUrl is required
        new AssetRegistryClient({});
      }).toThrow(/baseUrl is required/);
    });

    it("creates client with just baseUrl", () => {
      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
      });
      expect(client).toBeDefined();
    });

    it("creates client with all config options", () => {
      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
        issuerPrivateKey: "abcdef1234567890".repeat(4),
        timeout: 60000,
        maxRetries: 5,
        retryDelay: 2000,
      });
      expect(client).toBeDefined();
    });

    it("has legacy and v2 clients", () => {
      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
      });
      expect(client.legacy).toBeDefined();
      expect(client.v2).toBeDefined();
    });

    it("does not expose admin methods on the v2 registry client", () => {
      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
      });
      expect("forceDelistAsset" in client.v2).toBe(false);
      expect("addAdmin" in client.v2).toBe(false);
    });

    it("stores config", () => {
      const config: ClientConfig = {
        baseUrl: "https://api.example.com",
        timeout: 45000,
      };
      const client = new AssetRegistryClient(config);
      expect(client.config.baseUrl).toBe("https://api.example.com");
      expect(client.config.timeout).toBe(45000);
    });

    it("rejects ambiguous signer configuration", () => {
      expect(
        () =>
          new AssetRegistryClient({
            baseUrl: "https://api.example.com",
            signer: new FakeSigner(),
            issuerPrivateKey: "ab".repeat(32),
          })
      ).toThrow(/either signer or issuerPrivateKey/);
    });
  });

  describe("v2 issuer action hash chain", () => {
    it("fetches the latest action hash before signing issuer actions", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            asset_id: assetId,
            action_hash: "b".repeat(64),
            audit_id: 1,
            operation: "register",
            server_received_at: "2026-05-13T12:00:00Z",
          }),
          { status: 200 }
        )
      ).mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "applied",
            audit_entry: {
              audit_id: 2,
              server_received_at: "2026-05-13T12:01:00Z",
              actor: "issuer",
              action: {},
            },
          }),
          { status: 200 }
        )
      );

      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
        signer: new FakeSigner(),
      });

      await client.v2.replaceCategoryTags(assetId, ["stablecoin"]);

      expect(fetchMock).toHaveBeenNthCalledWith(
        1,
        `https://api.example.com/v2/assets/${assetId}/actions/latest`,
        expect.objectContaining({ method: "GET" })
      );

      const [, postOptions] = fetchMock.mock.calls[1];
      const body = JSON.parse((postOptions as RequestInit).body as string);
      expect(body.prev_action_hash).toBe("b".repeat(64));
      expect(body.operation).toBe("replace_category_tags");
    });

    it("uses custom_key in custom-field action payloads", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            asset_id: assetId,
            action_hash: "c".repeat(64),
            audit_id: 1,
            operation: "register",
            server_received_at: "2026-05-13T12:00:00Z",
          }),
          { status: 200 }
        )
      ).mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "applied", audit_entry: { audit_id: 2, action: {} } }), { status: 200 })
      );

      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
        signer: new FakeSigner(),
      });

      await client.v2.setCustomField(assetId, "isin", "US0000000000");

      const [, postOptions] = fetchMock.mock.calls[1];
      const body = JSON.parse((postOptions as RequestInit).body as string);
      expect(body.custom_key).toBe("isin");
      expect(body.customKey).toBeUndefined();
      expect(body.prev_action_hash).toBe("c".repeat(64));
    });
  });

  describe("v2 registration domain proof signatures", () => {
    const pubkey = `02${"ab".repeat(32)}`;
    const asset = {
      asset_id: assetId,
      contract: {
        entity: { domain: "proof.example.com" },
        initial_issuer_pubkey: pubkey.toUpperCase(),
        issuer_pubkey: null,
        name: "Proof Asset",
        precision: 8,
        ticker: "PROOF",
        version: 2,
      },
      domain_verification_method: "dns" as const,
      mutable: {
        custom: { ignored: true },
      },
    };

    it("canonicalizes the normalized contract for pubkey-bound domain proof signing", async () => {
      const canonical = AssetRegistryClient.normalizedRegistrationContractJson(asset.contract);

      expect(canonical).toBe(
        `{"entity":{"domain":"proof.example.com"},"initial_issuer_pubkey":"${pubkey}","name":"Proof Asset","precision":8,"ticker":"PROOF","version":2}`
      );
      expect(canonical).not.toContain("asset_id");
      expect(canonical).not.toContain("domain_verification_method");
      expect(canonical).not.toContain("mutable");
      expect(canonical).not.toContain("\"issuer_pubkey\":");
    });

    it("signs the normalized contract instead of the registration request body", async () => {
      const signer = new FakeSigner();
      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
        signer,
      });

      const signed = await client.v2.signRegistrationContract(asset);

      expect(signed.signature).toBe("fake-signature");
      expect(signer.signedData).toEqual([signed.canonicalJson]);
      expect(signed.canonicalJson).toBe(AssetRegistryClient.normalizedRegistrationContractJson(asset.contract));
      expect(signed.canonicalJson).not.toContain("asset_id");
      expect(signed.canonicalJson).not.toContain("mutable");
    });

    it("passes Asset-Registry-Signature when registering with pubkey-bound domain proof", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ ...asset, uuid: "asset-uuid", createdAt: "now", updatedAt: "now" }), {
          status: 201,
        })
      );

      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
      });

      await client.v2.register(asset, { domainSignature: "proof-signature" });

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/assets",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Asset-Registry-Signature": "proof-signature",
          }),
        })
      );
    });
  });

  describe("v2 admin actions", () => {
    const adminPubkey = `02${"cd".repeat(32)}`;

    function mockAssetResponse() {
      return new Response(
        JSON.stringify({
          asset_id: assetId,
          contract: {
            entity: { domain: "proof.example.com" },
            initial_issuer_pubkey: `02${"ab".repeat(32)}`,
            name: "Proof Asset",
            precision: 8,
            ticker: "PROOF",
            version: 2,
          },
          initial_issuer_pubkey: `02${"ab".repeat(32)}`,
          initial_issuer_pubkey_source: "contract",
          current_issuer_pubkey: `02${"ab".repeat(32)}`,
          issuer_pubkey_history: [],
          mutable: { category_tags: [], trading_venues: [], custom: {} },
          icon: null,
          status: "active",
          created_at: "2026-05-15T12:00:00Z",
          updated_at: "2026-05-15T12:00:00Z",
        }),
        { status: 200 }
      );
    }

    it("submits admin lifecycle actions to the lifecycle endpoint with current schema fields", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "applied", audit_entry: { audit_id: 1, action: {} } }), { status: 200 })
      );
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer: new FakeSigner() });

      await client.addAdmin(adminPubkey, ["annotate_assets"], "Ops");

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/admin/actions",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Asset-Registry-Admin-Signature": "fake-signature",
          }),
        })
      );

      const [, options] = fetchMock.mock.calls[0];
      const body = JSON.parse((options as RequestInit).body as string);
      expect(body.signing_context).toBe("liquid-asset-registry-admin-action-v1");
      expect(body.actor_pubkey).toBe(`02${"ef".repeat(32)}`);
      expect(body.operation).toBe("add_admin");
      expect(body.admin_pubkey).toBe(adminPubkey);
      expect(body.friendly_name).toBe("Ops");
      expect(body.timestamp).toBeDefined();
      expect(body.adminTimestamp).toBeUndefined();
      expect(body.adminPubkey).toBeUndefined();
    });

    it("signs and sends update_admin_annotations to the dedicated PUT endpoint", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(mockAssetResponse());
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer: new FakeSigner() });

      await client.updateAnnotations(assetId, { featured: true, admin_notes: "reviewed" });

      expect(fetchMock).toHaveBeenCalledWith(
        `https://api.example.com/v2/admin/assets/${assetId}/annotations`,
        expect.objectContaining({
          method: "PUT",
          headers: expect.objectContaining({
            "Asset-Registry-Admin-Signature": "fake-signature",
          }),
        })
      );

      const [, options] = fetchMock.mock.calls[0];
      const body = JSON.parse((options as RequestInit).body as string);
      expect(body.actor_pubkey).toBe(`02${"ef".repeat(32)}`);
      expect(body.operation).toBe("update_admin_annotations");
      expect(body.asset_id).toBe(assetId);
      expect(body.changes).toEqual({ admin_notes: "reviewed", featured: true });
    });

    it("supports force delist and force relist admin asset actions", async () => {
      const fetchMock = jest
        .spyOn(global, "fetch")
        .mockResolvedValueOnce(mockAssetResponse())
        .mockResolvedValueOnce(mockAssetResponse());
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer: new FakeSigner() });

      await client.forceDelistAsset(assetId, "policy review");
      await client.forceRelistAsset(assetId, "resolved");

      expect(fetchMock).toHaveBeenNthCalledWith(
        1,
        `https://api.example.com/v2/admin/assets/${assetId}/actions`,
        expect.objectContaining({ method: "POST" })
      );
      expect(fetchMock).toHaveBeenNthCalledWith(
        2,
        `https://api.example.com/v2/admin/assets/${assetId}/actions`,
        expect.objectContaining({ method: "POST" })
      );

      const firstBody = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
      const secondBody = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
      expect(firstBody.operation).toBe("force_delist_asset");
      expect(firstBody.reason).toBe("policy review");
      expect(secondBody.operation).toBe("force_relist_asset");
      expect(secondBody.reason).toBe("resolved");
    });

    it("supports explicit actor pubkey for custom admin signers", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "applied", audit_entry: { audit_id: 1, action: {} } }), { status: 200 })
      );
      const signer: SignerBase = {
        async signData() {
          return "compact-signature";
        },
      };
      const actorPubkey = `03${"12".repeat(32)}`;
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer, actorPubkey });

      await client.addAdmin(adminPubkey, ["annotate_assets"], "Ops");

      const [, options] = fetchMock.mock.calls[0];
      const body = JSON.parse((options as RequestInit).body as string);
      expect(body.actor_pubkey).toBe(actorPubkey);
      expect((options as RequestInit).headers).toEqual(
        expect.objectContaining({ "Asset-Registry-Admin-Signature": "compact-signature" })
      );
    });

    it("requires an actor pubkey for admin actions", async () => {
      const signer: SignerBase = {
        async signData() {
          return "compact-signature";
        },
      };
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer });

      await expect(client.addAdmin(adminPubkey, ["annotate_assets"], "Ops")).rejects.toThrow(/actor_pubkey/);
    });

    it("submits migration as a signed admin action", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "applied", audit_entry: { audit_id: 1, action: {} } }), {
          status: 200,
        })
      );
      const client = new AdminClient({ baseUrl: "https://api.example.com", signer: new FakeSigner() });

      await client.migrateAsset(assetId);

      const [url, options] = fetchMock.mock.calls[0];
      expect(url).toBe(`https://api.example.com/v2/assets/${assetId}/migrate`);
      expect((options as RequestInit).headers).toEqual(
        expect.objectContaining({ "Asset-Registry-Admin-Signature": "fake-signature" })
      );
      expect(JSON.parse((options as RequestInit).body as string)).toEqual(
        expect.objectContaining({ operation: "migrate_asset", asset_id: assetId })
      );
    });
  });

  describe("v2 search", () => {
    it("maps assetId search to the asset_id query parameter", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], page: 1, page_size: 50, total_count: 0, total_pages: 0 }), {
          status: 200,
        })
      );

      const client = new AssetRegistryClient({
        baseUrl: "https://api.example.com",
      });

      await client.v2.search({ assetId: "aa909f1b", name: "bitcoin" });

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/assets?asset_id=aa909f1b&name=bitcoin",
        expect.objectContaining({ method: "GET" })
      );
    });

    it("maps timestamp search filters to snake-case query parameters", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], page: 1, page_size: 50, total_count: 0, total_pages: 0 }), {
          status: 200,
        })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await client.v2.search({
        createdAfter: "2026-01-10T00:00:00Z",
        updatedAfter: "2026-01-20T00:00:00Z",
      });

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/assets?created_after=2026-01-10T00%3A00%3A00Z&updated_after=2026-01-20T00%3A00%3A00Z",
        expect.objectContaining({ method: "GET" })
      );
    });

    it("maps pagination, sort, enum filters, and repeated categories exactly", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], page: 2, page_size: 25 }), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await client.v2.search({
        page: 2,
        pageSize: 25,
        sort: "updated_at_desc",
        ticker: "USDt",
        assetType: "stablecoin",
        categoryTags: ["stablecoin", "tokenized"],
        tradingVenue: "bitfinex",
      });

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/assets?page=2&page_size=25&sort=updated_at_desc&ticker=USDt&asset_type=stablecoin&category_tag=stablecoin&category_tag=tokenized&trading_venue=bitfinex",
        expect.objectContaining({ method: "GET" })
      );
    });

    it("adds order to global audit searches", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], next_since_audit_id: null }), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await client.v2.searchAudit({ order: "desc" });

      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.example.com/v2/audit?order=desc",
        expect.objectContaining({ method: "GET" })
      );
    });

    it("maps cursor and global audit filters to snake case", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], next_since_audit_id: null }), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await client.v2.searchAudit({
        limit: 10,
        sinceAuditId: 42,
        assetId,
        operation: "deregister",
        actor: "issuer",
        fromServerReceivedAt: "2026-01-01T00:00:00Z",
        toServerReceivedAt: "2026-02-01T00:00:00Z",
      });

      expect(fetchMock).toHaveBeenCalledWith(
        `https://api.example.com/v2/audit?limit=10&since_audit_id=42&asset_id=${assetId}&operation=deregister&actor=issuer&from_server_received_at=2026-01-01T00%3A00%3A00Z&to_server_received_at=2026-02-01T00%3A00%3A00Z`,
        expect.objectContaining({ method: "GET" })
      );
    });
  });

  describe("contract-correct convenience methods", () => {
    it("returns the keyed all-assets object unchanged", async () => {
      const response = { [assetId]: { asset_id: assetId } };
      jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify(response), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await expect(client.v2.getAll()).resolves.toEqual(response);
    });

    it("reads issuer history from the asset lookup route", async () => {
      const history = [{ pubkey: `02${"ab".repeat(32)}`, valid_from_audit_id: 1 }];
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ asset_id: assetId, issuer_pubkey_history: history }), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await expect(client.v2.getIssuerPubkeyHistory(assetId)).resolves.toEqual(history);
      expect(fetchMock).toHaveBeenCalledWith(
        `https://api.example.com/v2/assets/${assetId}`,
        expect.objectContaining({ method: "GET" })
      );
    });

    it("sends the required signature body for legacy deletion", async () => {
      const fetchMock = jest.spyOn(global, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify("Asset deleted"), { status: 200 })
      );
      const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

      await expect(client.legacy.deregister(assetId, "legacy-signature")).resolves.toBe("Asset deleted");
      expect(fetchMock).toHaveBeenCalledWith(
        `https://api.example.com/${assetId}`,
        expect.objectContaining({ method: "DELETE", body: JSON.stringify({ signature: "legacy-signature" }) })
      );
    });
  });
});
