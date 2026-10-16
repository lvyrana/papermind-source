// Synthetic browser acceptance: no external provider, model, or real user data.
/* global process */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('../dist/', import.meta.url))
const screenshotDir = await mkdtemp(path.join(tmpdir(), 'litfinch-accounts-'))
console.log(`Synthetic account screenshots: ${screenshotDir}`)
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://fixture.test').pathname
  const relative = pathname.startsWith('/assets/') || ['/icon-light.svg', '/icon-192.svg', '/favicon.svg'].includes(pathname)
    ? pathname.slice(1) : 'index.html'
  const file = path.resolve(root, relative)
  if (!file.startsWith(root)) { res.writeHead(404).end(); return }
  try {
    const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html'
    res.writeHead(200, { 'Content-Type': type }); res.end(await readFile(file))
  } catch { res.writeHead(404).end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const base = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ headless: true, chromiumSandbox: true,
  ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PAPERMIND_CHROMIUM_EXECUTABLE } : {}) })
try {
  const context = await browser.newContext({ viewport: { width: 1100, height: 820 } })
  const page = await context.newPage(), errors = [], starts = [], usageRequests = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  const uid = '11111111-1111-4111-8111-111111111111'
  let authenticated = false, enabled = true
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== base) {
      if (url.origin === 'https://www.zotero.org' && url.pathname === '/oauth/authorize') {
        assert.equal(url.searchParams.get('identity'), '1')
        authenticated = true
        return route.fulfill({ status: 302, headers: { location: base + '/settings?auth=success' }, body: '' })
      }
      return route.fulfill({ status: 204, body: '' })
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    let data = {}
    switch (url.pathname) {
      case '/api/auth/status': data = { enabled, authenticated, account: authenticated ? { id: 'fixture-account', data_uid: uid, username: '测试阅读者' } : null }; break
      case '/api/auth/zotero/start': starts.push(route.request().postDataJSON()); data = { authorize_url: 'https://www.zotero.org/oauth/authorize?identity=1&oauth_token=synthetic' }; break
      case '/api/auth/logout': authenticated = false; data = { ok: true }; break
      case '/api/profile': data = { background: '临床护理', focus_areas: '康复' }; break
      case '/api/settings': data = { can_manage: false }; break
      case '/api/library': data = { papers: [] }; break
      case '/api/usage': usageRequests.push(url.pathname); data = { chat: { used: 0, limit: 60 }, translate: { used: 0, limit: 120 }, recommend: { used: 0, limit: 5 } }; break
      case '/api/stats': data = { papers: 0, notes: 0, chats: 0 }; break
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(data), headers: { 'Cache-Control': 'no-store' } })
  })
  await page.goto(base + '/settings')
  await page.getByRole('heading', { name: '研雀账号', exact: true }).waitFor()
  const checkHiddenAIStatus = async () => {
    assert.equal(await page.getByText('AI 服务', { exact: true }).count(), 0)
    assert.equal(await page.getByText(/支持论文解读、翻译和对话|每人每天最多/).count(), 0)
    assert.equal(await page.getByRole('heading', { name: '数据导出', exact: true }).isVisible(), true)
  }
  await checkHiddenAIStatus()
  assert.equal(await page.getByText(/关联后旧专属链接不再|只验证身份，不申请/).count(), 0)
  assert.equal(await page.getByRole('checkbox', { name: /首次创建账号时/ }).isChecked(), true)
  assert.equal(await page.title(), '研雀 · LitFinch')
  const brandLink = page.getByRole('link', { name: '研雀 · LitFinch 首页', exact: true })
  await brandLink.locator('img').evaluate(image => image.decode())
  for (const width of [390, 768, 1100]) {
    await page.setViewportSize({ width, height: 820 })
    assert.equal(await brandLink.isVisible(), true)
    assert.equal(await page.locator('nav').evaluate(nav => nav.scrollWidth <= nav.clientWidth), true)
    for (const name of ['首页', '书架', '设置']) {
      const box = await page.getByRole('link', { name, exact: true }).boundingBox()
      assert.ok(box && box.x >= 0 && box.x + box.width <= width)
    }
    await page.screenshot({ path: path.join(screenshotDir, `litfinch-brand-${width}.png`), fullPage: false, animations: 'disabled' })
  }
  await page.evaluate(uid => {
    localStorage.setItem('papermind-uid', uid)
    localStorage.setItem('pm-cache-owner', 'guest:' + uid)
    localStorage.setItem('paper-notes-77', '未保存的原始草稿')
  }, uid)
  await page.reload()
  await page.getByRole('button', { name: '使用 Zotero 账号继续', exact: true }).click().catch(async error => {
    console.log({ errors, screen: await page.locator('body').innerText() }); throw error
  })
  await page.waitForURL(base + '/settings?auth=success')
  await page.getByText('已登录：测试阅读者', { exact: true }).waitFor()
  await checkHiddenAIStatus()
  assert.equal(starts[0].adopt_legacy, true)
  assert.equal(await page.evaluate(() => localStorage.getItem('paper-notes-77')), '未保存的原始草稿')
  await page.screenshot({ path: path.join(screenshotDir, 'litfinch-account-settings.png'), fullPage: false })
  for (const result of ['legacy-preserved', 'cancelled']) {
    await page.goto(base + '/settings?auth=' + result)
    await page.getByText('已登录：测试阅读者', { exact: true }).waitFor()
    assert.equal(await page.getByText(/另一份旧设备记录|原专属链接仍用于|登录未完成，请重试/).count(), 0)
  }
  await page.getByRole('button', { name: '退出账号', exact: true }).click()
  await page.waitForURL(base + '/settings')
  await page.getByRole('button', { name: '使用 Zotero 账号继续', exact: true }).waitFor()
  assert.equal(await page.evaluate(() => localStorage.getItem('paper-notes-77')), null)
  assert.notEqual(await page.evaluate(() => localStorage.getItem('papermind-uid')), uid)
  await page.goto(base + '/settings?auth=cancelled')
  await page.getByText('登录未完成，请重试。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '使用 Zotero 账号继续', exact: true }).click()
  await page.waitForURL(base + '/settings?auth=success')
  await page.getByText('已登录：测试阅读者', { exact: true }).waitFor()
  assert.equal(await page.evaluate(() => localStorage.getItem('paper-notes-77')), '未保存的原始草稿')
  await page.goto(base + '/settings?device=ABCDEF123456')
  await page.getByRole('heading', { name: '连接 Zotero 插件' }).waitFor()
  await page.getByText('ABCDEF123456', { exact: true }).waitFor()
  await page.getByRole('button', { name: '使用 Zotero 账号继续', exact: true }).click()
  // This page already shows the logged-in label before confirmation. Wait for
  // the actual callback document, not that stale label, before reloading it.
  await page.waitForURL(base + '/settings?auth=success')
  await page.getByText('已登录：测试阅读者', { exact: true }).waitFor()
  assert.equal(starts.at(-1).device_code, 'ABCDEF123456')
  authenticated = false; enabled = false
  await page.reload()
  assert.equal(await page.getByRole('button', { name: '使用 Zotero 账号继续', exact: true }).isDisabled(), true)
  assert.deepEqual(errors, [])
  assert.deepEqual(usageRequests, [])
  console.log('Browser account checks passed: login, explicit legacy association, logout/cache isolation, return login, plugin confirmation, unconfigured service. No external calls.')
} finally {
  await browser.close()
  await new Promise(resolve => server.close(resolve))
}
