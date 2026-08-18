import { v4 as uuidv4 } from "uuid";
import type { SignerBase } from "../signer/signer.js";
import type {
  AdminAssetAction,
  AdminAssetActionInput,
  AdminAnnotationsUpdate,
  AdminLifecycleAction,
  AdminLifecycleActionInput,
  AdminPermission,
  MigrateAssetAction,
  PendingIconProposalSearchRequest,
  SetIconAction,
} from "../types/admin.js";
import type { AssetResponse } from "../types/index.js";
import type { AdminActionResponse, IssuerActionResponse, PendingIconProposalListResponse } from "../types/responses.js";
import { canonicalJson } from "../utils/canonicalJson.js";
import { DefaultHttpClient } from "../utils/http.js";
import { hashIconBytes, iconBytesToBase64 } from "../utils/icons.js";
import { signData } from "../utils/signatures.js";
import { validateAssetId, validatePubkey } from "../utils/validation.js";
import { BaseClient } from "./BaseClient.js";

const ADMIN_SIGNING_CONTEXT = "liquid-asset-registry-admin-action-v1" as const;
const ADMIN_SIGNATURE_HEADER = "Asset-Registry-Admin-Signature";
const ADMIN_QUERY_SIGNING_CONTEXT = "liquid-asset-registry-admin-query-v1" as const;

export interface PendingIconProposalSearchOptions {
  page?: number;
  pageSize?: number;
  order?: "asc" | "desc";
  timestamp?: string;
}

export interface AdminIconUploadOptions {
  nonce?: string;
  timestamp?: string;
}

export interface AdminClientConfig {
  baseUrl: string;
  signer: SignerBase;
  actorPubkey?: string;
  timeout?: number;
  maxRetries?: number;
  retryDelay?: number;
}

export class AdminClient extends BaseClient {
  private readonly actorPubkey?: string;

  constructor(config: AdminClientConfig) {
    if (!config.baseUrl) throw new Error("baseUrl is required");
    const httpClient = new DefaultHttpClient(config.baseUrl, {
      timeout: config.timeout,
      retry: { maxRetries: config.maxRetries, retryDelay: config.retryDelay },
    });
    super(httpClient, config.signer);
    if (config.actorPubkey) validatePubkey(config.actorPubkey);
    this.actorPubkey = config.actorPubkey?.toLowerCase();
  }

  async submitAdminAction(action: AdminLifecycleActionInput): Promise<AdminActionResponse> {
    return this.signedJsonRequest<AdminActionResponse>(
      "POST",
      "/v2/admin/actions",
      this.withAdminActionMeta(action),
      ADMIN_SIGNATURE_HEADER
    );
  }

  async submitAdminAssetAction(action: AdminAssetActionInput): Promise<AssetResponse> {
    validateAssetId(action.asset_id);
    return this.signedJsonRequest<AssetResponse>(
      "POST",
      `/v2/admin/assets/${action.asset_id}/actions`,
      this.withAdminActionMeta(action),
      ADMIN_SIGNATURE_HEADER
    );
  }

