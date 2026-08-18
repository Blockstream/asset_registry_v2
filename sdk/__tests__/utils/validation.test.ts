import { validateAssetId, validatePubkey, validateTimestamp } from "../../src/utils/validation.ts";

describe("validation", () => {
  it("accepts registry asset IDs and compressed public keys", () => {
    expect(() => validateAssetId("ab".repeat(32))).not.toThrow();
    expect(() => validatePubkey(`02${"ab".repeat(32)}`)).not.toThrow();
    expect(() => validatePubkey(`03${"cd".repeat(32)}`)).not.toThrow();
  });

  it("rejects malformed identifiers and uncompressed public keys", () => {
    expect(() => validateAssetId("ab")).toThrow(/64/);
    expect(() => validateAssetId("z".repeat(64))).toThrow(/hex/);
    expect(() => validatePubkey(`04${"ab".repeat(32)}`)).toThrow(/compressed/);
    expect(() => validatePubkey("ab".repeat(32))).toThrow(/66/);
  });

  it("validates parseable timestamps including the Unix epoch", () => {
    expect(() => validateTimestamp("1970-01-01T00:00:00Z")).not.toThrow();
    expect(() => validateTimestamp("not-a-timestamp")).toThrow(/timestamp/);
  });
});
