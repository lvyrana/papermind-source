// Public entry acceptance uses the built frontend and a local static server.
// No model, real account, existing user data or backend is used.
/* global process */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('../dist/', import.meta.url))
const output = await mkdtemp(path.join(tmpdir(), 'litfinch-public-entry-'))
console.log(`Public entry screenshots: ${output}`)
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://fixture.test').pathname
  const relative = pathname.startsWith('/assets/') || pathname.endsWith('.svg') ? pathname.slice(1) : 'index.html'
  const file = path.resolve(root, relative)
  if (!file.startsWith(root)) { res.writeHead(404).end(); return }
  try {
    const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html'
    res.writeHead(200, { 'Content-Type': type }); res.end(await readFile(file))
  } catch { res.writeHead(404).end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const base = `http://127.0.0.1:${server.address().port}`
let browser
async function assertWorkspacePalette(page) {
  const palette = await page.locator('.lf-public').evaluate(element => {
    const style = getComputedStyle(element)
    return { color: style.color, background: style.backgroundColor }
  })
  assert.deepEqual(palette, { color: 'rgb(30, 58, 95)', background: 'rgb(247, 240, 232)' },
    'Public pages must render the existing navy/cream workspace palette')
}
try {
  browser = await chromium.launch({ headless: true, chromiumSandbox: true,
    ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PAPERMIND_CHROMIUM_EXECUTABLE } : {}) })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
  const page = await context.newPage(), errors = [], apiRequests = []
  let allowAccount = false, failAccount = false
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) return route.fulfill({ status: 200, body: '' })
    if (!url.pathname.startsWith('/api/')) return route.continue()
    apiRequests.push(url.pathname)
    if (!allowAccount) return route.abort()
    if (failAccount) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '测试：登录服务暂时不可用' }) })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ enabled: false, authenticated: false, account: null }) })
  })

  await page.goto(base + '/welcome')
  await page.getByRole('heading', { name: /把一篇论文/ }).waitFor()
  await assertWorkspacePalette(page)
  assert.equal(await page.evaluate(() => localStorage.getItem('papermind-uid')), null)
  assert.deepEqual(apiRequests, [])
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.reload()
    await page.getByRole('heading', { name: /把一篇论文/ }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `welcome overflow at ${width}`)
    const cta = page.getByRole('link', { name: '免费试读一篇', exact: true })
    const box = await cta.boundingBox()
    assert.ok(box && box.x >= 0 && box.x + box.width <= width)
    await page.screenshot({ path: path.join(output, `welcome-${width}.png`), fullPage: width < 500, animations: 'disabled' })
  }
  await page.getByRole('link', { name: '免费试读一篇', exact: true }).click()
  await page.getByRole('heading', { name: '跟着三个问题，读深一篇。' }).waitFor()
  await assertWorkspacePalette(page)
  await page.evaluate(() => localStorage.setItem('paper-notes-existing', 'preserve this draft'))
  for (const name of ['看清设计', '读准数字', '追问边界']) {
    await page.getByRole('button', { name: new RegExp(name) }).click()
    await page.getByRole('button', { name: '回到原文核对', exact: true }).click()
    assert.equal(await page.locator('blockquote').evaluate(el => el === document.activeElement), true)
    assert.match(await page.locator('blockquote').getAttribute('class'), /lf-source-highlight/)
    const question = page.locator('.lf-prompt-box button').first()
    await question.click()
    assert.equal(await question.getAttribute('aria-expanded'), 'true')
    assert.ok((await page.locator('.lf-prompt-answer').innerText()).length > 20)
    await page.getByRole('button', { name: '保存这张样例卡片', exact: true }).click()
    await page.getByRole('button', { name: '已存入本机样例卡片', exact: true }).click()
  }
  assert.equal(await page.locator('.lf-saved-cards > button').count(), 3)
  await page.reload()
  assert.equal(await page.locator('.lf-saved-cards > button').count(), 3)
  assert.equal(await page.evaluate(() => localStorage.getItem('papermind-uid')), null)
  assert.equal(await page.evaluate(() => localStorage.getItem('paper-notes-existing')), 'preserve this draft')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 Markdown' }).click()
  const download = await downloadPromise
  const markdown = await readFile(await download.path(), 'utf8')
  assert.match(markdown, /10.1371\/journal.pone.0266052/)
  assert.match(markdown, /CC BY 4.0/)
  assert.equal((markdown.match(/^## /gm) || []).length, 3)
  assert.match(markdown, /10 个百分点/)
  await download.saveAs(path.join(output, download.suggestedFilename()))
  await page.goto(base + '/demo?section=result')
  await page.getByRole('button', { name: '回到原文核对', exact: true }).click()
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.reload()
    await page.getByRole('button', { name: '回到原文核对', exact: true }).click()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `demo overflow at ${width}`)
    await page.screenshot({ path: path.join(output, `demo-${width}.png`), fullPage: width < 500, animations: 'disabled' })
  }
  await page.getByRole('button', { name: '清空样例' }).click()
  assert.equal(await page.locator('.lf-saved-cards > button').count(), 0)
  assert.equal(await page.evaluate(() => localStorage.getItem('paper-notes-existing')), 'preserve this draft')
  assert.deepEqual(apiRequests, [])
  await page.goto(base + '/demo?section=unknown')
  await page.getByRole('heading', { name: '前瞻性收集的数据，为什么是回顾性研究？' }).waitFor()
  await page.evaluate(() => localStorage.setItem('litfinch-demo-cards-v1', '{bad json'))
  await page.reload()
  await page.getByRole('button', { name: '保存这张样例卡片' }).waitFor()
  assert.equal(await page.locator('.lf-saved-cards > button').count(), 0)

  // Storage denial is a usable session-only fallback with an honest notice.
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException('blocked', 'SecurityError') }
    Storage.prototype.setItem = () => { throw new DOMException('blocked', 'SecurityError') }
  })
  await page.reload()
  await page.getByRole('button', { name: '保存这张样例卡片' }).click()
  await page.getByText('本地存储不可用，建议下载卡片。', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '下载 Markdown' }).isEnabled(), true)
  assert.deepEqual(apiRequests, [])
  assert.deepEqual(errors, [])
  await page.close()

  // Existing account UI is reused, with both disabled and unavailable states.
  const login = await context.newPage()
  login.on('pageerror', error => errors.push(error.message))
  allowAccount = true
  await login.goto(base + '/login')
  await login.getByRole('heading', { name: '研雀账号', exact: true }).waitFor()
  await assertWorkspacePalette(login)
  assert.equal(await login.getByRole('button', { name: '使用 Zotero 账号继续' }).isDisabled(), true)
  await login.screenshot({ path: path.join(output, 'login-1440.png'), fullPage: true })
  failAccount = true
  await login.reload()
  await login.getByText('测试：登录服务暂时不可用', { exact: true }).waitFor()
  await login.getByRole('link', { name: '免费试读，无需登录' }).click()
  await login.getByRole('heading', { name: '跟着三个问题，读深一篇。' }).waitFor()
  assert.deepEqual(errors, [])
  console.log(`Public entry passed: 4 viewport widths, 3 guided questions, source focus/highlight, presets, deduplicated local cards, reload, export, clear, corrupt/blocked storage, no public API/identity writes, login fallback. Screenshots: ${output}`)
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
}
