import { getUserId } from './api.js'

const isAccountCache = key => key.startsWith('cached-') || /^paper-(notes|chat|bookmark)-/.test(key) || key === 'last-reading'

// Preserve local drafts instead of deleting them when switching accounts.
export function switchAccountCache(identity, storage = localStorage, inheritGuest = false) {
  const prior = storage.getItem('pm-cache-owner') || `guest:${getUserId()}`
  if (prior !== identity) {
    const saved = {}
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (isAccountCache(key)) saved[key] = storage.getItem(key)
    }
    const existing = storage.getItem(`pm-account-cache:${identity}`)
    const restored = existing ? JSON.parse(existing) : inheritGuest && prior.startsWith('guest:') ? saved : {}
    if (!restored || typeof restored !== 'object' || Array.isArray(restored)) throw new Error('本地缓存格式异常，原草稿未删除。')
    // If storage is full, stop before erasing unsaved drafts.
    storage.setItem(`pm-account-cache:${prior}`, JSON.stringify(saved))
    Object.keys(saved).forEach(key => storage.removeItem(key))
    Object.entries(restored).forEach(([key, value]) => { if (isAccountCache(key) && typeof value === 'string') storage.setItem(key, value) })
  }
  storage.setItem('pm-cache-owner', identity)
}

export async function authRequest(path, body) {
  const response = await fetch(`/api/auth/${path}`, { method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(20000),
    headers: { 'Content-Type': 'application/json', 'X-User-ID': getUserId() },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (response.status === 404 && path === 'status') return { enabled: false, authenticated: false }
  const data = await response.json()
  if (!response.ok) { const error = new Error(data.detail || '登录服务暂时不可用'); error.status = response.status; throw error }
  return data
}

export function validateAuthorizeURL(url) {
  const parsed = new URL(url)
  if (parsed.origin !== 'https://www.zotero.org' || parsed.pathname !== '/oauth/authorize' || parsed.searchParams.get('identity') !== '1') {
    throw new Error('授权地址无效，未跳转。')
  }
  return parsed.href
}
