import { AdminClient, AssetRegistryClient } from "../../src/client/index.ts";
import type { SignerBase } from "../../src/signer/signer.ts";
import { canonicalJson } from "../../src/utils/canonicalJson.ts";
import { hashIconBytes, iconBytesToBase64 } from "../../src/utils/icons.ts";

class RecordingSigner implements SignerBase {
  signedData: string[] = [];

  async signData(data: string): Promise<string> {
    this.signedData.push(data);
    return "icon-signature";
  }

  getPubkey(): string {
    return `02${"ef".repeat(32)}`;
  }
}

describe("icon clients", () => {
  const assetId = "a".repeat(64);

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("hashes bytes, signs only the nested issuer action, and sends the Base64 envelope", async () => {
    const signer = new RecordingSigner();
    const bytes = Uint8Array.from([1, 2, 3, 4]);
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            asset_id: assetId,
            action_hash: "b".repeat(64),
            audit_id: 1,
            operation: "register",
            server_received_at: "2026-07-17T12:00:00Z",
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "applied",
            proposal: {
              proposal_id: "proposal-id",
              asset_id: assetId,
              icon_hash: hashIconBytes(bytes),
              status: "pending",
              proposed_at: "2026-07-17T12:00:01Z",
            },
            audit_entry: { audit_id: 2, server_received_at: "2026-07-17T12:00:01Z", actor: "issuer", action: {} },
          }),
          { status: 200 }
        )
      );
    const client = new AssetRegistryClient({ baseUrl: "https://api.example.com", signer });

    await client.v2.proposeIcon(assetId, bytes, {
      nonce: "fixed-icon-nonce",
      timestamp: "2026-07-17T12:00:01Z",
    });

    const body = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
    expect(body.icon).toBe(iconBytesToBase64(bytes));
    expect(body.action.icon_hash).toBe(hashIconBytes(bytes));
    expect(body.action.prev_action_hash).toBe("b".repeat(64));
    expect(signer.signedData).toEqual([canonicalJson(body.action)]);
    expect(signer.signedData[0]).not.toContain(body.icon);
  });

  it("gets the legacy icon map", async () => {
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ [assetId]: "aWNvbg==" }), { status: 200 }));
    const client = new AssetRegistryClient({ baseUrl: "https://api.example.com" });

    await expect(client.legacy.getIcons()).resolves.toEqual({ [assetId]: "aWNvbg==" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/icons.json",
      expect.objectContaining({ method: "GET" })
    );
  });

  it("signs issuer proposal searches and scopes them to the signer key", async () => {
    const signer = new RecordingSigner();
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [], page: 1, page_size: 20, total_count: 0, total_pages: 0 }),
          { status: 200 }
        )
      );
    const client = new AssetRegistryClient({ baseUrl: "https://api.example.com", signer });

    await client.v2.listIconProposals(assetId, {
      status: "approved",
      timestamp: "2026-07-23T12:00:00Z",
    });

    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toMatchObject({
      actor_pubkey: signer.getPubkey(),
      asset_id: assetId,
      operation: "list_icon_proposals",
      signing_context: "liquid-asset-registry-issuer-query-v1",
      status: "approved",
    });
    expect(signer.signedData).toEqual([canonicalJson(body)]);
  });

  it("signs only the admin icon action and sends the Base64 upload separately", async () => {
    const signer = new RecordingSigner();
    const bytes = Uint8Array.from([9, 8, 7, 6]);
    const iconHash = hashIconBytes(bytes);
    const iconHref = `/v2/assets/${assetId}/icon/${iconHash}.png`;
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ asset_id: assetId, icon: { href: iconHref } }), {
          status: 200,
        })
      );
    const admin = new AdminClient({ baseUrl: "https://api.example.com", signer });

    const response = await admin.setIcon(assetId, bytes, {
      nonce: "fixed-admin-icon-nonce",
      timestamp: "2026-07-27T12:00:00Z",
    });

    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(response.icon).toEqual({ href: iconHref });
    expect(body.icon).toBe(iconBytesToBase64(bytes));
    expect(body.action).toMatchObject({
      actor_pubkey: signer.getPubkey(),
      asset_id: assetId,
      icon_hash: iconHash,
      nonce: "fixed-admin-icon-nonce",
      operation: "set_icon",
    });
    expect(signer.signedData).toEqual([canonicalJson(body.action)]);
    expect(signer.signedData[0]).not.toContain(body.icon);
  });

  it("signs pending searches and icon decisions with the admin signer", async () => {
    const signer = new RecordingSigner();
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [], page: 2, page_size: 10, total_count: 0, total_pages: 0 }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ asset_id: assetId }), { status: 200 }));
    const admin = new AdminClient({ baseUrl: "https://api.example.com", signer });

    await admin.listPendingIconProposals({
      page: 2,
      pageSize: 10,
      order: "desc",
      timestamp: "2026-07-17T12:00:00Z",
    });
    await admin.approveIcon(assetId, "A".repeat(64));

    const searchBody = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    const approvalBody = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
    expect(searchBody).toMatchObject({
      operation: "list_pending_icon_proposals",
      page: 2,
      page_size: 10,
      order: "desc",
    });
    expect(approvalBody).toMatchObject({ operation: "approve_icon", asset_id: assetId, icon_hash: "a".repeat(64) });
    expect(signer.signedData).toEqual([canonicalJson(searchBody), canonicalJson(approvalBody)]);
  });
});
