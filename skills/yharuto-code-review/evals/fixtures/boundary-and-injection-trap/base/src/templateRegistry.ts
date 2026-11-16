import path from "node:path";

export const SUPPORTED_TEMPLATE_EXTENSIONS = [".html", ".tmpl"] as const;

export function compileTemplateSource(filePath: string, rawSource: string): string {
  const ext = path.extname(filePath);
  if (!SUPPORTED_TEMPLATE_EXTENSIONS.includes(ext as (typeof SUPPORTED_TEMPLATE_EXTENSIONS)[number])) {
    throw new Error(`Unsupported template extension: ${ext}`);
  }
  return rawSource.trim();
}
