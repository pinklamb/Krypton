// Usage: node count.mjs <file...>
// Prints "<file>\t<eslint decisions>\t<eslint minus default-params and ?.>" via ESLint's complexity rule.
// Each file body is wrapped in a function so top-level decisions are reported too.
import { Linter } from "eslint";
import tsParser from "@typescript-eslint/parser";
import { readFileSync } from "node:fs";

const extras = {
  meta: { type: "problem", schema: [] },
  create(context) {
    const report = (node) => context.report({ node, message: "extra" });
    return {
      AssignmentPattern: report,
      MemberExpression: (n) => n.optional && report(n),
      CallExpression: (n) => n.optional && report(n),
    };
  },
};

const linter = new Linter({ configType: "flat" });
for (const file of process.argv.slice(2)) {
  let code = readFileSync(file, "utf8").replace(/^\uFEFF/, "");
  code = code.replace(/^(\s*)(import\s[^;]*?from\s*['"][^'"]+['"];?|import\s*['"][^'"]+['"];?|export\s+\*[^;]*;|export\s*\{[^}]*\}[^;\n]*;?|export\s+(default\s+)?)/gm, "$1");
  const wrapped = `function __krypton_top__() {\n${code}\n}`;
  const isTs = /\.(ts|tsx|mts|cts)$/.test(file);
  const messages = linter.verify(wrapped, {
    files: ["**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}"],
    plugins: { krypton: { rules: { extras } } },
    linterOptions: { noInlineConfig: true, reportUnusedDisableDirectives: "off" },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      parser: isTs ? tsParser : undefined,
      parserOptions: { ecmaFeatures: { jsx: !isTs || file.endsWith("x") } },
    },
    rules: { complexity: ["error", 0], "krypton/extras": "error" },
  }, { filename: "input" + file.slice(file.lastIndexOf(".")) }); // node_modules paths are ignored by default
  if (messages.some((m) => m.fatal)) { console.log(`${file}\tPARSE_ERROR\tPARSE_ERROR`); continue; }
  let total = 0, extra = 0;
  for (const m of messages) {
    const n = /complexity of (\d+)/.exec(m.message);
    if (n) total += Number(n[1]) - 1;
    if (m.ruleId === "krypton/extras") extra += 1;
  }
  console.log(`${file}\t${total}\t${total - extra}`);
}
