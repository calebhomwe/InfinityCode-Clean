// TypeScript syntactic diagnostics for a single file (no project build).
// Usage: node Tools/ts_diag.js <absolute-path>
const ts = require("C:/Users/caleb/infinity-code/node_modules/typescript");
const fs = require("fs");

const file = process.argv[2];
if (!file) {
  process.exit(1);
}
const source = fs.readFileSync(file, "utf8");
const result = ts.transpileModule(source, {
  reportDiagnostics: true,
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, allowJs: true },
});
const diagnostics = (result.diagnostics || []).map((d) => {
  const pos = d.file
    ? d.file.getLineAndCharacterOfPosition(d.start)
    : { line: 0, character: 0 };
  return {
    line: pos.line + 1,
    column: pos.character + 1,
    message: ts.flattenDiagnosticMessageText(d.messageText, " "),
  };
});
process.stdout.write(JSON.stringify(diagnostics));
