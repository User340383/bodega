// NFR-761: the core owner workflow in each browser engine, plus NFR-732 layout checks.
// Runs inside mcr.microsoft.com/playwright (Chromium, Firefox, WebKit) on the compose.prodcheck.yml
// network. A small TCP relay keeps the production origins (127.0.0.1:8090 SPA, :8091 API), so
// CORS and config.js behave exactly as in the production topology.
//
//   docker run --rm --network bodega-prodcheck_default -v "$PWD/scripts:/s:ro" -w /work \
//     mcr.microsoft.com/playwright:v1.49.1-noble sh -c "npm i -s playwright@1.49.1 && cp /s/browser_engines.mjs . && node browser_engines.mjs"
//
// Engines are the Playwright builds of Chromium, Firefox and WebKit (the Safari engine) on Linux;
// device rows emulate an Android Chrome and an iPhone Safari viewport and user agent. These are not
// the branded desktop browsers or physical phones that NFR-761 names.
import net from 'node:net'
import { chromium, devices, firefox, webkit } from 'playwright'

for (const [port, host, target] of [[8090, 'web', 8080], [8091, 'api', 8000]]) {
  net.createServer(client => {
    const upstream = net.connect(target, host)
    client.pipe(upstream).pipe(client)
    client.on('error', () => upstream.destroy()); upstream.on('error', () => client.destroy())
  }).listen(port, '127.0.0.1')
}

const WEB = 'http://127.0.0.1:8090'
const CODE = 'prodcheck-access-code'
const RUNS = [
  ['Chromium (desktop)', chromium, { viewport: { width: 1280, height: 900 } }],
  ['Firefox (desktop)', firefox, { viewport: { width: 1280, height: 900 } }],
  ['WebKit / Safari engine (desktop)', webkit, { viewport: { width: 1280, height: 900 } }],
  ['Android Chrome emulation (Pixel 7)', chromium, devices['Pixel 7']],
  ['iOS Safari emulation (iPhone 14)', webkit, devices['iPhone 14']],
]

async function layout(page) {
  return page.evaluate(() => {
    const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !el.closest('.sr-only, svg') }
    const small = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), textarea, [role=tab]')]
      .filter(visible).filter(el => !el.classList.contains('skip-link'))
      .map(el => ({ el, r: (el.type === 'checkbox' && el.closest('label') ? el.closest('label') : el).getBoundingClientRect() }))
      .filter(({ r }) => r.width < 43.5 || r.height < 43.5)
      .map(({ el, r }) => `${el.tagName.toLowerCase()}[${el.type || ''}] "${(el.textContent || '').trim().slice(0, 20)}" ${Math.round(r.width)}x${Math.round(r.height)}`)
    const text = [...document.querySelectorAll('p, li, td, label, button, input, dd, dt')].filter(visible)
      .filter(el => parseFloat(getComputedStyle(el).fontSize) < 16).length
    return { overflow: document.documentElement.scrollWidth - window.innerWidth, smallTargets: small, smallText: text }
  })
}

const results = []
for (const [name, engine, options] of RUNS) {
  const steps = {}
  const errors = []
  const browser = await engine.launch()
  // The owner's device shares the business timezone (APP_TIMEZONE); the container runs in UTC.
  const page = await (await browser.newContext({ ...options, timezoneId: 'America/Santo_Domingo' })).newPage()
  page.on('pageerror', error => errors.push(String(error)))
  const step = async (label, fn) => { try { await fn(); steps[label] = 'pass' } catch (error) { steps[label] = `FAIL: ${String(error).split('\n')[0].slice(0, 140)}` } }
  await step('sign in (NFR-718)', async () => {
    await page.goto(`${WEB}/login`)
    await page.fill('input[type=password]', CODE)
    await page.click('button:has-text("Sign in")')
    await page.waitForURL(/\/capture/)
  })
  await step('manual entry (UC3, FR-104)', async () => {
    await page.click('role=tab[name="Enter manually"]')
    const cells = page.locator('.manual-entry td input')
    await cells.nth(0).fill('Engine bread'); await cells.nth(1).fill('2.5'); await cells.nth(2).fill('1.20')
    await page.click('button:has-text("Save sales")')
    await page.waitForSelector('text=Sales saved.')
  })
  await step('text capture, verify and confirm (UC2, UC4)', async () => {
    await page.click('role=tab[name="Text"]')
    await page.fill('#sales-text', 'Engine milk 2 1.25')
    await page.click('button:has-text("Read text")')
    await page.waitForURL(/\/review\//)
    await page.waitForSelector('section[aria-labelledby="draft-heading"] tbody tr')
    for (const box of await page.locator('label.verification input[type=checkbox]').all()) await box.check()
    await page.click('button:has-text("Confirm sales")')
    await page.waitForSelector('text=Sales saved.')
  })
  await step('history shows confirmed sales (UC5)', async () => {
    await page.goto(`${WEB}/history`)
    await page.waitForSelector('text=Engine bread')
    await page.waitForSelector('text=Engine milk')
  })
  await step('results dashboard (UC7, UC9)', async () => {
    await page.goto(`${WEB}/analysis`)
    await page.waitForSelector('text=As of')
  })
  const sizes = {}
  await step('layout at this viewport (NFR-732)', async () => {
    for (const path of ['/capture', '/history', '/analysis', '/drafts']) {
      await page.goto(`${WEB}${path}`); await page.waitForLoadState('networkidle')
      const m = await layout(page)
      sizes[path] = m
      if (m.overflow > 0 || m.smallTargets.length || m.smallText) throw new Error(`${path} ${JSON.stringify(m)}`)
    }
  })
  await step('delete all data with typed DELETE (UC6, AC-21)', async () => {
    await page.goto(`${WEB}/history`)
    await page.click('button:has-text("Delete all data")')
    await page.fill('input[autocomplete=off] >> nth=-1', 'DELETE')
    await page.click('form.confirm-delete button.danger')
    await page.waitForSelector('text=/\\d+ records? deleted/')
  })
  results.push({ browser: name, version: browser.version(), steps, layout: sizes, pageErrors: errors })
  await browser.close()
}
const failed = results.filter(r => r.pageErrors.length || Object.values(r.steps).some(v => v !== 'pass'))
console.log(JSON.stringify({ results, failed: failed.map(r => r.browser) }, null, 2))
process.exit(failed.length ? 1 : 0)
