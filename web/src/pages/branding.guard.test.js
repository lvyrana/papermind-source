import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = relative => readFileSync(new URL(relative, import.meta.url), 'utf8')

test('website entry points use the accepted LitFinch brand', () => {
  assert.match(source('../../index.html'), /<title>研雀 · LitFinch<\/title>/)
  assert.match(source('../components/Navbar.jsx'), /aria-label="研雀 · LitFinch 首页"/)
  const account = source('../components/AccountGate.jsx')
  assert.match(account, />研雀 · LitFinch<\/h1>/)
  assert.match(account, /'研雀账号'/)
  assert.doesNotMatch(account, /PaperMind/)
})

test('display rename preserves existing reading identity and export conventions', () => {
  const api = source('../api.js')
  assert.match(api, /localStorage\.getItem\('papermind-uid'\)/)
  assert.match(api, /localStorage\.setItem\('papermind-uid', uid\)/)
  assert.match(api, /setCookie\('papermind-uid', uid\)/)
  assert.doesNotMatch(api, /litfinch-uid/i)
  assert.match(source('./Settings.jsx'), /papermind-notes-/)
})

test('account browser screenshots use a unique portable temporary directory', () => {
  const browserCheck = source('../../scripts/check-accounts-browser.mjs')
  assert.match(browserCheck, /import \{ tmpdir \} from 'node:os'/)
  assert.match(browserCheck, /mkdtemp\(path\.join\(tmpdir\(\), 'litfinch-accounts-'\)\)/)
  assert.match(browserCheck, /path: path\.join\(screenshotDir, `litfinch-brand-\$\{width\}\.png`\)/)
  assert.match(browserCheck, /path: path\.join\(screenshotDir, 'litfinch-account-settings\.png'\)/)
  assert.doesNotMatch(browserCheck, /\/private\/tmp/)
})

test('plugin confirmation acceptance waits for the actual callback navigation', () => {
  const browserCheck = source('../../scripts/check-accounts-browser.mjs')
  const confirmation = browserCheck.slice(browserCheck.indexOf("await page.goto(base + '/settings?device="))
  assert.ok(confirmation.startsWith("await page.goto(base + '/settings?device="))
  assert.match(confirmation, /await page\.waitForURL\(base \+ '\/settings\?auth=success'\)[\s\S]*await page\.getByText\('已登录：测试阅读者'[\s\S]*authenticated = false; enabled = false[\s\S]*await page\.reload\(\)/)
})
