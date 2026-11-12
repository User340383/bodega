// NFR-732 accessibility and layout check against compose.prodcheck.yml (production nginx bundle and
// API on separate origins), in Microsoft Edge driven by Playwright with axe-core.
//
//   npm i playwright@1.49.1 @axe-core/playwright@4.10.1   (in a scratch folder; not a dependency)
//   node browser_a11y.mjs                                  (after scripts/prodcheck.py loaded data)
//
// Pages x widths (360, 768, 1280, 1920 px). For each combination:
//   - axe-core: 0 violations of the WCAG 2.0/2.1 A and AA rules;
//   - no horizontal page overflow;
//   - visible body text at least 16 px (M3 4.3, NFR-732);
//   - every visible button, link, input and select at least 44 x 44 px (M3 4.3), except inline
//     text links inside a sentence, which WCAG 2.5.8 exempts.
// Prints JSON; exit code 1 when any combination fails. Synthetic data only.
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'

const WEB = process.env.WEB || 'http://127.0.0.1:8090'
const CODE = process.env.CODE || 'prodcheck-access-code'
const WIDTHS = [360, 768, 1280, 1920]
const PAGES = [
  ['/capture', 'main'],
  ['/drafts', 'main'],
  ['/history', 'main'],
  ['/analysis', 'main'],
]

const browser = await chromium.launch({ channel: process.env.CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page = await context.newPage()
const results = []

async function layout() {
  return page.evaluate(() => {
    const visible = el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el)
      const onScreen = r.right > 0 && r.bottom > 0 && r.left < innerWidth  // Skip link: off-screen until focused.
      return onScreen && r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
    }
    const overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth
    const smallText = []
    for (const el of document.querySelectorAll('p, li, td, th, label, button, a, input, select, textarea, span, h1, h2, h3')) {
      if (!visible(el) || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue
      const size = parseFloat(getComputedStyle(el).fontSize)
      if (size < 16) smallText.push(`${el.tagName.toLowerCase()} ${size}px "${el.textContent.trim().slice(0, 30)}"`)
    }
    const smallTargets = []
    for (const el of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=tab]')) {
      if (!visible(el)) continue
      const inlineLink = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.closest('p')
      if (inlineLink) continue
      const r = el.getBoundingClientRect()
      const box = el.type === 'checkbox' || el.type === 'radio' ? (el.closest('label') || el).getBoundingClientRect() : r
      if (box.width < 44 || box.height < 44) {
        smallTargets.push(`${el.tagName.toLowerCase()} ${Math.round(box.width)}x${Math.round(box.height)} "${(el.textContent || el.name || el.type).trim().slice(0, 30)}"`)
      }
    }
    return { overflow, smallText, smallTargets }
  })
}

// The skip link appears only on keyboard focus; measure it then (M3 4.3: 44 x 44 px).
async function skipLink() {
  await page.keyboard.press('Tab')
  return page.evaluate(() => {
    const el = document.activeElement
    if (!el || !el.classList.contains('skip-link')) return []
    const r = el.getBoundingClientRect()
    return r.width < 44 || r.height < 44 ? [`skip link ${Math.round(r.width)}x${Math.round(r.height)}`] : []
  })
}

await page.goto(`${WEB}/login`)
const loginAxe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
results.push({ page: '/login', width: 1280, violations: loginAxe.violations.map(v => `${v.id} (${v.nodes.length})`),
  ...(await layout()) })
await page.fill('input[type=password]', CODE)
await page.click('button:has-text("Sign in")')
await page.waitForURL(/\/capture/)

for (const [path, ready] of PAGES) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`${WEB}${path}`)
    await page.waitForSelector(ready)
    await page.waitForFunction(() => !/Loading/.test(document.body.textContent), null, { timeout: 30000 })
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    const measured = await layout()
    measured.smallTargets.push(...await skipLink())
    results.push({ page: path, width, violations: axe.violations.map(v => `${v.id} (${v.nodes.length})`), ...measured })
  }
}
await browser.close()

const failed = results.filter(r => r.violations.length || r.overflow > 0 || r.smallText.length || r.smallTargets.length)
console.log(JSON.stringify({
  browser: 'Microsoft Edge (Chromium)', axe: '@axe-core/playwright 4.10.1, WCAG 2.0/2.1 A and AA',
  combinations: results.length, passed: results.length - failed.length,
  failed: failed.map(r => ({ page: r.page, width: r.width, violations: r.violations, overflow: r.overflow,
    smallText: r.smallText.slice(0, 5), smallTargets: r.smallTargets.slice(0, 5) })),
}, null, 2))
process.exit(failed.length ? 1 : 0)
