import test from 'node:test'
import assert from 'node:assert/strict'
import { uploadPaperPdf } from './uploadPaperPdf.js'

const pdf = new File(['%PDF-1.7 test'], 'test.pdf', { type: 'application/pdf' })
const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

test('direct uploads omit app credentials and only complete after PUT', async () => {
  const calls = []
  const replies = [json({ mode: 'direct' }), json({ upload_id: 'abc', upload_url: 'https://vercel.com/api/blob/?pathname=test.pdf&signature=test' }), new Response('', { status: 200 }), json({ ok: true })]
  const result = await uploadPaperPdf(1, pdf, 'uid', async (...args) => { calls.push(args); return replies.shift() })
  assert.equal(result.ok, true)
  assert.equal(calls[2][1].credentials, 'omit')
  assert.deepEqual(calls[2][1].headers, { 'Content-Type': 'application/pdf' })
  assert.equal(calls[3][0], '/api/library/1/pdf-upload/abc')
})

test('old server stays on its original multipart path', async () => {
  const calls = []
  const replies = [json({ mode: 'server' }), json({ ok: true })]
  await uploadPaperPdf(1, pdf, 'uid', async (...args) => { calls.push(args); return replies.shift() })
  assert.equal(calls[1][0], '/api/library/1/pdf')
  assert.ok(calls[1][1].body instanceof FormData)
})

test('failed PUT never marks a PDF complete', async () => {
  let calls = 0
  const replies = [json({ mode: 'direct' }), json({ upload_id: 'abc', upload_url: 'https://vercel.com/api/blob/?pathname=test.pdf' }), new Response('', { status: 403 })]
  await assert.rejects(uploadPaperPdf(1, pdf, 'uid', async () => { calls++; return replies.shift() }), /直传失败/)
  assert.equal(calls, 3)
})

test('reject oversized files before network and never fall back on cloud failures', async () => {
  await assert.rejects(uploadPaperPdf(1, { size: 51 * 1024 * 1024 }, 'uid', () => { throw Error('network') }), /50MB/)
  await assert.rejects(uploadPaperPdf(1, pdf, 'uid', async () => json({}, 503)), /失败/)
})
