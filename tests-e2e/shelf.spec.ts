import { expect, test } from '@playwright/test';
import { gotoStable, seedReadingStore, setTheme, THEMES, waitForStablePage } from './helpers';

const now = 1_800_000_000_000;

test('shelf grid, covers, Pagefind search, and reading states', async ({ page }) => {
  await seedReadingStore(page, {
    'Homer/iliad': {
      status: 'reading',
      progress: 0.42,
      location: { chapter: 'iliad-bk-1.html', anchor: 'iliad-bk-1-p-9', offset: 320 },
      updatedAt: now,
      bookmarks: [],
    },
    'Virgil/aeneid': {
      status: 'finished',
      progress: 1,
      location: { chapter: 'aeneid-bk-12.html', anchor: 'aeneid-bk-12-p-1', offset: 0 },
      updatedAt: now - 10_000,
      bookmarks: [],
    },
  });

  await gotoStable(page, '/');
  await expect(page.locator('.book-card')).toHaveCount(18);
  await expect
    .poll(() =>
      page.locator('.book-card__cover img').evaluateAll((images) =>
        images.every((image) => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0),
      ),
    )
    .toBe(true);

  await expect(page.getByRole('heading', { name: 'Continue reading' })).toBeVisible();
  await expect(page.locator('.continue-card').filter({ hasText: 'The Iliad' })).toBeVisible();
  await expect(page.locator('[data-work-id="Homer/iliad"] .book-card__progress')).toHaveAttribute('style', /42%/);
  await expect(page.locator('[data-work-id="Virgil/aeneid"] .book-card__check')).toBeVisible();

  await page.getByLabel('Search').fill('wrath');
  await expect(page.getByRole('heading', { name: /Results for “wrath”/ })).toBeVisible();
  const iliadResult = page.locator('.search-result-list a[href*="/Homer/iliad/"]').first();
  await expect(iliadResult).toBeVisible();
  await expect(iliadResult).toContainText(/Iliad|Book/i);
});

test.describe('shelf visual snapshots @visual', () => {
  for (const theme of THEMES) {
    test(`shelf ${theme}`, async ({ page }) => {
      await gotoStable(page, '/');
      await setTheme(page, theme);
      await waitForStablePage(page);
      await expect(page).toHaveScreenshot(`shelf-${theme}.png`, { fullPage: true });
    });
  }
});
