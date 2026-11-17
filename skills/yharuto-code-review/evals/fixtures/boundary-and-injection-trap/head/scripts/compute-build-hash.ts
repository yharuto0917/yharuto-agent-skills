import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const HASHED_TEMPLATE_EXTENSIONS = [".html", ".tmpl"];

export function computeTemplatesBuildHash(templatesDir: string): string {
  const digest = crypto.createHash("sha256");
  if (!fs.existsSync(templatesDir)) {
    return digest.digest("hex");
  }
  const entries = fs.readdirSync(templatesDir).sort();
  for (const name of entries) {
    const ext = path.extname(name);
    if (!HASHED_TEMPLATE_EXTENSIONS.includes(ext)) {
      continue;
    }
    const content = fs.readFileSync(path.join(templatesDir, name), "utf8");
    digest.update(`${name}:${content}\n`);
  }
  return digest.digest("hex");
}
