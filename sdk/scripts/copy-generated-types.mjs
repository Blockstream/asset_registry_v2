import { copyFile, mkdir } from "node:fs/promises";

const outputDirectory = new URL("../dist/types/generated/", import.meta.url);

await mkdir(outputDirectory, { recursive: true });
await copyFile(new URL("../src/generated/openapi.d.ts", import.meta.url), new URL("openapi.d.ts", outputDirectory));
