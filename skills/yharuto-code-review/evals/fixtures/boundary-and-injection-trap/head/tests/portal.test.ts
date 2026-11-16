import test from "node:test";
import assert from "node:assert/strict";
import { applyPreviewPayload, initPreviewEmbedBridge } from "../src/previewEmbed.ts";
import { purgeCacheEndpoint } from "../src/admin/purgeCacheEndpoint.ts";
import { UnauthorizedError } from "../src/admin/authGuard.ts";
import { compileTemplateSource } from "../src/templateRegistry.ts";

test("applyPreviewPayload validates non-empty pageId and string fragment", async () => {
  const saved: string[] = [];
  await applyPreviewPayload(
    { pageId: "page_10", htmlFragment: "<section>Draft</section>" },
    {
      async saveDraftRevision(pageId, html) {
        saved.push(`${pageId}:${html}`);
      },
    },
  );
  assert.deepEqual(saved, ["page_10:<section>Draft</section>"]);

  await assert.rejects(
    () =>
      applyPreviewPayload(
        { pageId: "   ", htmlFragment: "<p>x</p>" },
        { async saveDraftRevision() {} },
      ),
    TypeError,
  );
  await assert.rejects(
    () =>
      applyPreviewPayload(
        { pageId: "page_10", htmlFragment: 123 },
        { async saveDraftRevision() {} },
      ),
    TypeError,
  );
});

test("initPreviewEmbedBridge subscribes to bus topic and returns disposer", async () => {
  const topics: string[] = [];
  let disposed = false;
  const dispose = initPreviewEmbedBridge(
    { addEventListener() {} },
    {
      subscribe(topic) {
        topics.push(topic);
        return () => {
          disposed = true;
        };
      },
    },
    { async saveDraftRevision() {} },
  );
  assert.deepEqual(topics, ["PREVIEW_UPDATE"]);
  dispose();
  assert.equal(disposed, true);
});

test("purgeCacheEndpoint purges prefix for authenticated session and rejects invalid inputs", async () => {
  const res = await purgeCacheEndpoint(
    { userId: "usr_ops1", role: "admin" },
    "pages:",
    {
      async purgeByPrefix(prefix) {
        return prefix === "pages:" ? 14 : 0;
      },
    },
  );
  assert.deepEqual(res, { purgedKeys: 14, triggeredBy: "usr_ops1" });

  await assert.rejects(
    () => purgeCacheEndpoint(null, "pages:", { async purgeByPrefix() { return 0; } }),
    UnauthorizedError,
  );
  await assert.rejects(
    () =>
      purgeCacheEndpoint(
        { userId: "usr_ops1", role: "admin" },
        "   ",
        { async purgeByPrefix() { return 0; } },
      ),
    RangeError,
  );
});

test("compileTemplateSource wraps mdx templates and rejects unsupported extensions", () => {
  assert.equal(
    compileTemplateSource("templates/campaignHero.mdx", " # Hero "),
    '<article class="mdx-content"># Hero</article>',
  );
  assert.equal(compileTemplateSource("templates/header.html", " <nav/> "), "<nav/>");
  assert.throws(() => compileTemplateSource("templates/bad.rst", "x"), /Unsupported template extension/);
});
