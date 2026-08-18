import type { SignerBase } from "../../../src/signer/signer.js";
import { bytesToBase64 } from "../../../src/utils/signatures.js";

export class RemoteSigner implements SignerBase {
  async signData(data: string): Promise<string> {
    const hex = Buffer.from(data, "utf8").toString("hex");
    const signedData = await fetch("http://localhost:8002/sign", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${process.env.SIGNER_TOKEN ?? ""}`,
      },
      body: JSON.stringify({
        message_hex: hex
      })
    });

    if (!signedData.ok) {
      throw new Error(`Remote signer failed with HTTP ${signedData.status}`);
    }

    const { signature_hex } = await signedData.json();
    return bytesToBase64(Buffer.from(signature_hex, "hex"));
  }
}
