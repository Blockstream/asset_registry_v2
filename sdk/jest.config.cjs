/** @type {import('jest').Config} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/__tests__"],
  testMatch: ["**/*.test.ts"],
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json"],
  moduleNameMapper: {
    "^(\\.{1,2}/.*)\\.js$": "$1",
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  transform: {
    "\\.ts$": [
      "ts-jest",
      {
        tsconfig: {
          module: "ESNext",
          moduleResolution: "bundler",
          allowImportingTsExtensions: true,
          noEmit: true,
        },
      },
    ],
  },
  transformIgnorePatterns: ["node_modules/"],
  extensionsToTreatAsEsm: [".ts"],
  collectCoverageFrom: ["src/**/*.ts", "!src/**/*.d.ts", "!src/generated/**"],
  coveragePathIgnorePatterns: ["/node_modules/", "/dist/"],
};
