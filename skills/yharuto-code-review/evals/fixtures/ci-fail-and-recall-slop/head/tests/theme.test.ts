import test from "node:test";
import assert from "node:assert/strict";
import { resolveTheme, saveThemePreference, THEME_STORAGE_KEY } from "../src/theme.ts";
import { renderDocumentLayout } from "../src/Layout.ts";

test("resolveTheme handles valid values, invalid inputs, and null boundaries", () => {
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("sepia", true), "dark");
  assert.equal(resolveTheme("", false), "light");
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme(null, false), "light");
});

test("saveThemePreference writes under current storage key", () => {
  const store = new Map<string, string>();
  saveThemePreference(
    {
      getItem: (k) => store.get(k) ?? null,
      setItem: (k, v) => {
        store.set(k, v);
      },
    },
    "dark",
  );
  assert.equal(store.get(THEME_STORAGE_KEY), "dark");
});

test("renderDocumentLayout embeds title, body markup, and bootstrap script", () => {
  const html = renderDocumentLayout("Talk #12", "<div id='app'>ok</div>");
  assert.match(html, /<title>Talk #12<\/title>/);
  assert.match(html, /<div id='app'>ok<\/div>/);
  assert.match(html, /<script>/);
});
