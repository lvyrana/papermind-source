import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(`../../../${path}`, import.meta.url), 'utf8')
const version = read('VERSION').trim()

test('website version agrees with both frontend package metadata locations', () => {
  assert.match(version, /^\d+\.\d+\.\d+$/)
  const pkg = JSON.parse(read('web/package.json'))
  const lock = JSON.parse(read('web/package-lock.json'))
  assert.equal(pkg.version, version)
  assert.equal(lock.version, version)
  assert.equal(lock.packages[''].version, version)
})

test('website release has its own versioned notes and public readme entry', () => {
  const notes = read(`docs/releases/v${version}.md`)
  assert.ok(notes.startsWith(`# 研雀 · LitFinch v${version}\n`))
  assert.ok(read('README.md').includes(`v${version}`))
})
