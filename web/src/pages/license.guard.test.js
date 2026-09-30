import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createHash } from 'node:crypto'

const read = (path) => fs.readFileSync(new URL(`../../../${path}`, import.meta.url), 'utf8')

test('root LICENSE preserves the complete official GNU AGPL v3 text', () => {
  // https://www.gnu.org/licenses/agpl-3.0.txt, verified 2026-09-28.
  const license = read('LICENSE').replace(/\r\n/g, '\n')
  assert.equal(
    createHash('sha256').update(license).digest('hex'),
    '0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0',
    'Keep the official license verbatim; version selection belongs in the README grant.',
  )
})

test('main project grant and frontend package metadata agree on AGPL-3.0-only', () => {
  const pkg = JSON.parse(read('web/package.json'))
  const lock = JSON.parse(read('web/package-lock.json'))
  const readme = read('README.md')
  assert.equal(pkg.license, 'AGPL-3.0-only')
  assert.equal(lock.packages[''].license, pkg.license)
  assert.match(readme, /## 许可[\s\S]*主项目原创代码采用[^\n]*AGPL-3\.0-only/)
  assert.match(readme, /\[LICENSE\]\(LICENSE\)/)
})
