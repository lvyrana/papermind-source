/* global process */
import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { chromium } from 'playwright'

const base = process.env.PAPERMIND_TEST_URL
const paper = Number(process.env.PAPERMIND_TEST_PAPER)
if (!/^https:\/\/litfinch-migration-preview-[a-z0-9]+-lvyranas-projects\.vercel\.app$/.test(base) || !paper) throw Error('Invalid preview')
const browser = await chromium.launch({headless:true,chromiumSandbox:true,
  ...(process.env.PAPERMIND_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PAPERMIND_CHROMIUM_EXECUTABLE} : {}),
  ...(process.env.HTTPS_PROXY ? {proxy:{server:process.env.HTTPS_PROXY}} : {})})
const errors = []
let stage = 'reader'
try {
  const context = await browser.newContext({viewport:{width:1440,height:1000}})
  await context.addCookies([{name:'pm_session',value:process.env.PAPERMIND_TEST_SESSION,url:base,httpOnly:true,secure:true,sameSite:'Lax'}])
  await context.addInitScript(uid => {
    localStorage.setItem('papermind-uid',uid)
    localStorage.setItem('pm-paper-tour-done','1')
  },process.env.PAPERMIND_TEST_UID)
  await context.route('**/*',route => new URL(route.request().url()).origin === base
    ? route.continue({headers:{...route.request().headers(),'x-vercel-trusted-oidc-idp-token':process.env.VERCEL_OIDC_TOKEN}})
    : route.continue())
  const page = await context.newPage()
  page.on('pageerror',error => errors.push(error.name))
  await page.goto(`${base}/paper/${paper}?library=1`,{waitUntil:'domcontentloaded',timeout:60000})
  const canvas = page.locator('canvas').first()
  await canvas.waitFor({state:'visible',timeout:60000})
  stage = 'snip_and_send'
  await page.getByTitle('截取图表：框选 PDF 上的图/表',{exact:true}).click()
  const box = await canvas.boundingBox()
  await page.mouse.move(box.x+30,box.y+40)
  await page.mouse.down()
  await page.mouse.move(box.x+Math.min(box.width-20,280),box.y+170,{steps:8})
  await page.mouse.up()
  await page.getByAltText('图表截图',{exact:true}).waitFor({state:'visible',timeout:15000})
  const completed = page.waitForResponse(response => new URL(response.url()).pathname.startsWith(`/api/board/${paper}/figure-upload/`),{timeout:60000})
  completed.catch(() => {})
  await page.getByRole('button',{name:/关键结果/}).last().click()
  const response = await completed
  assert.equal(response.status(),200)
  const item = (await response.json()).item
  stage = 'reload_and_render'
  await page.reload({waitUntil:'domcontentloaded'})
  await page.getByRole('button',{name:/打开汇报板/}).first().click({timeout:30000})
  const image = page.locator(`img[src*="/figures/${item.image}"]`).first()
  await image.waitFor({state:'visible',timeout:30000})
  await image.evaluate(image => image.decode())
  assert.ok(await image.evaluate(image => image.naturalWidth>0))
  const artifact = await mkdtemp('/private/tmp/litfinch-figures-ui-')
  await page.screenshot({path:`${artifact}/reopened-figure.png`})
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({ok:true,checks:['pdf_snip','browser_direct_upload','board_entry_created','reopened_private_image'],screenshot:`${artifact}/reopened-figure.png`,page_errors:errors}))
} catch(error) {
  console.log(JSON.stringify({ok:false,stage,error_type:error.name,page_errors:errors}))
  process.exitCode = 1
} finally {await browser.close()}
