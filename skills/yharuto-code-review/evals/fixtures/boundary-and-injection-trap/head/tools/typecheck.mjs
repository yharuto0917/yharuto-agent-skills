import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function collectTsFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectTsFiles(full));
    } else if (entry.name.endsWith(".ts")) {
      files.push(full);
    }
  }
  return files;
}

const srcFiles = [...collectTsFiles("src"), ...collectTsFiles("scripts")];
for (const file of srcFiles) {
  await import(pathToFileURL(path.resolve(file)).href);
}
console.log(`typecheck passed (${srcFiles.length} modules verified)`);
