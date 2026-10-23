// The actual page resumes a persisted queue. No live OAuth or model credentials.
/* global process */
import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { mkdtemp } from 'node:fs/promises'

const base = process.env.PAPERMIND_TEST_URL
const job = process.env.PAPERMIND_TEST_JOB
const paper = Number(process.env.PAPERMIND_TEST_PAPER)
if (!/^https:\/\/litfinch-migration-preview-[a-z0-9]+-lvyranas-projects\.vercel\.app$/.test(base) || !/^[a-f0-9]{32}$/.test(job) || !paper) throw Error('Invalid preview')
const browser = await chromium.launch({ headless:true, chromiumSandbox:true,
  ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE ? { executablePath:process.env.PAPERMIND_CHROMIUM_EXECUTABLE } : {}),
  ...(process.env.HTTPS_PROXY ? {proxy:{server:process.env.HTTPS_PROXY}} : {}) })
const errors = []
let stage = 'page_reentry'
try {
  const context = await browser.newContext()
  await context.addCookies([{name:'pm_session',value:process.env.PAPERMIND_TEST_SESSION,url:base,httpOnly:true,secure:true,sameSite:'Lax'}])
  await context.addInitScript(uid => localStorage.setItem('papermind-uid',uid),process.env.PAPERMIND_TEST_UID)
  await context.route('**/*', route => new URL(route.request().url()).origin === base
    ? route.continue({headers:{...route.request().headers(),'x-vercel-trusted-oidc-idp-token':process.env.VERCEL_OIDC_TOKEN}})
    : route.continue())
  const page = await context.newPage()
  page.on('pageerror',error => errors.push(error.name))
  let calls = 0
  page.on('request',request => { if (new URL(request.url()).pathname === `/api/tasks/${job}/run`) calls++ })
  const finished = page.waitForResponse(response => new URL(response.url()).pathname === `/api/tasks/${job}/run`,{timeout:60000})
  finished.catch(() => {})
  await page.goto(`${base}/library`,{waitUntil:'domcontentloaded',timeout:60000})
  assert.equal((await finished).status(),200)
  await page.getByText('文献解读已保存，重新打开文献可查看。',{exact:true}).waitFor({timeout:15000})
  const notice = await page.getByLabel('解读任务',{exact:true}).boundingBox()
  const viewport = page.viewportSize()
  assert.ok(notice.x >= 0 && notice.y >= 0 && notice.x + notice.width <= viewport.width && notice.y + notice.height <= viewport.height)
  const artifact = await mkdtemp('/private/tmp/litfinch-jobs-ui-')
  await page.screenshot({path:`${artifact}/task-complete.png`})
  stage = 'result_read_and_no_replay'
  const result = await page.evaluate(async paper => (await (await fetch(`/api/library/${paper}`)).json()).paper.summary_zh,paper)
  assert.ok(result.includes('合成验收摘要'))
  const listed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/tasks',{timeout:30000})
  await page.reload({waitUntil:'domcontentloaded'})
  assert.deepEqual((await listed).status(),200)
  assert.equal(calls,1)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({ok:true,checks:['browser_resumes_queue','completion_visible','persisted_result_read','reload_no_replay'],task_requests:calls,page_errors:errors,screenshot:`${artifact}/task-complete.png`}))
} catch(error) {
  console.log(JSON.stringify({ok:false,stage,error_type:error.name,page_errors:errors}))
  process.exitCode = 1
} finally { await browser.close() }
