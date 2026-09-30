import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const source = fs.readFileSync(new URL('../components/PdfViewer.jsx', import.meta.url), 'utf8')
const manifest = JSON.parse(fs.readFileSync(new URL('../../package.json', import.meta.url), 'utf8'))
const lock = JSON.parse(fs.readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'))

test('uses the pdf.js legacy build so older trial browsers get required polyfills', () => {
  assert.match(source, /from 'pdfjs-dist\/legacy\/build\/pdf\.mjs'/)
  assert.match(source, /from 'pdfjs-dist\/legacy\/build\/pdf\.worker\.min\.mjs\?url'/)
  assert.match(source, /import 'pdfjs-dist\/legacy\/web\/pdf_viewer\.css'/)
  assert.doesNotMatch(source, /from 'pdfjs-dist\/build\/pdf\.mjs'/)
  assert.doesNotMatch(source, /from 'pdfjs-dist\/build\/pdf\.worker\.min\.mjs\?url'/)
})

test('pins the reviewed PDF.js release consistently in manifest and lockfile', () => {
  assert.equal(manifest.dependencies['pdfjs-dist'], '6.2.108')
  assert.equal(lock.packages[''].dependencies['pdfjs-dist'], '6.2.108')
  assert.equal(lock.packages['node_modules/pdfjs-dist'].version, '6.2.108')
})

test('worker cache version follows the actual library and TextLayer uses the supported API', () => {
  assert.match(source, /workerSrc\}\?v=\$\{pdfjsLib\.version\}/)
  assert.doesNotMatch(source, /5\.7\.284/)
  assert.match(source, /new pdfjsLib\.TextLayer\(/)
  assert.doesNotMatch(source, /pdfjsLib\.renderTextLayer/)
})

test('the lightweight reader does not import a PDF scripting manager or sandbox', () => {
  assert.doesNotMatch(source, /import\s[\s\S]*?from\s*['"][^'"]*(?:pdf_viewer\.mjs|pdf\.sandbox)/)
  assert.doesNotMatch(source, /new\s+(?:pdfjsLib\.)?PDFScriptingManager/)
})

test('uses PDF.js 6 point conversion and loading-task teardown instead of removed proxy APIs', () => {
  assert.doesNotMatch(source, /\.convertToViewportRectangle\(/)
  assert.doesNotMatch(source, /(?:pdf|pdfRef\.current)\.destroy\(/)
  assert.match(source, /await task\.destroy\(\)/)
  assert.match(source, /task\?\.destroy\(\)\.catch/)
})
