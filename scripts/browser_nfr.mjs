// Browser timings for NFR-702, NFR-704 and NFR-705 against compose.prodcheck.yml (production nginx
// bundle + API on its own origin), in Microsoft Edge driven by Playwright.
//
//   npm i playwright@1.49.1      (in any scratch folder; this repository does not depend on it)
//   node scripts/browser_nfr.mjs
//
// Conditions (SRS section 5): the reference 4G profile — 10 Mbps down, 5 Mbps up, 100 ms round
// trip — on a phone-sized viewport, CPU throttled 4x as a mid-range phone, browser cache disabled
// so every open is a cold load. 50 trials per target; the target is met when p95 <= the limit.
// The AI is the mock provider, so NFR-702/705 time Bodega itself, not a real provider.
import { chromium } from 'playwright'

const WEB = 'http://127.0.0.1:8090'
const CODE = 'prodcheck-access-code'
const TRIALS = Number(process.env.TRIALS || 50)
const FOUR_G = { offline: false, latency: 100, downloadThroughput: (10e6) / 8, uploadThroughput: (5e6) / 8 }

const p95 = values => { const s = [...values].sort((a, b) => a - b); return s[Math.ceil(0.95 * s.length) - 1] }
const median = values => { const s = [...values].sort((a, b) => a - b); return s[Math.floor(s.length / 2)] }

const browser = await chromium.launch({ channel: 'msedge' })
const page = await browser.newPage({ viewport: { width: 412, height: 915 }, isMobile: false })
const errors = []
page.on('pageerror', error => errors.push(String(error)))
const cdp = await page.context().newCDPSession(page)
await cdp.send('Network.enable')
await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
await cdp.send('Network.emulateNetworkConditions', FOUR_G)
await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 })

await page.goto(`${WEB}/login`)
await page.fill('input[type=password]', CODE)
await page.click('button:has-text("Sign in")')
await page.waitForURL(/\/capture/)

// NFR-704: open the results dashboard (5,000 records, 100 items) until every item card is shown.
const dashboard = []
for (let i = 0; i < TRIALS; i++) {
  const started = Date.now()
  await page.goto(`${WEB}/analysis`)
  await page.waitForFunction(() => document.querySelectorAll('.item-card').length >= 100 &&
    !document.body.textContent.includes('Loading analysis'), null, { timeout: 30000 })
  dashboard.push((Date.now() - started) / 1000)
}

// NFR-705 (guidance): progress indicator within 1 s; outcome within 16 s.
const guidanceIndicator = [], guidanceOutcome = []
for (let i = 0; i < TRIALS; i++) {
  await page.goto(`${WEB}/analysis`)
  await page.waitForSelector('button:has-text("Get restocking advice"):not([disabled])')
  const started = Date.now()
  await page.click('button:has-text("Get restocking advice")')
  await page.waitForSelector('text=Getting advice', { timeout: 5000 })
  guidanceIndicator.push((Date.now() - started) / 1000)
  await page.waitForSelector('.guidance-result', { timeout: 20000 })
  guidanceOutcome.push((Date.now() - started) / 1000)
}

// NFR-702 / NFR-705 (extraction): a 50-row text log. Indicator within 1 s; the draft's 50 rows on
// screen within 8 s of the (instant, mock) AI response.
const lines = Array.from({ length: 50 }, (_, i) => `Product ${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))} ${1 + (i % 9)} 1.50`).join('\n')
const extractIndicator = [], draftRows = []
for (let i = 0; i < TRIALS; i++) {
  await page.goto(`${WEB}/capture`)
  await page.click('role=tab[name="Text"]')
  await page.fill('#sales-text', lines)
  const started = Date.now()
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/extract'))
  await page.click('button:has-text("Read text")')
  await page.waitForSelector('text=Reading your sales log', { timeout: 5000 })
  extractIndicator.push((Date.now() - started) / 1000)
  await response
  const responded = Date.now()
  await page.waitForFunction(() => document.querySelectorAll('section[aria-labelledby="draft-heading"] tbody tr').length === 50,
    null, { timeout: 20000 })
  draftRows.push((Date.now() - responded) / 1000)
}
await browser.close()

const summary = (values, limit) => ({ trials: values.length, p95: +p95(values).toFixed(3), median: +median(values).toFixed(3),
  max: +Math.max(...values).toFixed(3), limit, passed: p95(values) <= limit })
const report = {
  conditions: { network: '10 Mbps down / 5 Mbps up / 100 ms RTT', cpu: '4x throttled', cache: 'disabled', viewport: '412x915', browser: 'Microsoft Edge (Chromium)' },
  'NFR-704 dashboard render, 5,000 records': summary(dashboard, 3.0),
  'NFR-702 50 draft rows after AI response': summary(draftRows, 8.0),
  'NFR-705 extraction progress indicator': summary(extractIndicator, 1.0),
  'NFR-705 guidance progress indicator': summary(guidanceIndicator, 1.0),
  'NFR-705 guidance outcome': summary(guidanceOutcome, 16.0),
  pageErrors: errors,
}
console.log(JSON.stringify(report, null, 2))
process.exit(Object.values(report).some(v => v && v.passed === false) || errors.length ? 1 : 0)
