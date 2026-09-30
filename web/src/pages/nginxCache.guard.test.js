import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const domainConfig = fs.readFileSync(new URL('../../../deploy/nginx-papermind.conf', import.meta.url), 'utf8')
const ipConfig = fs.readFileSync(new URL('../../../deploy/nginx-papermind-ip.conf', import.meta.url), 'utf8')
const updateScript = fs.readFileSync(new URL('../../../deploy/update.sh', import.meta.url), 'utf8')

test('keeps SPA HTML uncached while hashed assets stay immutable', () => {
  for (const config of [domainConfig, ipConfig]) {
    assert.match(config, /location \/ \{[\s\S]*Cache-Control "no-cache, no-store, must-revalidate"/)
    assert.match(config, /location \/assets\/ \{[\s\S]*Cache-Control "public, immutable"/)
  }
})

test('deployment updates live certbot-managed nginx configs without overwriting them', () => {
  assert.match(updateScript, /ensure_spa_no_cache \/etc\/nginx\/sites-available\/papermind/)
  assert.match(updateScript, /ensure_spa_no_cache \/etc\/nginx\/sites-available\/papermind-domain/)
  assert.doesNotMatch(updateScript, /cp "\$PROJECT_DIR\/deploy\/nginx-papermind\.conf" \/etc\/nginx/)
})

test('deployment publishes readable assets and checks the frontend as well as the API', () => {
  assert.match(updateScript, /chmod -R a\+rX "\$BUILD_DIR"/)
  assert.match(updateScript, /127\.0\.0\.1:8000\/ >\/dev\/null/)
  assert.ok(updateScript.indexOf('backup_data.py" create') < updateScript.indexOf('git merge --ff-only'))
})
