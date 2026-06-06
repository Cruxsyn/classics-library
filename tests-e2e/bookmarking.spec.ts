import { expect, test } from '@playwright/test';
import { gotoStable, waitForStablePage } from './helpers';

test('keyboard bookmarking, progress, and resume persistence', async ({ page }) => {
  await gotoStable(page, '/Homer/iliad/iliad-bk-1.html');

  await page.keyboard.press('B');
  await expect(page.locator('.reader-live')).toHaveText('Bookmark added');
  await expect(page.locator('.reader-has-bookmark, .reader-has-bookmark-line')).toHaveCount(1);

  const bookmarked = await page.evaluate(() => JSON.parse(localStorage.getItem('library:reading:v1') ?? '{}'));
  expect(bookmarked['Homer/iliad'].bookmarks).toHaveLength(1);
  expect(bookmarked['Homer/iliad'].bookmarks[0].anchor).toBeTruthy();

  const progress = page.getByRole('progressbar', { name: 'Reading progress' });
  const before = Number(await progress.getAttribute('aria-valuenow'));
  await page.locator('#iliad-bk-1-p-20').scrollIntoViewIfNeeded();
  await expect.poll(async () => Number(await progress.getAttribute('aria-valuenow'))).toBeGreaterThan(before);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const state = JSON.parse(localStorage.getItem('library:reading:v1') ?? '{}')['Homer/iliad'];
        return state?.location?.offset ?? 0;
      }),
    )
    .toBeGreaterThan(0);

  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('library:reading:v1') ?? '{}')['Homer/iliad']);
  expect(saved.status).toBe('reading');
  expect(saved.location.anchor).toBeTruthy();

  await page.reload();
  await waitForStablePage(page);
  await page.waitForFunction((anchor) => {
    const target = document.getElementById(anchor);
    if (target === null) {
      return false;
    }
    const rect = target.getBoundingClientRect();
    return rect.top >= -20 && rect.top < window.innerHeight * 0.75;
  }, saved.location.anchor);
});
