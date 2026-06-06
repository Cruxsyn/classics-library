import { expect, type Page } from '@playwright/test';

export const THEMES = ['light', 'dark', 'sepia'] as const;
export type Theme = (typeof THEMES)[number];

export const readingStore = (entries: Record<string, unknown>): string =>
  JSON.stringify({ __version: 1, ...entries });

export async function waitForDecodedImages(page: Page): Promise<void> {
  await page.evaluate(() => {
    for (const image of Array.from(document.images)) {
      image.loading = 'eager';
    }
  });
  await page.waitForFunction(() =>
    Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0),
  );
  await page.evaluate(async () => {
    await Promise.all(Array.from(document.images).map((image) => image.decode().catch(() => undefined)));
  });
}

export async function waitForStablePage(page: Page): Promise<void> {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForLoadState('networkidle');
  await waitForDecodedImages(page);
}

export async function gotoStable(page: Page, path: string): Promise<void> {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(path);
  await waitForStablePage(page);
}

export async function setTheme(page: Page, theme: Theme): Promise<void> {
  await page.evaluate((nextTheme) => {
    localStorage.setItem('library:theme:v1', nextTheme);
    document.documentElement.dataset.theme = nextTheme;
  }, theme);
}

export async function expectTheme(page: Page, theme: Theme): Promise<void> {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('library:theme:v1'))).toBe(theme);
}

export async function seedReadingStore(page: Page, entries: Record<string, unknown>): Promise<void> {
  await page.addInitScript((payload) => {
    localStorage.setItem('library:reading:v1', payload);
  }, readingStore(entries));
}
