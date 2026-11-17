import test from "node:test";
import assert from "node:assert/strict";
import { applyPreviewPayload } from "../src/previewEmbed.ts";
import { listTenantUsersEndpoint } from "../src/admin/usersEndpoint.ts";
import { compileTemplateSource } from "../src/templateRegistry.ts";

test("applyPreviewPayload persists valid preview revision", async () => {
  const saved: Array<{ pageId: string; html: string }> = [];
  await applyPreviewPayload(
    { pageId: "p_1", htmlFragment: "<p>Hello</p>" },
    {
      async saveDraftRevision(pageId, html) {
        saved.push({ pageId, html });
      },
    },
  );
  assert.equal(saved.length, 1);
});

test("listTenantUsersEndpoint enforces admin role", () => {
  assert.throws(() =>
    listTenantUsersEndpoint({ userId: "u_1", role: "viewer" }, ["u_1"]),
  );
  const res = listTenantUsersEndpoint({ userId: "u_admin", role: "admin" }, ["u_1", "u_2"]);
  assert.equal(res.count, 2);
});

test("compileTemplateSource compiles html and rejects unknown extensions", () => {
  assert.equal(compileTemplateSource("a.html", " <div>x</div> "), "<div>x</div>");
  assert.throws(() => compileTemplateSource("a.xyz", "x"), /Unsupported template extension/);
});
