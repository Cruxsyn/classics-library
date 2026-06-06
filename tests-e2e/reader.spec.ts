import { expect, test } from '@playwright/test';
import { expectTheme, gotoStable, setTheme, THEMES, waitForStablePage } from './helpers';

test('reader prose and verse layouts are wired', async ({ page }) => {
  await gotoStable(page, '/Homer/iliad/iliad-bk-1.html');
  await expect(page.getByRole('navigation', { name: 'Reader navigation' })).toBeVisible();
  await expect(page.locator('.reader-title__book')).toContainText('The Iliad');
  await expect(page.locator('main.ptx-main')).toHaveAttribute('data-pagefind-body', 'true');

  const firstParagraph = page.locator('section.chapter > h2.heading + div.para').first();
  await expect(firstParagraph).toBeVisible();
  await expect
    .poll(() => firstParagraph.evaluate((node) => getComputedStyle(node).textAlign))
    .toBe('justify');
  await expect
    .poll(() => firstParagraph.evaluate((node) => getComputedStyle(node).fontFamily))
    .toContain('Source Serif');
  await expect
    .poll(() => firstParagraph.evaluate((node) => Number.parseFloat(getComputedStyle(node, '::first-letter').fontSize)))
    .toBeGreaterThan(40);

  await gotoStable(page, '/Virgil/aeneid/aeneid-bk-1.html');
  const verseLine = page.locator('div.line').first();
  await expect(verseLine).toBeVisible();
  await expect
    .poll(() => verseLine.evaluate((node) => getComputedStyle(node).textAlign))
    .not.toBe('justify');
});

test.describe('reader visual snapshots @visual', () => {
  for (const theme of THEMES) {
    test(`prose chapter ${theme}`, async ({ page }) => {
      await gotoStable(page, '/Homer/iliad/iliad-bk-1.html');
      await setTheme(page, theme);
      await expectTheme(page, theme);
      await waitForStablePage(page);
      await expect(page).toHaveScreenshot(`reader-prose-${theme}.png`);
    });

    test(`verse chapter ${theme}`, async ({ page }) => {
      await gotoStable(page, '/Virgil/aeneid/aeneid-bk-1.html');
      await setTheme(page, theme);
      await expectTheme(page, theme);
      await waitForStablePage(page);
      await expect(page).toHaveScreenshot(`reader-verse-${theme}.png`);
    });
  }
});
