import { canonicalJson } from "../../src/utils/canonicalJson.ts";

// ============= Standard Tests =============

describe("canonicalJson", () => {
  describe("basic types", () => {
    it("serializes null", () => {
      expect(canonicalJson(null)).toBe("null");
    });

    it("serializes booleans", () => {
      expect(canonicalJson(true)).toBe("true");
      expect(canonicalJson(false)).toBe("false");
    });

    it("serializes numbers", () => {
      expect(canonicalJson(42)).toBe("42");
      expect(canonicalJson(3.14159)).toBe("3.14159");
      expect(canonicalJson(-100)).toBe("-100");
      expect(canonicalJson(0)).toBe("0");
      expect(canonicalJson(1e10)).toBe("10000000000");
    });

    it("serializes strings", () => {
      expect(canonicalJson("hello")).toBe('"hello"');
      expect(canonicalJson("")).toBe('""');
    });
  });

  describe("arrays", () => {
    it("serializes empty array", () => {
      expect(canonicalJson([])).toBe("[]");
    });

    it("serializes array of primitives", () => {
      expect(canonicalJson([1, 2, 3])).toBe("[1,2,3]");
      expect(canonicalJson(["a", "b", "c"])).toBe('["a","b","c"]');
    });

    it("serializes nested arrays", () => {
      expect(canonicalJson([[1, 2], [3, 4]])).toBe("[[1,2],[3,4]]");
    });

    it("preserves array order", () => {
      const arr = [3, 1, 2];
      expect(canonicalJson(arr)).toBe("[3,1,2]");
    });
  });

  describe("objects", () => {
    it("serializes empty object", () => {
      expect(canonicalJson({})).toBe("{}");
    });

    it("sorts object keys alphabetically", () => {
      const obj = {
        c: 1,
        b: 2,
        a: 3,
      };
      expect(canonicalJson(obj)).toBe('{"a":3,"b":2,"c":1}');
    });

    it("recursively sorts nested objects", () => {
      const obj = {
        z: {
          c: 1,
          b: 2,
        },
        a: {
          y: 1,
          x: 2,
        },
      };
      expect(canonicalJson(obj)).toBe('{"a":{"x":2,"y":1},"z":{"b":2,"c":1}}');
    });

    it("handles mixed value types", () => {
      const obj = {
        str: "hello",
        num: 42,
        bool: true,
        arr: [1, 2],
        nested: { key: "value" },
      };
      expect(canonicalJson(obj)).toBe(
        '{"arr":[1,2],"bool":true,"nested":{"key":"value"},"num":42,"str":"hello"}'
      );
    });
  });

  describe("idempotency", () => {
    it("produces same output for same input", () => {
      const obj = { c: 1, b: 2, a: 3 };
      const result1 = canonicalJson(obj);
      const result2 = canonicalJson(obj);
      expect(result1).toBe(result2);
    });

    it("order of keys doesn\'t affect output", () => {
      const obj1 = { a: 1, b: 2, c: 3 };
      const obj2 = { c: 3, b: 2, a: 1 };
      const obj3 = { b: 2, a: 1, c: 3 };

      expect(canonicalJson(obj1)).toBe(canonicalJson(obj2));
      expect(canonicalJson(obj2)).toBe(canonicalJson(obj3));
    });
  });

  describe("invalid JSON values", () => {
    it("rejects non-finite numbers", () => {
      expect(() => canonicalJson({ value: Number.NaN })).toThrow(/non-finite/);
      expect(() => canonicalJson(Number.POSITIVE_INFINITY)).toThrow(/non-finite/);
    });

    it("rejects circular references", () => {
      const value: Record<string, unknown> = {};
      value.self = value;
      expect(() => canonicalJson(value)).toThrow(/circular/);
    });

    it("follows JSON semantics for undefined values", () => {
      expect(canonicalJson({ omitted: undefined, present: null })).toBe('{"present":null}');
      expect(canonicalJson([undefined])).toBe("[null]");
    });
  });
});
