import { expect, test, type Page } from '@playwright/test';
import { AxeBuilder } from '@axe-core/playwright';
import { gotoStable } from './helpers';

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  return results.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious');
}

test('shelf has no critical or serious axe violations', async ({ page }) => {
  await gotoStable(page, '/');
  const violations = await seriousViolations(page);
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
});

test('reader has no critical or serious axe violations', async ({ page }) => {
  await gotoStable(page, '/Homer/iliad/iliad-bk-1.html');
  const violations = await seriousViolations(page);
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
});
