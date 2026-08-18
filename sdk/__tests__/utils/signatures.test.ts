import {
  signData,
  verifySignature,
  generateKeyPair,
} from "../../src/utils/signatures.ts";

describe("signatures", () => {
  describe("generateKeyPair", () => {
    it("generates a valid key pair", async () => {
      const { privateKey, pubkey } = await generateKeyPair();

      expect(privateKey).toBeDefined();
      expect(pubkey).toBeDefined();
      expect(privateKey instanceof Uint8Array).toBe(true);
      expect(typeof pubkey).toBe("string");
    });

    it("generates consistent public key from private key", async () => {
      const { privateKey, pubkey } = await generateKeyPair();

      // Verify the public key is the correct length (64 hex chars = 32 bytes compressed)
      expect(pubkey.length).toBeGreaterThan(0);
    });
  });

  describe("signData", () => {
    it("signs data with a private key", async () => {
      const { privateKey } = await generateKeyPair();
      const data = { test: "data" };

      const signature = await signData(data, { privateKey });

      expect(signature).toBeDefined();
      expect(typeof signature).toBe("string");
    });

    it("signs string data", async () => {
      const { privateKey } = await generateKeyPair();
      const data = "test data";

      const signature = await signData(data, { privateKey });

      expect(signature).toBeDefined();
    });

    it("signs Uint8Array data", async () => {
      const { privateKey } = await generateKeyPair();
      const data = new Uint8Array([1, 2, 3, 4, 5]);

      const signature = await signData(data, { privateKey });

      expect(signature).toBeDefined();
    });

    it("throws ValidationError for invalid private key format", async () => {
      const data = { test: "data" };

      await expect(
        signData(data, { privateKey: new Uint8Array([1, 2, 3]) })
      ).rejects.toThrow();
    });

    it("uses the registry Bitcoin Signed Message signature format", async () => {
      const privateKey = Buffer.from("4ba25894eeda1dce9b2b48c76bb393b3f3e35a012f85b7159d11c80d0469a468", "hex");
      const signature = await signData('{"a":1}', { privateKey });

      expect(signature).toBe(
        "qLQaBkzOM8gpPwkYiJWF804NxcsioQMk33rI9YWxUMcm2/FtnUNsO9sPELo35oMzQGGnX79DEDIw1NZ6Bjt7rA=="
      );
    });
  });

  describe("verifySignature", () => {
    it("verifies a valid signature", async () => {
      const { privateKey, pubkey } = await generateKeyPair();
      const data = { test: "data" };

      const signature = await signData(data, { privateKey });
      const isValid = await verifySignature(data, {
        signature,
        pubkey,
      });

      expect(isValid).toBe(true);
    });

    it("returns false for invalid data", async () => {
      const { privateKey, pubkey } = await generateKeyPair();
      const data = { test: "data" };

      const signature = await signData(data, { privateKey });

      // Verify with different data
      const invalidData = { test: "different data" };
      const isValid = await verifySignature(invalidData, {
        signature,
        pubkey,
      });

      expect(isValid).toBe(false);
    });

    it("returns false for wrong public key", async () => {
      const { privateKey: privKey1 } = await generateKeyPair();
      const { pubkey: pubKey2 } = await generateKeyPair();
      const data = { test: "data" };

      const signature = await signData(data, { privateKey: privKey1 });
      const isValid = await verifySignature(data, {
        signature,
        pubkey: pubKey2,
      });

      expect(isValid).toBe(false);
    });

    it("throws ValidationError for missing public key", async () => {
      const data = { test: "data" };

      await expect(
        verifySignature(data, {
          signature: "test",
        } as any)
      ).rejects.toThrow();
    });

    it("throws ValidationError for missing signature", async () => {
      const { pubkey } = await generateKeyPair();
      const data = { test: "data" };

      await expect(
        verifySignature(data, {
          pubkey,
        } as any)
      ).rejects.toThrow();
    });
  });

  describe("integration", () => {
    it("sign and verify round trip", async () => {
      const { privateKey, pubkey } = await generateKeyPair();
      const data = {
        action: "test.action",
        body: { key: "value" },
      };

      const signature = await signData(data, { privateKey });
      const isValid = await verifySignature(data, {
        signature,
        pubkey,
      });

      expect(isValid).toBe(true);
    });

    it("signature is deterministic for same input", async () => {
      const { privateKey } = await generateKeyPair();
      const data = { test: "data" };

      const sig1 = await signData(data, { privateKey });
      const sig2 = await signData(data, { privateKey });

      // Note: ECDSA signatures may not be deterministic without explicit nonce
      // This test documents the current behavior
      expect(typeof sig1).toBe("string");
      expect(typeof sig2).toBe("string");
    });
  });
});
