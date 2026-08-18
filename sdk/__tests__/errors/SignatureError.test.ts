import { SignatureError } from "../../src/errors/SignatureError.ts";

describe("SignatureError", () => {
  it("creates error with message", () => {
    const error = new SignatureError("Invalid signature");
    expect(error.message).toBe("Invalid signature");
  });

  it("has correct name", () => {
    const error = new SignatureError("Invalid signature");
    expect(error.name).toBe("SignatureError");
  });

  it("has signature_error code", () => {
    const error = new SignatureError("Invalid signature");
    expect((error as any).code).toBe("signature_error");
  });

  it("stores reason in details", () => {
    const error = new SignatureError("Invalid signature", {
      reason: "signature verification failed",
    });
    expect((error as any).details?.reason).toBe("signature verification failed");
  });

  it("stores expectedContext in details", () => {
    const error = new SignatureError("Invalid signature", {
      expectedContext: "io.registry.action",
    });
    expect((error as any).details?.expectedContext).toBe("io.registry.action");
  });

  it("preserves stack trace", () => {
    const error = new SignatureError("Invalid signature");
    expect(error.stack).toBeDefined();
    expect(error.stack).toContain("SignatureError");
  });
});
