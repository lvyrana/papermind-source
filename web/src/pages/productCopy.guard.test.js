import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = relative => readFileSync(new URL(relative, import.meta.url), 'utf8')

test('设置页收起内置 AI 服务卡片和额度查询，保留账号与数据操作', () => {
  const settings = source('./Settings.jsx')
  assert.match(settings, /const SHOW_BUILTIN_AI_STATUS = false/)
  assert.match(settings, /\{SHOW_BUILTIN_AI_STATUS && <>\s*<SectionLabel>AI 服务<\/SectionLabel>/)
  assert.match(settings, /if \(SHOW_BUILTIN_AI_STATUS\) apiGet\('\/usage'\)/)
  assert.doesNotMatch(settings, /管理 AI 服务、数据与隐私/)
  assert.match(settings, /<AccountCard \/>/)
  assert.match(settings, /<CustomLLMCard \/>/)
  assert.match(settings, /<SectionLabel>数据管理<\/SectionLabel>/)
})

test('账号界面保留登录操作，移除已拒绝的解释性小字', () => {
  const account = source('../components/AccountGate.jsx')
  const login = source('./Login.jsx')
  assert.doesNotMatch(account + login, /另一份旧设备记录保持原样|原专属链接仍用于|关联后旧专属链接不再|只验证身份，不申请|现有试用方式和本地记录保持不变/)
  assert.match(account, /checked=\{adopt\} onChange=/)
  assert.match(account, /使用 Zotero 账号继续/)
  assert.match(account, /退出账号/)
  assert.match(account, /!account\?\.authenticated && result === 'cancelled'/)
  assert.match(account, /连接完成，可返回 Zotero/)
})

test('首页和试读不重复解释未受影响或依赖站点配置', () => {
  const welcome = source('./Welcome.jsx')
  const demo = source('./Demo.jsx')
  assert.doesNotMatch(welcome + demo, /正式书架和阅读记录不受影响|正式 AI 阅读按站点配置使用|正式工作台的 AI 使用取决于站点配置|插件安装与连接按当前发布版本/)
  assert.match(demo, /预设解读/)
  assert.match(demo, /CC BY 4\.0/)
  assert.match(welcome, /相关内容发送至所选模型服务/)
  assert.match(demo, /本地存储不可用，建议下载卡片/)
})

test('引导和阅读状态不承诺第三方零传输或永久保存', () => {
  assert.doesNotMatch(source('./Onboarding.jsx'), /不会.*第三方|不是又一个|不会删除你的文献|30 秒生出/)
  assert.doesNotMatch(source('./Home.jsx'), /不需要先填画像|不用先填画像|请确认后端已启动/)
  assert.doesNotMatch(source('./PaperRead.jsx'), /永久保存|收藏后永久|投递超时，可能是/)
  assert.match(source('./PaperRead.jsx'), /清空这篇论文的全部对话记录？不可撤销/)
  assert.match(source('./Library.jsx'), /笔记和对话也会删除/)
})

test('错误界面提供恢复操作，移除猜测原因和泛化保证', () => {
  const fallback = source('../components/AppErrorFallback.jsx')
  const pdf = source('../components/PdfViewer.jsx')
  assert.doesNotMatch(fallback + pdf, /浏览器可能临时改动|不会因此删除|可能是浏览器内存不足|出版方阻止了跨域加载/)
  assert.match(fallback, /重新加载/)
  assert.match(fallback, /返回首页/)
  assert.match(pdf, /用浏览器打开 PDF/)
  assert.match(pdf, /上传本地 PDF 精读/)
})
