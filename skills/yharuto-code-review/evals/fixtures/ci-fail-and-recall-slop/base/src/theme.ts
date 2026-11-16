export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "talk_portal_theme";

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function resolveTheme(saved: string | null, prefersDark: boolean): Theme {
  if (saved === "dark" || saved === "light") {
    return saved;
  }
  return prefersDark ? "dark" : "light";
}

export function saveThemePreference(storage: KeyValueStorage, theme: Theme): void {
  storage.setItem(THEME_STORAGE_KEY, theme);
}
