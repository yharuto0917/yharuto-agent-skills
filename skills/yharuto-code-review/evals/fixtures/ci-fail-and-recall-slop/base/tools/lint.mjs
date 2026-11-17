import fs from "node:fs";
import path from "node:path";
import { stripTypeScriptTypes } from "node:module";

function collectFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(full));
    } else if (entry.name.endsWith(".ts") || entry.name.endsWith(".mjs")) {
      files.push(full);
    }
  }
  return files;
}

const targets = [...collectFiles("src"), ...collectFiles("scripts"), ...collectFiles("tests")];
for (const file of targets) {
  const src = fs.readFileSync(file, "utf8");
  if (file.endsWith(".ts")) {
    stripTypeScriptTypes(src, { mode: "strip" });
  }
  if (/\bdebugger\s*;/.test(src)) {
    console.error(`Forbidden debugger statement in ${file}`);
    process.exit(1);
  }
}
console.log(`lint passed (${targets.length} files checked)`);
