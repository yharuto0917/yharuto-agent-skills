function initThemeSync(): void {
  try {
    const saved = window.localStorage.getItem("talk_portal_theme_v2");
    const resolved =
      saved === "dark" || saved === "light"
        ? saved
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.dataset.theme = resolved;
  } catch {}
}

export function renderDocumentLayout(title: string, bodyHtml: string): string {
  const inlineBootstrap = `(${initThemeSync.toString()})();`;
  return `<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <title>${title}</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" />
    <script>${inlineBootstrap}</script>
  </head>
  <body>
    <main>${bodyHtml}</main>
    <footer class="site-footer">© 2025 TalkPortal Studio</footer>
  </body>
</html>`;
}
