import { ValidationError } from "../errors/ValidationError.js";

/** Serialize JSON deterministically for registry hashes and signed request bodies. */
export function canonicalJson(value: unknown): string {
  const ancestors = new Set<object>();

  function serialize(entry: unknown, inArray = false): string | undefined {
    if (entry === null) return "null";
    if (entry === undefined || typeof entry === "function" || typeof entry === "symbol") {
      return inArray ? "null" : undefined;
    }
    if (typeof entry === "string" || typeof entry === "boolean") {
      return JSON.stringify(entry);
    }
    if (typeof entry === "number") {
      if (!Number.isFinite(entry)) {
        throw new ValidationError("Canonical JSON does not support non-finite numbers");
      }
      return JSON.stringify(entry);
    }
    if (typeof entry === "bigint") {
      throw new ValidationError("Canonical JSON does not support bigint values");
    }
    if (typeof entry !== "object") {
      return undefined;
    }
    if (ancestors.has(entry)) {
      throw new ValidationError("Canonical JSON does not support circular references");
    }

    ancestors.add(entry);
    try {
      if (Array.isArray(entry)) {
        return `[${entry.map((item) => serialize(item, true) ?? "null").join(",")}]`;
      }

      const pairs = Object.keys(entry)
        .sort()
        .flatMap((key) => {
          const serialized = serialize((entry as Record<string, unknown>)[key]);
          return serialized === undefined ? [] : [`${JSON.stringify(key)}:${serialized}`];
        });
      return `{${pairs.join(",")}}`;
    } finally {
      ancestors.delete(entry);
    }
  }

  const serialized = serialize(value);
  if (serialized === undefined) {
    throw new ValidationError("Value cannot be represented as canonical JSON");
  }
  return serialized;
}
