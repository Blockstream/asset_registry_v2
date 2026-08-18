const js = require("@eslint/js");
const globals = require("globals");
const typescriptEslintPlugin = require("@typescript-eslint/eslint-plugin");
const typescriptEslintParser = require("@typescript-eslint/parser");

module.exports = [
  {
    ignores: ["dist/", "node_modules/", "src/generated/"],
  },
  js.configs.recommended,
  {
    files: ["**/*.ts"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        ...globals.jest,
        ...globals.node,
      },
      parser: typescriptEslintParser,
      sourceType: "module",
    },
    plugins: {
      "@typescript-eslint": typescriptEslintPlugin,
    },
    rules: {
      ...typescriptEslintPlugin.configs.recommended.rules,
      "@typescript-eslint/explicit-function-return-type": "off",
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-undef": "off",
    },
  },
];
