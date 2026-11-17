export function renderDocumentLayout(title: string, bodyHtml: string): string {
  return `<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <title>${title}</title>
  </head>
  <body>
    <main>${bodyHtml}</main>
    <footer class="site-footer">© TalkPortal Studio</footer>
  </body>
</html>`;
}
