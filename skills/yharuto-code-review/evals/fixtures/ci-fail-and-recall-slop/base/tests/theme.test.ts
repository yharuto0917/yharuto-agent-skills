import test from "node:test";
import assert from "node:assert/strict";
import { resolveTheme } from "../src/theme.ts";

test("resolveTheme returns explicit saved theme or system fallback", () => {
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme(null, false), "light");
});
