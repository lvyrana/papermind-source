import test from 'node:test'
import assert from 'node:assert/strict'
import { switchAccountCache, validateAuthorizeURL } from './auth.js'

function storage() {
  const map = new Map([['pm-cache-owner', 'guest:old'], ['paper-notes-1', 'unsaved draft'], ['cached-profile', 'profile'], ['theme', 'dark']])
  return { get length() { return map.size }, key: n => [...map.keys()][n],
    getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) }
}

test('account switch preserves guest drafts and does not show another account cache', () => {
  const s = storage()
  switchAccountCache('account:A', s)
  assert.equal(s.getItem('paper-notes-1'), null)
  assert.equal(s.getItem('cached-profile'), null)
  assert.equal(s.getItem('theme'), 'dark')
  s.setItem('paper-chat-2', 'Account A conversation')
  switchAccountCache('account:B', s)
  assert.equal(s.getItem('paper-chat-2'), null)
  switchAccountCache('guest:old', s)
  assert.equal(s.getItem('paper-notes-1'), 'unsaved draft')
  assert.equal(s.getItem('paper-chat-2'), null)
})

test('first legacy association carries local drafts without rewriting an existing account cache', () => {
  const s = storage()
  switchAccountCache('account:A', s, true)
  assert.equal(s.getItem('paper-notes-1'), 'unsaved draft')
  switchAccountCache('guest:old', s)
  s.setItem('paper-notes-1', 'different draft')
  switchAccountCache('account:A', s, true)
  assert.equal(s.getItem('paper-notes-1'), 'unsaved draft')
})

test('full browser storage does not erase unsaved drafts', () => {
  const s = storage()
  s.setItem = () => { throw Error('quota') }
  assert.throws(() => switchAccountCache('account:A', s), /quota/)
  assert.equal(s.getItem('paper-notes-1'), 'unsaved draft')
})

test('authorization navigation stays on Zotero identity-only endpoint', () => {
  assert.match(validateAuthorizeURL('https://www.zotero.org/oauth/authorize?identity=1&oauth_token=test'), /zotero/)
  for (const url of ['https://evil.test/oauth/authorize?identity=1', 'javascript:alert(1)', 'https://www.zotero.org/oauth/authorize']) {
    assert.throws(() => validateAuthorizeURL(url))
  }
})

test('corrupt target cache does not erase the active draft', () => {
  const s = storage()
  s.setItem('pm-account-cache:account:A', '{corrupt')
  assert.throws(() => switchAccountCache('account:A', s))
  assert.equal(s.getItem('paper-notes-1'), 'unsaved draft')
})
