import { RegistryError } from "../../src/errors/RegistryError.ts";

describe("RegistryError", () => {
  it("creates error with message", () => {
    const error = new RegistryError("Test error", { code: "test_error" });
    expect(error.message).toBe("Test error");
  });

  it("has correct name", () => {
    const error = new RegistryError("Test error", { code: "test_error" });
    expect(error.name).toBe("RegistryError");
  });

  it("has code from options", () => {
    const error = new RegistryError("Test error", { code: "test_error" });
    expect((error as any).code).toBe("test_error");
  });

  it("has details from options", () => {
    const details = { key: "value" };
    const error = new RegistryError("Test error", {
      code: "test_error",
      details,
    });
    expect((error as any).details).toEqual(details);
  });

  it("preserves stack trace", () => {
    const error = new RegistryError("Test error", { code: "test_error" });
    expect(error.stack).toBeDefined();
    expect(error.stack).toContain("RegistryError");
  });

});