  async updateAnnotations(assetId: string, changes: AdminAnnotationsUpdate): Promise<AssetResponse> {
    validateAssetId(assetId);
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "update_admin_annotations",
      asset_id: assetId,
      changes,
    } satisfies AdminAssetActionInput;
    return this.signedJsonRequest<AssetResponse>(
      "PUT",
      `/v2/admin/assets/${assetId}/annotations`,
      this.withAdminActionMeta(action),
      ADMIN_SIGNATURE_HEADER
    );
  }

  async addAdmin(pubkey: string, permissions: AdminPermission[], friendlyName = "Admin"): Promise<AdminActionResponse> {
    validatePubkey(pubkey);
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "add_admin",
      admin_pubkey: pubkey.toLowerCase(),
      friendly_name: friendlyName,
      permissions,
    } satisfies AdminLifecycleActionInput;
    return this.submitAdminAction(action);
  }

  async updateAdminPermissions(adminPubkey: string, permissions: AdminPermission[]): Promise<AdminActionResponse> {
    validatePubkey(adminPubkey);
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "update_admin_permissions",
      admin_pubkey: adminPubkey.toLowerCase(),
      permissions,
    } satisfies AdminLifecycleActionInput;
    return this.submitAdminAction(action);
  }

  async updateAdminName(adminPubkey: string, friendlyName: string): Promise<AdminActionResponse> {
    validatePubkey(adminPubkey);
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "update_admin_name",
      admin_pubkey: adminPubkey.toLowerCase(),
      friendly_name: friendlyName,
    } satisfies AdminLifecycleActionInput;
    return this.submitAdminAction(action);
  }

  async removeAdmin(adminPubkey: string): Promise<AdminActionResponse> {
    validatePubkey(adminPubkey);
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "remove_admin",
      admin_pubkey: adminPubkey.toLowerCase(),
    } satisfies AdminLifecycleActionInput;
    return this.submitAdminAction(action);
  }

  async forceDelistAsset(assetId: string, reason?: string): Promise<AssetResponse> {
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "force_delist_asset",
      asset_id: assetId,
      reason,
    } satisfies AdminAssetActionInput;
    return this.submitAdminAssetAction(action);
  }

  async forceRelistAsset(assetId: string, reason?: string): Promise<AssetResponse> {
    const action = {
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "force_relist_asset",
      asset_id: assetId,
      reason,
    } satisfies AdminAssetActionInput;
    return this.submitAdminAssetAction(action);
  }

  async listPendingIconProposals(
    options: PendingIconProposalSearchOptions = {}
  ): Promise<PendingIconProposalListResponse> {
    const query = {
      signing_context: ADMIN_QUERY_SIGNING_CONTEXT,
      actor_pubkey: this.getActorPubkey(),
      operation: "list_pending_icon_proposals",
      timestamp: options.timestamp ?? new Date().toISOString(),
      page: options.page ?? 1,
      page_size: options.pageSize ?? 20,
      order: options.order ?? "asc",
    } satisfies PendingIconProposalSearchRequest;
    return this.signedJsonRequest<PendingIconProposalListResponse>(
      "POST",
      "/v2/admin/icon-proposals/search",
      query,
      ADMIN_SIGNATURE_HEADER
    );
  }

  async approveIcon(assetId: string, iconHash: string): Promise<AssetResponse> {
    validateAssetId(assetId);
    return this.submitAdminAssetAction({
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "approve_icon",
      asset_id: assetId,
      icon_hash: iconHash.toLowerCase(),
    });
  }

  async rejectIcon(assetId: string, iconHash: string, reason?: string): Promise<AssetResponse> {
    validateAssetId(assetId);
    return this.submitAdminAssetAction({
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "reject_icon",
      asset_id: assetId,
      icon_hash: iconHash.toLowerCase(),
      reason,
    });
  }

  async setIcon(assetId: string, pngBytes: Uint8Array, options: AdminIconUploadOptions = {}): Promise<AssetResponse> {
    validateAssetId(assetId);
    const action = this.withAdminActionMeta({
      signing_context: ADMIN_SIGNING_CONTEXT,
      actor_pubkey: this.getActorPubkey(),
      operation: "set_icon",
      asset_id: assetId,
      icon_hash: hashIconBytes(pngBytes),
      nonce: options.nonce,
      timestamp: options.timestamp,
    }) satisfies SetIconAction;
    const canonicalAction = canonicalJson(action);
    const signature = await this.getSigner().signData(canonicalAction);
    return this.httpClient.put<AssetResponse>(
      `/v2/admin/assets/${assetId}/icon`,
      {
        action: JSON.parse(canonicalAction) as SetIconAction,
        icon: iconBytesToBase64(pngBytes),
      },
      {
        headers: { [ADMIN_SIGNATURE_HEADER]: signature },
        retry: true,
      }
    );
  }

  async migrateAsset(assetId: string): Promise<IssuerActionResponse> {
    validateAssetId(assetId);
    const action = this.withAdminActionMeta({
      signing_context: ADMIN_SIGNING_CONTEXT,
      operation: "migrate_asset",
      asset_id: assetId,
    }) satisfies MigrateAssetAction;
    return this.signedJsonRequest<IssuerActionResponse>(
      "POST",
      `/v2/assets/${assetId}/migrate`,
      action,
      ADMIN_SIGNATURE_HEADER
    );
  }

  static async signAdminAction(
    action: AdminLifecycleAction,
    privateKey: string | Uint8Array
  ): Promise<{ canonicalJson: string; signature: string }> {
    requireActorPubkey(action);
    const canonical = canonicalJson(action);
    return { canonicalJson: canonical, signature: await signData(canonical, { privateKey }) };
  }

  static async signAdminAssetAction(
    action: AdminAssetAction | MigrateAssetAction | SetIconAction,
    privateKey: string | Uint8Array
  ): Promise<{ canonicalJson: string; signature: string }> {
    requireActorPubkey(action);
    const canonical = canonicalJson(action);
    return { canonicalJson: canonical, signature: await signData(canonical, { privateKey }) };
  }

  private withAdminActionMeta<T extends object>(
    action: T
  ): T & { actor_pubkey: string; timestamp: string; nonce: string } {
    const meta = action as { actor_pubkey?: string; timestamp?: string; nonce?: string };
    const actorPubkey = meta.actor_pubkey ?? this.getActorPubkey();
    validatePubkey(actorPubkey);
    return {
      ...action,
      actor_pubkey: actorPubkey.toLowerCase(),
      nonce: meta.nonce ?? uuidv4(),
      timestamp: meta.timestamp ?? new Date().toISOString(),
    };
  }

  private getActorPubkey(): string {
    const actorPubkey = this.actorPubkey ?? this.getSigner().getPubkey?.();
    if (!actorPubkey) throw new Error("actor_pubkey is required for admin actions");
    validatePubkey(actorPubkey);
    return actorPubkey.toLowerCase();
  }
}

function requireActorPubkey(action: { actor_pubkey?: string }): asserts action is { actor_pubkey: string } {
  if (!action.actor_pubkey) throw new Error("actor_pubkey is required for admin actions");
  validatePubkey(action.actor_pubkey);
}
