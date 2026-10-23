// The signed upload URL is an expiring capability: keep it out of localStorage
// and logs; never attach PaperMind cookies/identity headers to the Blob request.
export async function uploadPaperPdf(paperId, file, uid, fetcher = globalThis.fetch) {
  if (!file || file.size < 5 || file.size > 50 * 1024 * 1024) {
    throw new Error('PDF 文件不能超过 50MB。')
  }
  const identity = { 'X-User-ID': uid }
  const config = await fetcher('/api/file-storage', { headers: identity })
  const mode = config.status === 404 ? 'server' : (await checkedJson(config)).mode
  if (mode === 'server') {
    const form = new FormData()
    form.append('file', file)
    return checkedJson(await fetcher(`/api/library/${paperId}/pdf`, {
      method: 'POST', headers: identity, body: form,
    }))
  }
  if (mode !== 'direct') throw new Error('文件上传服务尚未准备好。')
  const grant = await checkedJson(await fetcher('/api/file-grant', {
    method: 'POST', headers: { ...identity, 'Content-Type': 'application/json' },
    body: JSON.stringify({ paper_id: paperId, size: file.size }),
  }))
  const url = new URL(grant.upload_url)
  // The pinned SDK signs PUTs on the Blob control endpoint, not the private
  // download hostname. Restrict to this exact official endpoint.
  if (url.origin !== 'https://vercel.com' || url.pathname !== '/api/blob/' || url.username || url.password) {
    throw new Error('文件上传地址无效。')
  }
  const uploaded = await fetcher(url.href, {
    method: 'PUT', headers: { 'Content-Type': 'application/pdf' }, body: file,
    credentials: 'omit', referrerPolicy: 'no-referrer', redirect: 'error',
  })
  if (!uploaded.ok) throw new Error('PDF 直传失败，请重试。')
  return checkedJson(await fetcher(`/api/library/${paperId}/pdf-upload/${grant.upload_id}`, {
    method: 'POST', headers: identity,
  }))
}

async function checkedJson(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.ok === false) throw new Error(typeof data.detail === 'string' ? data.detail : 'PDF 上传失败，请重试。')
  return data
}
