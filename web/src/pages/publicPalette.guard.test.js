import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const css = read('../styles/public.css')

test('welcome omits the removed desktop and phone tagline', () => {
  assert.ok(!read('./Welcome.jsx').includes('适合电脑精读，也方便在手机上先试一篇。'))
})

test('public pages inherit the workspace cream, navy, coral and mint palette', () => {
  for (const token of ['cream', 'cream-dark', 'navy', 'navy-light', 'coral', 'coral-light', 'coral-deep', 'mint', 'mint-deep', 'warm-white']) {
    assert.ok(css.includes(`var(--color-${token})`), `Use the shared ${token} token`)
  }
  assert.match(css, /--lf-ink:\s*var\(--color-navy\)/)
  assert.match(css, /--lf-paper:\s*var\(--color-warm-white\)/)
  assert.match(css, /background-color:\s*var\(--color-cream\)/)
})

test('public styles derive colors from shared tokens instead of a separate literal palette', () => {
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i)
  assert.doesNotMatch(css, /--lf-rust/)
})

test('welcome, demo and login share the same public palette', () => {
  assert.match(read('../components/PublicLayout.jsx'), /import ['"]\.\.\/styles\/public\.css['"]/)
  for (const page of ['Welcome', 'Demo', 'Login']) {
    assert.match(read(`./${page}.jsx`), /<PublicLayout\b/, `${page} must use PublicLayout`)
  }
})
