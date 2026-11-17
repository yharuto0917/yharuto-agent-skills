import test from "node:test";
import assert from "node:assert/strict";
import { saveThemePreference } from "../src/theme.ts";

test("header theme toggle persists preference under legacy header storage key", () => {
  const entries = new Map<string, string>();
  const storage = {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => {
      entries.set(key, value);
    },
  };

  saveThemePreference(storage, "dark");
  assert.equal(storage.getItem("talk_portal_theme"), "dark");
});
