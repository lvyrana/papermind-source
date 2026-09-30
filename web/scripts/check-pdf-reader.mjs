// Browser regression against the actual PdfViewer, using only generated PDF data.
// No backend, real library, external PDF, or model API is involved.
import assert from 'node:assert/strict'
import { createServer as createHttpServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const root = fileURLToPath(new URL('../', import.meta.url))
const requests = []

function createPdf(pages = 43) {
  const objects = []
  const add = value => { objects.push(value); return objects.length }
  add('') // catalog
  add('') // page tree
  const font = add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
  // Harmless marker. Its presence tests the reader's no-scripting boundary,
  // not a complete reproduction of any PDF.js vulnerability.
  const action = add('<< /S /JavaScript /JS (globalThis.__pdfActionExecuted = true;) >>')
  const pageIds = []
  for (let number = 1; number <= pages; number++) {
    const text = `BT /F1 18 Tf 40 740 Td (PaperMind synthetic page ${number}) Tj ET\n0.2 0.4 0.6 rg 40 600 180 60 re f\n`
    const stream = add(`<< /Length ${Buffer.byteLength(text)} >>\nstream\n${text}endstream`)
    pageIds.push(add(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${stream} 0 R /AA << /O ${action} 0 R >> >>`))
  }
  objects[0] = `<< /Type /Catalog /Pages 2 0 R /OpenAction ${action} 0 R >>`
  objects[1] = `<< /Type /Pages /Count ${pages} /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] >>`
  let output = '%PDF-1.7\n'
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(output))
    output += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xref = Buffer.byteLength(output)
  output += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  output += offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  output += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(output)
}

const pdf = createPdf()
const shortPdf = createPdf(3)
const fixtureHtml = `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0"><div id="root" style="height:100vh"></div>
<script type="module">
import React from 'react'
import { createRoot } from 'react-dom/client'
import PdfViewer from '/src/components/PdfViewer.jsx'
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs'
import '/src/index.css'
const state = window.__reader = { ref: React.createRef(), selection: null, texts: {}, errors: [] }
state.pdfjs = pdfjs
const onSelection = value => { state.selection = value }
const onTextReady = (page, content) => { state.texts[page] = content.items.map(item => item.str || '').join(' ') }
const initialUrl = new URLSearchParams(location.search).get('pdf') || '/__fixture.pdf'
function Harness() {
  const [highlights, setHighlights] = React.useState([])
  const [url, setUrl] = React.useState(initialUrl)
  state.setHighlights = setHighlights
  state.setUrl = setUrl
  return React.createElement(PdfViewer, {
    ref: state.ref, url, originalUrl: url, onSelection, onTextReady, highlights,
  })
}
let root
state.mount = () => {
  root = createRoot(document.getElementById('root'))
  root.render(React.createElement(Harness))
}
state.unmount = () => root.unmount()
state.mount()
</script></body></html>`

let vite, browser
const crossOrigin = createHttpServer((request, response) => {
  requests.push({ kind: 'cross-origin', uid: request.headers['x-user-id'], cookie: request.headers.cookie })
  response.writeHead(200, { 'Content-Type': 'application/pdf',
    'Access-Control-Allow-Origin': request.headers.origin || '*',
    'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'X-User-ID' })
  response.end(pdf)
})

try {
  await new Promise(resolve => crossOrigin.listen(0, '127.0.0.1', resolve))
  const externalPdf = `http://127.0.0.1:${crossOrigin.address().port}/fixture.pdf`
  vite = await createServer({
    root,
    envDir: false,
    server: { host: '127.0.0.1', port: 0, open: false },
    plugins: [{
      name: 'local-reader-fixture',
      configureServer(server) {
        server.middlewares.use(async (request, response, next) => {
          const path = request.url?.split('?')[0]
          if (path === '/__reader_smoke') {
            response.setHeader('Content-Type', 'text/html')
            response.end(await server.transformIndexHtml(request.url, fixtureHtml))
          } else if (path === '/__fixture.pdf') {
            requests.push({ kind: 'same-origin', uid: request.headers['x-user-id'] })
            response.setHeader('Content-Type', 'application/pdf')
            response.end(pdf)
          } else if (path === '/__broken.pdf') {
            response.setHeader('Content-Type', 'application/pdf')
            response.end('not a PDF')
          } else if (path === '/__redirect.pdf') {
            response.writeHead(302, { Location: externalPdf })
            response.end()
          } else if (path === '/__short.pdf') {
            response.setHeader('Content-Type', 'application/pdf')
            response.end(shortPdf)
          } else if (path === '/__slow.pdf') {
            requests.push({ kind: 'pending-load' })
            const timer = setTimeout(() => {
              response.setHeader('Content-Type', 'application/pdf')
              response.end(pdf)
            }, 1500)
            response.on('close', () => clearTimeout(timer))
          } else next()
        })
      },
    }],
  })
  await vite.listen()
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`
  browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PAPERMIND_CHROMIUM_EXECUTABLE } : {}),
  })
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    const isFixtureOrigin = url.origin === origin || url.origin === new URL(externalPdf).origin
    return isFixtureOrigin && !url.pathname.startsWith('/api') ? route.continue() : route.abort()
  })
  // Simulate the missing built-in that previously broke older trial browsers.
  await context.addInitScript(() => {
    delete Map.prototype.getOrInsertComputed
    window.__legacyMissingBeforeLoad = typeof Map.prototype.getOrInsertComputed === 'undefined'
  })
  const page = await context.newPage()
  const errors = [], sandboxRequests = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (/pdf\.sandbox|scripting\.mjs/.test(request.url())) sandboxRequests.push(request.url())
  })
  page.setDefaultTimeout(30_000)

  async function openReader(url) {
    await page.goto(`${origin}/__reader_smoke?pdf=${encodeURIComponent(url)}`)
    await page.waitForFunction(() => window.__reader?.ref.current?.getNumPages() === 43)
    await page.waitForFunction(() => document.querySelectorAll('.pdf-page-wrap').length === 43)
    await page.waitForFunction(() => window.__reader.texts[1]?.includes('synthetic page 1'))
    assert.equal(await page.evaluate(() => window.__reader.pdfjs.version), '6.2.108')
    assert.equal(await page.evaluate(() => window.__legacyMissingBeforeLoad), true)
    assert.equal(await page.evaluate(() => typeof Map.prototype.getOrInsertComputed), 'function')
  }

  await openReader('/__fixture.pdf')
  const initial = await page.evaluate(() => {
    const wrap = document.querySelector('.pdf-page-wrap[data-page-num="1"]')
    const canvas = wrap.querySelector('canvas')
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    let dark = 0
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] && pixels[i] < 200) dark++
    return { pages: document.querySelectorAll('.pdf-page-wrap').length,
      canvases: document.querySelectorAll('.pdf-page-wrap canvas').length,
      dark, width: canvas.width }
  })
  assert.equal(initial.pages, 43)
  assert.ok(initial.canvases <= 5)
  assert.ok(initial.dark > 100)

  const selection = await page.evaluate(() => {
    const span = document.querySelector('.pdf-page-wrap[data-page-num="1"] .textLayer span')
    const range = document.createRange()
    range.selectNodeContents(span)
    const selection = window.getSelection()
    selection.removeAllRanges()
    selection.addRange(range)
    document.dispatchEvent(new MouseEvent('mouseup'))
    return window.__reader.selection
  })
  assert.match(selection.text, /synthetic page 1/)
  assert.equal(selection.page, 1)
  assert.ok(selection.anchor.rects.length > 0)
  assert.equal(selection.needsOcr, false)

  await page.evaluate(() => window.__reader.ref.current.setScale(2))
  await page.waitForFunction(width => {
    const canvas = document.querySelector('.pdf-page-wrap[data-page-num="1"] canvas')
    return canvas && canvas.width > width && document.querySelector('.textLayer span')
  }, initial.width)
  const geometry = await page.evaluate(() => {
    const wrap = document.querySelector('.pdf-page-wrap[data-page-num="1"]')
    const pageRect = wrap.getBoundingClientRect()
    const textRect = wrap.querySelector('.textLayer span').getBoundingClientRect()
    return { width: textRect.width, height: textRect.height,
      inside: textRect.left >= pageRect.left && textRect.right <= pageRect.right
        && textRect.top >= pageRect.top && textRect.bottom <= pageRect.bottom }
  })
  assert.ok(geometry.width > 20 && geometry.height > 5 && geometry.inside)
  await page.evaluate(selection => window.__reader.setHighlights([{
    id: 'fixture-quote', page: selection.page, text: selection.text, anchor: selection.anchor,
  }]), selection)
  await page.locator('[data-quote-highlight="fixture-quote"]').waitFor()
  const highlightFitsText = await page.evaluate(() => {
    const marker = document.querySelector('[data-quote-highlight="fixture-quote"]').getBoundingClientRect()
    const text = document.querySelector('.pdf-page-wrap[data-page-num="1"] .textLayer span').getBoundingClientRect()
    return Math.abs(marker.left - text.left) < 2 && Math.abs(marker.width - text.width) < 2
      && marker.top < text.bottom && marker.bottom > text.top
  })
  assert.ok(highlightFitsText)
  assert.match(await page.evaluate(() => window.__reader.ref.current.capturePageImage(1)), /^data:image\/jpeg;base64,/)
  if (process.env.PAPERMIND_PDF_SCREENSHOT) {
    await page.screenshot({ path: process.env.PAPERMIND_PDF_SCREENSHOT })
  }

  for (const target of [21, 43, 1]) {
    await page.evaluate(number => window.__reader.ref.current.goToPage(number), target)
    await page.waitForFunction(number => {
      const canvases = document.querySelectorAll('.pdf-page-wrap canvas')
      return window.__reader.ref.current.getCurrentPage() === number
        && window.__reader.texts[number]?.includes(`synthetic page ${number}`)
        && document.querySelector(`.pdf-page-wrap[data-page-num="${number}"] .textLayer span`)
        && canvases.length <= 5
    }, target)
  }
  await page.evaluate(() => window.__reader.ref.current.highlightQuote('fixture-quote'))
  await page.locator('[data-quote-highlight="fixture-quote"]').waitFor()
  assert.equal(await page.evaluate(() => window.__pdfActionExecuted), undefined)

  // Ensure the fixture really carries JavaScript actions, without executing them.
  const actions = await page.evaluate(async () => {
    const data = new Uint8Array(await (await fetch('/__fixture.pdf')).arrayBuffer())
    const task = window.__reader.pdfjs.getDocument({ data })
    const pdf = await task.promise
    const actions = await pdf.getJSActions()
    await task.destroy()
    return actions
  })
  assert.match(JSON.stringify(actions), /__pdfActionExecuted/)

  // Keep the same React mount: full page navigation would miss effect cleanup.
  await page.evaluate(() => window.__reader.setUrl('/__short.pdf'))
  await page.waitForFunction(() => window.__reader.ref.current?.getNumPages() === 3
    && document.querySelectorAll('.pdf-page-wrap').length === 3
    && document.querySelector('.pdf-page-wrap[data-page-num="1"] .textLayer span'))
  await page.evaluate(() => window.__reader.unmount())
  assert.equal(await page.evaluate(() => window.__reader.ref.current), null)
  await page.evaluate(() => window.__reader.mount())
  await page.waitForFunction(() => window.__reader.ref.current?.getNumPages() === 43
    && document.querySelector('.pdf-page-wrap[data-page-num="1"] .textLayer span'))
  const pendingRequest = page.waitForRequest('**/__slow.pdf')
  await page.evaluate(() => window.__reader.setUrl('/__slow.pdf'))
  await pendingRequest
  await page.evaluate(() => window.__reader.unmount())
  await page.evaluate(() => window.__reader.mount())
  await page.waitForFunction(() => window.__reader.ref.current?.getNumPages() === 43
    && document.querySelector('.pdf-page-wrap[data-page-num="1"] .textLayer span'))
  assert.deepEqual(errors, [])

  await openReader(externalPdf)
  assert.equal(await page.evaluate(() => window.__pdfActionExecuted), undefined)
  assert.ok(requests.some(request => request.kind === 'same-origin' && request.uid))
  assert.ok(requests.some(request => request.kind === 'cross-origin'))
  assert.ok(requests.filter(request => request.kind === 'cross-origin').every(request => !request.uid && !request.cookie))
  assert.deepEqual(sandboxRequests, [])
  assert.deepEqual(errors, [])

  const crossBeforeRedirect = requests.filter(request => request.kind === 'cross-origin').length
  await page.goto(`${origin}/__reader_smoke?pdf=/__redirect.pdf`)
  await page.getByText('PDF 无法直接渲染', { exact: true }).waitFor()
  assert.equal(requests.filter(request => request.kind === 'cross-origin').length, crossBeforeRedirect,
    'Authenticated PDF fetch must reject redirects before contacting an external site')
  assert.ok(await page.getByRole('link', { name: /PDF|原文/ }).count() > 0)

  await page.goto(`${origin}/__reader_smoke?pdf=/__broken.pdf`)
  await page.getByText('PDF 无法直接渲染', { exact: true }).waitFor()
  assert.ok(await page.getByRole('link', { name: /PDF|原文/ }).count() > 0)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({
    result: 'PASS', version: '6.2.108', pages: 43,
    checks: ['canvas pixels', 'text/selection anchors', 'zoom geometry', 'quote highlights', 'OCR page capture',
      'page-window release/rebuild', 'source switch/unmount/remount/pending-load abort',
      'legacy polyfill', 'same/cross-origin identity', 'authenticated redirect rejected',
      'inert PDF actions/no sandbox', 'broken-PDF recovery'],
    browser: browser.version(),
    limitation: 'Synthetic PDFs in Chromium; not Windows/WeChat or a full CVE exploit test.',
  }, null, 2))
} finally {
  await browser?.close()
  await vite?.close()
  await new Promise(resolve => crossOrigin.close(resolve))
}
