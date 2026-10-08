import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { SourceMapConsumer, SourceNode } = require('source-map-js')
const lock = JSON.parse(fs.readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'))
const flatMap = { version: 3, sources: ['synthetic.js'], sourcesContent: ['x'], names: [], mappings: 'AAAA' }
const sectionMap = line => ({ version: 3, sections: [{ offset: { line, column: 0 }, map: flatMap }] })

test('source-map-js lock excludes the affected indexed-map versions', () => {
  const entries = Object.entries(lock.packages).filter(([name]) => name.endsWith('/source-map-js'))
  assert.ok(entries.length > 0)
  for (const [name, entry] of entries) {
    const [major, minor, patch] = entry.version.split('.').map(Number)
    assert.ok(major > 1 || major === 1 && (minor > 2 || minor === 2 && patch >= 2),
      `${name}: GHSA-68fv-2mgg-jv7q requires source-map-js 1.2.2 or later`)
  }
})

test('installed source-map-js rejects excessive and invalid section offsets before serialization', () => {
  for (const line of [10000001, -1, 1.5, Infinity, '1']) {
    assert.throws(() => new SourceMapConsumer(sectionMap(line)), /Section offset/)
  }
  const nested = { version: 3, sections: [{ offset: { line: 6000000, column: 0 }, map: sectionMap(5000000) }] }
  assert.throws(() => new SourceMapConsumer(nested), /including offsets of nested sections/)
})

test('ordinary indexed source maps still preserve positions and generated text', () => {
  const consumer = new SourceMapConsumer(sectionMap(2))
  assert.deepEqual(consumer.originalPositionFor({ line: 3, column: 1 }),
    { source: 'synthetic.js', line: 1, column: 0, name: null })
  assert.equal(SourceNode.fromStringWithSourceMap('header\n\nx', consumer).toString(), 'header\n\nx')
})
