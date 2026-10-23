// Like PDF upload, the signed URL stays in memory and receives no app identity.
export async function uploadBoardFigure(paperId, file, uid, metadata, signal, fetcher = globalThis.fetch) {
  if (!file || file.size < 8 || file.size > 10 * 1024 * 1024 || !['image/png','image/jpeg'].includes(file.type)) {
    throw new Error('仅支持不超过 10MB 的 PNG/JPEG。')
  }
  const headers = { 'X-User-ID': uid }
  const config = await fetcher('/api/file-storage', {headers,signal})
  const mode = config.status === 404 ? 'server' : (await checked(config)).mode
  if (mode === 'server') {
    const body = new FormData()
    body.append('file', file, file.type === 'image/png' ? 'figure.png' : 'figure.jpg')
    body.append('section', metadata.section)
    if (metadata.page) body.append('page', String(metadata.page))
    if (metadata.caption) body.append('caption', metadata.caption)
    return checked(await fetcher(`/api/board/${paperId}/figures`, {method:'POST',headers,body,signal}))
  }
  if (mode !== 'direct') throw new Error('图表上传服务尚未准备好。')
  const grant = await checked(await fetcher('/api/file-grant', {method:'POST',headers:{...headers,'Content-Type':'application/json'},signal,
    body:JSON.stringify({kind:'figure',paper_id:paperId,size:file.size,mime:file.type,...metadata})}))
  const url = new URL(grant.upload_url)
  if (url.origin !== 'https://vercel.com' || url.pathname !== '/api/blob/' || url.username || url.password) throw new Error('图表上传地址无效。')
  const uploaded = await fetcher(url.href,{method:'PUT',headers:{'Content-Type':file.type},body:file,signal,
    credentials:'omit',referrerPolicy:'no-referrer',redirect:'error'})
  if (!uploaded.ok) throw new Error('图表直传失败，请重试。')
  return checked(await fetcher(`/api/board/${paperId}/figure-upload/${grant.upload_id}`,{method:'POST',headers,signal}))
}

async function checked(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.ok === false) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || '图表上传失败，请重试。')
  return data
}
