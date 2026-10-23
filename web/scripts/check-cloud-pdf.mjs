// Real protected-preview browser flow. Credentials are injected in memory only.
/* global process, Buffer */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { chromium } from 'playwright'

const base = process.env.PAPERMIND_TEST_URL
const paper = Number(process.env.PAPERMIND_TEST_PAPER)
if (!/^https:\/\/litfinch-migration-preview-[a-z0-9]+-lvyranas-projects\.vercel\.app$/.test(base) || !paper) throw Error('Invalid preview')

function syntheticPdf() {
  const stream = 'BT /F1 20 Tf 50 740 Td (LitFinch synthetic private PDF) Tj ET\n%' + 'x'.repeat(6 * 1024 * 1024) + '\n'
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`,
  ]
  let text = '%PDF-1.4\n', offsets = [0]
  objects.forEach((body, i) => { offsets.push(Buffer.byteLength(text)); text += `${i + 1} 0 obj\n${body}\nendobj\n` })
  const xref = Buffer.byteLength(text)
  text += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n => String(n).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(text)
}

const buffer = syntheticPdf()
const expectedHash = createHash('sha256').update(buffer).digest('hex')
const browser = await chromium.launch({ headless: true, chromiumSandbox: true,
  ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PAPERMIND_CHROMIUM_EXECUTABLE } : {}),
  ...(process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {}) })
let stage = 'open_reader'
const fileResponses = []
const failedRequests = []
let readerPage
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } })
  await context.addCookies([{ name: 'pm_session', value: process.env.PAPERMIND_TEST_SESSION, url: base, httpOnly: true, secure: true, sameSite: 'Lax' }])
  await context.addInitScript(uid => {
    localStorage.setItem('papermind-uid', uid)
    localStorage.setItem('pm-paper-tour-done', '1')
  }, process.env.PAPERMIND_TEST_UID)
  // Scope protection credentials to this deployment, never to the Blob host.
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === base) return route.continue({ headers: {
      ...route.request().headers(), 'x-vercel-trusted-oidc-idp-token': process.env.VERCEL_OIDC_TOKEN,
    } })
    return route.continue()
  })
  const page = await context.newPage()
  readerPage = page
  page.on('requestfailed', request => {
    const parsed = new URL(request.url())
    // Never include query strings: signed upload URLs contain credentials.
    failedRequests.push({ path: parsed.origin + parsed.pathname, error: request.failure()?.errorText })
  })
  page.on('response', response => {
    const parsed = new URL(response.url())
    if (parsed.origin === base && /file-grant|pdf-upload|\/pdf$/.test(parsed.pathname)) {
      fileResponses.push({ path: parsed.pathname, status: response.status() })
    }
  })
  await page.goto(`${base}/paper/${paper}?library=1`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.locator('input[type="file"]').first().waitFor({ state: 'attached', timeout: 30000 })
  stage = 'browser_direct_upload'
  const completion = page.waitForResponse(response => new URL(response.url()).pathname.startsWith(`/api/library/${paper}/pdf-upload/`) && response.request().method() === 'POST', { timeout: 90000 })
  completion.catch(() => {})
  const granted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/file-grant', { timeout: 30000 })
  await page.locator('input[type="file"]').first().setInputFiles({ name: 'synthetic-private-6mb.pdf', mimeType: 'application/pdf', buffer })
  const grantResponse = await granted
  if (grantResponse.status() !== 200) {
    const failure = await grantResponse.json().catch(() => ({}))
    fileResponses.push({ stage: failure.stage || 'unknown', status: grantResponse.status() })
    throw Error('Grant failed')
  }
  const response = await completion
  assert.equal(response.status(), 200)
  assert.equal((await response.json()).sha256, expectedHash)
  stage = 'render_uploaded_pdf'
  await page.locator('canvas').first().waitFor({ state: 'visible', timeout: 60000 })
  stage = 'reopen_pdf'
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.locator('canvas').first().waitFor({ state: 'visible', timeout: 60000 })
  stage = 'download_hash_and_isolation'
  // Fetch uses the real current session and reader endpoint, not a fixture API.
  const result = await page.evaluate(async ({ paper, expectedHash }) => {
    const response = await fetch(`/api/library/${paper}/pdf`)
    const data = await response.arrayBuffer()
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), n => n.toString(16).padStart(2, '0')).join('')
    return { status: response.status, size: data.byteLength, hash_matches: hash === expectedHash }
  }, { paper, expectedHash })
  assert.equal(result.status, 200)
  assert.equal(result.size, buffer.length)
  assert.equal(result.hash_matches, true)
  const privateData = await page.evaluate(async paper => (await fetch(`/api/library/${paper}/pdf-upload`)).json(), paper)
  const anonymous = await fetch(`https://${process.env.BLOB_STORE_ID.replace(/^store_/, '').toLowerCase()}.private.blob.vercel-storage.com/${privateData.pathname}`)
  assert.equal(anonymous.status, 403)
  await context.addCookies([{ name: 'pm_session', value: process.env.PAPERMIND_TEST_OTHER_SESSION, url: base, httpOnly: true, secure: true, sameSite: 'Lax' }])
  const denied = await page.evaluate(async paper => ({
    download: (await fetch(`/api/library/${paper}/pdf`)).status,
    upload: (await fetch('/api/file-grant', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paper_id: paper, size: 10 }) })).status,
  }), paper)
  assert.equal(denied.download, 404)
  assert.equal(denied.upload, 404)
  console.log(JSON.stringify({ ok: true, paper_id: paper, bytes: buffer.length, sha256: expectedHash,
    checks: ['browser_direct_upload_over_4_5mb', 'pdf_canvas_rendered', 'reload_reads_cloud_pdf', 'stream_download_hash', 'anonymous_blob_denied', 'other_account_read_write_denied'] }))
} catch (error) {
  const current = readerPage ? new URL(readerPage.url()) : null
  console.log(JSON.stringify({ ok: false, stage, error_type: error.name,
    page: current ? current.origin + current.pathname : null,
    fileResponses, failedRequests: failedRequests.slice(-10) }))
  process.exitCode = 1
} finally {
  await browser.close()
}
