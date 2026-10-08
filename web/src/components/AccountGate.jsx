import { useEffect, useState } from 'react'
import { AccountContext, useAccount } from '../accountContext'
import { authRequest, switchAccountCache, validateAuthorizeURL } from '../auth'
import { getUserId, clearUserId } from '../api'

export default function AccountGate({ children }) {
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    authRequest('status').then(data => {
      if (!alive) return
      if (!data.authenticated && localStorage.getItem('pm-cache-owner')?.startsWith('account:')) clearUserId()
      const identity = data.authenticated ? `account:${data.account.id}` : `guest:${getUserId()}`
      if (localStorage.getItem('pm-cache-owner') !== identity) {
        sessionStorage.removeItem('pm-auto-fetch')
        sessionStorage.removeItem('home-scroll-y')
      }
      switchAccountCache(identity, localStorage, data.authenticated && data.account.data_uid === getUserId())
      setState(data)
    }).catch(e => { if (alive) setError(e.message) })
    const changed = event => { if (event.key === 'pm-cache-owner') window.location.reload() }
    window.addEventListener('storage', changed)
    return () => { alive = false; window.removeEventListener('storage', changed) }
  }, [])
  if (!state) return <main className="max-w-xl mx-auto p-8 text-navy">
    <h1 className="text-xl mb-4">研雀 · LitFinch</h1>
    <p role="status">{error || '正在确认阅读空间…'}</p>
    {error && <div className="flex gap-4 mt-4">
      <button onClick={() => window.location.reload()}>重试</button>
      <button onClick={async () => {
        try { await authRequest('logout', {}); clearUserId(); window.location.assign('/settings') } catch (e) { setError(e.message) }
      }}>清除过期登录并返回设置</button>
    </div>}
  </main>
  return <AccountContext.Provider value={state}>{children}</AccountContext.Provider>
}

export function AccountCard() {
  const account = useAccount()
  const params = new URLSearchParams(window.location.search)
  const device = params.get('device') || ''
  const result = params.get('auth') || ''
  const [adopt, setAdopt] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const login = async () => {
    setBusy(true); setError('')
    try {
      const data = await authRequest('zotero/start', { adopt_legacy: adopt, device_code: device })
      window.location.assign(validateAuthorizeURL(data.authorize_url))
    } catch (e) { setError(e.message); setBusy(false) }
  }
  const logout = async () => {
    setBusy(true)
    try {
      await authRequest('logout', {})
      // A new guest identity cannot read the claimed account via old links.
      clearUserId(); switchAccountCache(`guest:${getUserId()}`)
      window.location.assign('/settings')
    } catch (e) { setError(e.message); setBusy(false) }
  }
  return <section id="account" className="bg-warm-white rounded-2xl p-5 border border-cream-dark mb-5">
    <h2 className="text-navy font-semibold">{device ? '连接 Zotero 插件' : '研雀账号'}</h2>
    <p className="text-sm text-warm-gray my-3">{account?.authenticated ? `已登录：${account.account.username}` : '使用 Zotero 账号连接网站和插件，无需另设密码。'}</p>
    {device && <p className="text-sm my-3">请核对插件显示的连接码：<strong>{device}</strong>。仅确认由当前电脑发起的连接。</p>}
    {result === 'success' && <p role="status" className="text-sm my-3">登录完成。{params.get('connected') === 'plugin' ? '可返回 Zotero，插件将自动接收登录结果。' : '已进入账号阅读空间。'}</p>}
    {result === 'cancelled' && <p role="status">授权未完成，可以重新登录。</p>}
    {result === 'legacy-preserved' && <p role="status" className="text-sm my-3">已进入现有账号。另一份旧设备记录保持原样，尚未合并；原专属链接仍用于访问未关联的旧记录。</p>}
    {(!account?.authenticated || device) && <>
      <label className="flex gap-2 items-start text-sm my-3"><input type="checkbox" checked={adopt} onChange={e => setAdopt(e.target.checked)} />首次创建账号时，关联{device ? '发起连接的插件' : '当前浏览器'}原有记录</label>
      <p className="text-xs text-warm-gray mb-3">关联后旧专属链接不再单独授权访问，其他设备需登录同一账号。只验证身份，不申请云端文库权限。</p>
      <button disabled={busy || !account?.enabled} onClick={login} className="px-4 py-2 rounded-xl bg-navy text-warm-white disabled:opacity-50">{busy ? '正在连接…' : '使用 Zotero 账号继续'}</button>
      {!account?.enabled && <p role="status" className="text-sm mt-3">登录服务尚未启用。现有试用方式和本地记录保持不变。</p>}
    </>}
    {account?.authenticated && <button disabled={busy} onClick={logout} className="ml-3 px-4 py-2 border rounded-xl">退出账号</button>}
    {error && <p role="alert" className="text-sm text-coral mt-3">{error}</p>}
  </section>
}
