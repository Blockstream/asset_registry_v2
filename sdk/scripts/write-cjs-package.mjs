import { mkdir, writeFile } from "node:fs/promises";

const outputDirectory = new URL("../dist/cjs/", import.meta.url);

await mkdir(outputDirectory, { recursive: true });
await writeFile(new URL("package.json", outputDirectory), '{"type":"commonjs"}\n');
