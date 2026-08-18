import type { components } from "../generated/openapi.js";

export type AddAdminAction = components["schemas"]["AddAdminAction"];
export type UpdateAdminPermissionsAction = components["schemas"]["UpdateAdminPermissionsAction"];
export type UpdateAdminNameAction = components["schemas"]["UpdateAdminNameAction"];
export type RemoveAdminAction = components["schemas"]["RemoveAdminAction"];
export type UpdateAdminAnnotationsAction = components["schemas"]["UpdateAdminAnnotationsAction"];
export type ForceDelistAssetAction = components["schemas"]["ForceDelistAssetAction"];
export type ForceRelistAssetAction = components["schemas"]["ForceRelistAssetAction"];
export type MigrateAssetAction = components["schemas"]["MigrateAssetAction"];
export type ApproveIconAction = components["schemas"]["ApproveIconAction"];
export type RejectIconAction = components["schemas"]["RejectIconAction"];
export type SetIconAction = components["schemas"]["SetIconAction"];
export type AdminIconUploadRequest = components["schemas"]["AdminIconUploadRequest"];
export type PendingIconProposalSearchRequest = components["schemas"]["PendingIconProposalSearchRequest"];
export type AdminAnnotationsUpdate = components["schemas"]["AdminAnnotationsUpdateRequest"];
export type AdminPermission = NonNullable<AddAdminAction["permissions"]>[number];

export type AdminLifecycleAction =
  AddAdminAction | UpdateAdminPermissionsAction | UpdateAdminNameAction | RemoveAdminAction;

export type AdminAssetAction =
  UpdateAdminAnnotationsAction | ForceDelistAssetAction | ForceRelistAssetAction | ApproveIconAction | RejectIconAction;

type AdminActionMeta = "actor_pubkey" | "timestamp" | "nonce";
type WithOptionalAdminMeta<T extends AdminLifecycleAction | AdminAssetAction> = Omit<T, AdminActionMeta> &
  Partial<Pick<T, AdminActionMeta>>;

export type AdminLifecycleActionInput = AdminLifecycleAction extends infer T
  ? T extends AdminLifecycleAction
    ? WithOptionalAdminMeta<T>
    : never
  : never;

export type AdminAssetActionInput = AdminAssetAction extends infer T
  ? T extends AdminAssetAction
    ? WithOptionalAdminMeta<T>
    : never
  : never;
