import { ValidationError } from "../../src/errors/ValidationError.ts";

describe("ValidationError", () => {
  it("creates error with message", () => {
    const error = new ValidationError("Invalid input");
    expect(error.message).toBe("Invalid input");
  });

  it("has correct name", () => {
    const error = new ValidationError("Invalid input");
    expect(error.name).toBe("ValidationError");
  });

  it("has validation_error code", () => {
    const error = new ValidationError("Invalid input");
    expect((error as any).code).toBe("validation_error");
  });

  it("stores field in details", () => {
    const error = new ValidationError("Invalid input", {
      field: "email",
    });
    expect((error as any).details?.field).toBe("email");
  });

  it("stores reason in details", () => {
    const error = new ValidationError("Invalid input", {
      reason: "must be a valid email address",
    });
    expect((error as any).details?.reason).toBe("must be a valid email address");
  });

  it("preserves stack trace", () => {
    const error = new ValidationError("Invalid input");
    expect(error.stack).toBeDefined();
    expect(error.stack).toContain("ValidationError");
  });
});
