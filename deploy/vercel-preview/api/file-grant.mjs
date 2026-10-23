import { issueSignedToken, presignUrl } from '@vercel/blob'
import { getVercelOidcToken } from '@vercel/oidc'

const noStore = { 'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer' }

async function grant(request) {
  const preview = process.env.VERCEL_ENV === 'preview' && process.env.BLOB_STORE_ID === 'store_RkRS2huB7kCab3j8'
  const production = process.env.VERCEL_ENV === 'production' && process.env.PAPERMIND_MIGRATION_APPROVED === 'true'
    && process.env.PAPERMIND_FILE_BACKEND === 'vercel-blob' && /^store_[A-Za-z0-9]+$/.test(process.env.BLOB_STORE_ID || '')
    && process.env.BLOB_STORE_ID !== 'store_RkRS2huB7kCab3j8'
  if (!preview && !production) return Response.json({ detail: 'File storage unavailable' }, { status: 503, headers: noStore })
  const origin = `https://${process.env.VERCEL_URL}`
  const publicOrigin = production ? process.env.PAPERMIND_PUBLIC_ORIGIN : origin
  try {
    const parsed = new URL(publicOrigin)
    if (parsed.protocol !== 'https:' || parsed.origin !== publicOrigin) throw new Error('Invalid origin')
  } catch {
    return Response.json({ detail: 'Site origin unavailable' }, { status: 503, headers: noStore })
  }
  if (request.headers.get('origin') && request.headers.get('origin') !== publicOrigin) {
    return Response.json({ detail: 'Forbidden origin' }, { status: 403, headers: noStore })
  }
  try {
    const body = await request.json()
    const figure = body.kind === 'figure'
    if (body.kind && !figure && body.kind !== 'pdf') return Response.json({detail:'Invalid upload kind'},{status:400,headers:noStore})
    const paperId = String(body.paper_id)
    if (!/^[1-9][0-9]{0,9}$/.test(paperId)) return Response.json({ detail: 'Invalid paper' }, { status: 400, headers: noStore })
    // Never accept an upstream URL or pathname from a caller. Python owns auth,
    // paper ownership, reservations and quotas; forward only identity headers.
    const headers = { 'Content-Type': 'application/json',
      'x-vercel-trusted-oidc-idp-token': await getVercelOidcToken() }
    for (const key of ['authorization', 'cookie', 'x-user-id', 'origin']) {
      const value = request.headers.get(key)
      if (value) headers[key] = value
    }
    const reservation = figure ? `/api/board/${paperId}/figure-upload` : `/api/library/${paperId}/pdf-upload`
    const response = await fetch(`${origin}${reservation}`, {
      method: 'POST', headers, redirect: 'error',
      body: JSON.stringify(figure ? {size:body.size,mime:body.mime,section:body.section,page:body.page,caption:body.caption} : { size: body.size }),
      signal: AbortSignal.timeout(20000),
    })
    if (!response.ok) return Response.json({ detail: '文件授权失败，请检查登录、文件大小或稍后重试。',
      stage: response.headers.get('content-type')?.includes('application/json') ? 'application_auth' : 'deployment_protection',
    }, { status: response.status, headers: noStore })
    const record = await response.json()
    if (!(figure ? /^board-figure-v1\/[0-9a-f]{32}\.(png|jpg)$/ : /^paper-pdf-v1\/[0-9a-f]{32}\.pdf$/).test(record.pathname)) throw new Error('Invalid stored path')
    if (figure && !['image/png','image/jpeg'].includes(record.mime)) throw new Error('Invalid image type')
    const validUntil = record.expires * 1000
    const constraints = { allowedContentTypes: [figure ? record.mime : 'application/pdf'], maximumSizeInBytes: record.size }
    const token = await issueSignedToken({ pathname: record.pathname, operations: ['put'], validUntil, ...constraints })
    const { presignedUrl } = await presignUrl(token, {
      pathname: record.pathname, operation: 'put', access: 'private', validUntil, ...constraints,
      addRandomSuffix: false, allowOverwrite: false, cacheControlMaxAge: 60,
    })
    return Response.json({ upload_id: record.id, upload_url: presignedUrl }, { headers: noStore })
  } catch {
    // Errors can contain signed URLs or credentials; never log the raw error.
    console.error('private_pdf_grant_failed')
    return Response.json({ detail: '文件授权服务暂不可用，请重试。' }, { status: 502, headers: noStore })
  }
}

export async function POST(request) { return grant(request) }
