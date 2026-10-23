// Isolated grant tests: no credentials, SDK or network access.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('../deploy/vercel-preview/api/file-grant.mjs', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '').replace('export async function POST', 'async function POST')
const preview = { VERCEL_ENV: 'preview', BLOB_STORE_ID: 'store_RkRS2huB7kCab3j8', VERCEL_URL: 'preview.vercel.app' }
const production = { VERCEL_ENV: 'production', BLOB_STORE_ID: 'store_ProductionTest', VERCEL_URL: 'deployment.vercel.app',
  PAPERMIND_MIGRATION_APPROVED: 'true', PAPERMIND_FILE_BACKEND: 'vercel-blob', PAPERMIND_PUBLIC_ORIGIN: 'https://site.example' }
async function invoke(env, origin, body = { paper_id: 1, size: 123 }, path) {
  const calls = []
  const record = { id: 'a'.repeat(32), pathname: path || (body.kind === 'figure' ? `board-figure-v1/${'b'.repeat(32)}.png` : `paper-pdf-v1/${'b'.repeat(32)}.pdf`),
    mime: 'image/png', size: 123, expires: 2000000000 }
  const context = vm.createContext({ process: { env }, Response, URL, AbortSignal, console: { error() {} },
    getVercelOidcToken: async () => 'synthetic-oidc',
    fetch: async (url, options) => { calls.push({ url, options }); return Response.json(record) },
    issueSignedToken: async options => { calls.push({ signed: options }); return 'synthetic-signed-token' },
    presignUrl: async () => ({ presignedUrl: 'https://vercel.com/api/blob/synthetic' }) })
  vm.runInContext(source, context)
  const request = new Request(`https://${env.VERCEL_URL}/api/file-grant`, { method: 'POST',
    headers: { authorization: 'Bearer synthetic', ...(origin ? { origin } : {}) }, body: JSON.stringify(body) })
  return { response: await context.POST(request), calls }
}

test('preview PDF and image grants accept only server-reserved paths', async () => {
  for (const kind of ['pdf', 'figure']) {
    const result = await invoke(preview, 'https://preview.vercel.app', { kind, paper_id: 1, size: 123, mime: 'image/png' })
    assert.equal(result.response.status, 200)
    assert.equal(result.calls[0].url, `https://preview.vercel.app/api/${kind === 'pdf' ? 'library/1/pdf-upload' : 'board/1/figure-upload'}`)
    assert.equal(result.calls[1].signed.maximumSizeInBytes, 123)
  }
  assert.equal((await invoke(preview, null, undefined, '../private')).response.status, 502)
})
test('production validates public origin but contacts its own deployment', async () => {
  const result = await invoke(production, 'https://site.example')
  assert.equal(result.response.status, 200)
  assert.equal(result.calls[0].url, 'https://deployment.vercel.app/api/library/1/pdf-upload')
  assert.equal(result.calls[0].options.headers.origin, 'https://site.example')
  assert.equal((await invoke(production, 'https://preview.vercel.app')).response.status, 403)
  assert.equal((await invoke(production, null)).response.status, 200) // Native Connector bearer flow.
})
test('unapproved production and preview storage reuse fail before network', async () => {
  for (const env of [{ ...production, PAPERMIND_MIGRATION_APPROVED: 'false' },
    { ...production, BLOB_STORE_ID: preview.BLOB_STORE_ID }, { ...preview, BLOB_STORE_ID: 'store_other' },
    { ...production, PAPERMIND_PUBLIC_ORIGIN: 'https://site.example/path' }]) {
    const result = await invoke(env, null)
    assert.equal(result.response.status, 503)
    assert.equal(result.calls.length, 0)
  }
})
test('unknown kinds, IDs and foreign origins cannot create reservations', async () => {
  for (const [origin, body, status] of [['https://evil.example', { paper_id: 1 }, 403],
    [null, { paper_id: '../1' }, 400], [null, { paper_id: 1, kind: 'arbitrary' }, 400]]) {
    const result = await invoke(preview, origin, body)
    assert.equal(result.response.status, status)
    assert.equal(result.calls.length, 0)
  }
})
