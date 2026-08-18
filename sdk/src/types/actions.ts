import type { components } from "../generated/openapi.js";

export type ReplaceCategoryTagsAction = components["schemas"]["ReplaceCategoryTagsAction"];
export type ReplaceTradingVenuesAction = components["schemas"]["ReplaceTradingVenuesAction"];
export type ReplaceCustomAction = components["schemas"]["ReplaceCustomAction"];
export type SetCustomFieldAction = components["schemas"]["SetCustomFieldAction"];
export type DeleteCustomFieldAction = components["schemas"]["DeleteCustomFieldAction"];
export type DeregisterAction = components["schemas"]["DeregisterAction"];
export type RotateIssuerPubkeyAction = components["schemas"]["RotateIssuerPubkeyAction"];
export type ProposeIconAction = components["schemas"]["ProposeIconAction"];
export type IconProposalRequest = components["schemas"]["IconProposalRequest"];
export type IssuerIconProposalSearchRequest = components["schemas"]["IssuerIconProposalSearchRequest"];

export type IssuerAction =
  | ReplaceCategoryTagsAction
  | ReplaceTradingVenuesAction
  | ReplaceCustomAction
  | SetCustomFieldAction
  | DeleteCustomFieldAction
  | DeregisterAction
  | RotateIssuerPubkeyAction;

type IssuerActionMeta = "prev_action_hash" | "timestamp" | "nonce";
type WithOptionalIssuerMeta<T extends IssuerAction> = Omit<T, IssuerActionMeta> & Partial<Pick<T, IssuerActionMeta>>;

/** Raw issuer action accepted by the SDK before it fills chain metadata. */
export type IssuerActionInput = IssuerAction extends infer T
  ? T extends IssuerAction
    ? WithOptionalIssuerMeta<T>
    : never
  : never;
