import { expect, test } from '@playwright/test';
import { expectTheme, gotoStable, waitForStablePage } from './helpers';

test('theme cycle persists across shelf, reader, and back to shelf', async ({ page }) => {
  await gotoStable(page, '/');
  const toggle = page.getByRole('button', { name: /Change color theme/ });

  await expect(page.locator('#theme-toggle-label')).toHaveText('Light');
  await toggle.click();
  await expectTheme(page, 'dark');
  await toggle.click();
  await expectTheme(page, 'sepia');

  await page.reload();
  await waitForStablePage(page);
  await expectTheme(page, 'sepia');

  await page.locator('[data-work-id="Homer/iliad"]').click();
  await page.waitForURL('**/Homer/iliad/**');
  await waitForStablePage(page);
  await expectTheme(page, 'sepia');

  await page.getByRole('link', { name: 'Back to library' }).click();
  await page.waitForURL('**/');
  await waitForStablePage(page);
  await expectTheme(page, 'sepia');
});
