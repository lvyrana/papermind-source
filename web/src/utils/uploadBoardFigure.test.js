import test from 'node:test'
import assert from 'node:assert/strict'
import { uploadBoardFigure } from './uploadBoardFigure.js'
const file = new File(['synthetic image'], 'figure.png', {type:'image/png'})
const json = (data,status=200) => new Response(JSON.stringify(data),{status})
test('figure direct upload preserves metadata, strips identity at Blob and completes last',async () => {
  const calls = []
  const replies = [json({mode:'direct'}),json({upload_id:'abc',upload_url:'https://vercel.com/api/blob/?signed=test'}),new Response('',{status:200}),json({ok:true,item:{id:1}})]
  const result = await uploadBoardFigure(2,file,'uid',{section:'results',page:3},undefined,async (...args) => {calls.push(args);return replies.shift()})
  assert.equal(result.item.id,1)
  assert.equal(JSON.parse(calls[1][1].body).kind,'figure')
  assert.equal(JSON.parse(calls[1][1].body).page,3)
  assert.deepEqual(calls[2][1].headers,{'Content-Type':'image/png'})
  assert.equal(calls[2][1].credentials,'omit')
  assert.equal(calls[3][0],'/api/board/2/figure-upload/abc')
})
test('old server retains multipart and failed grant never falls back',async () => {
  const calls = []
  const replies = [json({},404),json({ok:true})]
  await uploadBoardFigure(2,file,'uid',{section:'methods'},undefined,async (...args) => {calls.push(args);return replies.shift()})
  assert.equal(calls[1][0],'/api/board/2/figures')
  assert.ok(calls[1][1].body instanceof FormData)
  await assert.rejects(uploadBoardFigure(2,file,'uid',{section:'methods'},undefined,async () => json({},503)),/失败/)
})
test('untrusted URL, invalid image and incomplete PUT are rejected',async () => {
  await assert.rejects(uploadBoardFigure(2,{size:12,type:'image/svg+xml'},'uid',{}),/PNG/)
  let replies = [json({mode:'direct'}),json({upload_url:'https://other.example/file'})]
  await assert.rejects(uploadBoardFigure(2,file,'uid',{},undefined,async () => replies.shift()),/地址无效/)
  replies = [json({mode:'direct'}),json({upload_url:'https://vercel.com/api/blob/'}),new Response('',{status:403})]
  await assert.rejects(uploadBoardFigure(2,file,'uid',{},undefined,async () => replies.shift()),/直传失败/)
})
