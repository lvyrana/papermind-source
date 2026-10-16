# Zotero 账号登录

2026-10-08 名称已确认：网站与插件统一使用「研雀 · LitFinch」，登记不再使用 PaperMind。域名、回调、环境变量和数据标识保持原样。本地显示更名不代表应用已登记或真实授权已通过。

状态：2026-09-30 本地实现。尚未配置真实 Zotero 应用凭据、部署或实机验收登录。Connector 0.7.0 是本地候选包；本机此前安装版本仍为 0.6.1。

## 用户路径

网站「设置 → 研雀账号」或插件阅读台「登录」发起。浏览器进入 Zotero 官方授权页，确认后返回研雀；插件通过一次性连接码接收会话。网站与插件识别同一个 Zotero 用户，无需新增密码。

只请求 `identity=1` 身份确认，不申请云端文库读取或写入权限。研雀不接收 Zotero 密码，不保存 Zotero API Key。依据：[Zotero OAuth 官方文档](https://www.zotero.org/support/dev/web_api/v3/oauth)。

插件的本地 PDF、手工卡片和汇报仍可离线使用。服务启用后，内置 AI 默认要求登录；收藏与个人设置仍可保存，匿名保存不会顺带启动后台 AI 解读。网站和插件统一身份不等于插件本地卡片已同步到网站；本次未实现记录同步、订阅或计费。

## 旧记录与退出

- 首次创建账号可明确勾选关联当前浏览器或发起连接的插件原有身份。服务器只登记归属，不迁移、重写论文、笔记或卡片。
- 关联后，原 UUID 专属链接不再单独授权访问该空间；其他设备要登录同一 Zotero 账号。维护者首次登录需关联原 owner 身份才能保留原管理权限；普通登录不授予管理权限。
- 已有账号从另一旧身份登录时，保持两份记录，暂不自动合并。未关联的旧空间仍可用旧链接访问；插件本地旧文件不删除。后续迁移应显式选择来源、目标并备份。
- 浏览器缓存和未提交草稿按账号归档，切换前先保存；损坏或空间不足时不先清空原草稿。插件按服务、数据身份和论文分文件保存。退出后切换本地空间；再次登录原账号可恢复其记录。
- 退出只撤销当前客户端会话，不退出 Zotero Desktop 的同步账号，也不删除阅读记录。浏览器和插件可分别退出。会话最长 30 天，到期重新登录；不提供静默长期刷新或全设备管理界面。

## 启用步骤

### 应用登记填写底稿（2026-10-09）

已在最近协作记录中确认进入 Zotero 应用登记页，尚无提交成功或凭据生成证据。以下内容根据当前代码和品牌决定准备；正式表单尚未完整读取，字段名称和必填项以实际页面为准，不据此宣称已登记。

| 内容 | 待填值 |
|---|---|
| 应用名称 | `研雀 · LitFinch` |
| 网站 | `https://papermindapp.com` |
| Callback URL | `https://papermindapp.com/api/auth/zotero/callback` |
| 中文说明（如需） | 论文精读与研究笔记工具，通过 Zotero 仅验证用户身份，不读取或修改云端文库。 |
| 英文说明（如需） | LitFinch supports close reading and research notes. Zotero is used only to verify identity, without access to the user's online library. |
| 联系邮箱、维护者资料（如需） | 使用维护者确认的信息，不从其他账号推断或代填。 |

`identity=1` 是授权请求参数，由 `papermind/auth.py` 设置，不是要求用户创建文库 API Key。2026-10-09 复核 [Zotero 官方文档](https://www.zotero.org/support/dev/web_api/v3/oauth)：该模式仅获取身份，不创建文库 API Key。

登记返回的 Client Key / Client Secret 只进入服务端受控配置，对应 `ZOTERO_OAUTH_CLIENT_KEY` / `ZOTERO_OAUTH_CLIENT_SECRET`；不填进本底稿、前端或插件。另设 `PAPERMIND_PUBLIC_ORIGIN=https://papermindapp.com`，不带路径和尾斜杠。

### 登记后执行顺序

1. 用项目维护者的 Zotero 账号打开 [应用登记](https://www.zotero.org/oauth/apps)。应用名称填 `研雀 · LitFinch`，站点填 `https://papermindapp.com`，固定 Callback URL 填 `https://papermindapp.com/api/auth/zotero/callback`。若表单需要说明，使用“论文精读与研究笔记工具，通过 Zotero 仅验证用户身份，不读取或修改云端文库”。本应用在授权请求中固定 `identity=1`；不要另行创建文库 API Key 或申请整库权限。
2. 在服务器受控配置中写入 `ZOTERO_OAUTH_CLIENT_KEY`、`ZOTERO_OAUTH_CLIENT_SECRET`、`PAPERMIND_PUBLIC_ORIGIN`。客户端密钥不得放前端、插件、Git、截图或聊天。站点 origin 和授权回调必须完全匹配；反向代理保留 HTTPS 和 Origin。
3. 确认部署目标与维护窗口后，先备份 `paperdiary.db`、附件及已有 `auth.sqlite3`，验证可恢复性。按 [部署与恢复中的账号上线检查](deployment-recovery.md#账号功能首次上线检查)安装回调配置；现有 `deploy/update.sh` 不会自动把该 include 加入既有 HTTPS server 块。网站前后端成套更新，仅更新前端时遇到旧服务的 status 404 会保留旧试用模式。
4. 在已配置回调的受控验收环境中，用测试账号走完整真实授权：首次关联旧记录 → 网页重开 → 插件核对连接码 → 登录同一账号 → 发起一次经授权的 AI 请求 → 分别退出 → 确认旧链接不能访问已关联空间。再检查原生 Zotero 重启后的凭据恢复。若使用正式域名完成首轮验收，应先部署到该域名并限制试用范围；不能在尚无回调服务时把真实授权记为通过。
5. 验收后再更新分发镜像、发布插件并向更多试用者开放。配置、部署、原生安装、发布分别记录完成证据；本轮仅完成本地准备。

### 真实验收记录（待登记后执行）

- [ ] 授权页面显示研雀名称，只验证身份；成功回到固定 HTTPS 回调。
- [ ] 新测试账号可登录；取消授权可回到可重试状态，不出现假登录。
- [ ] 测试旧空间主动关联后仍能读取原记录；退出后不能靠旧 UUID 访问已关联空间。
- [ ] 插件领取连接码后与网站身份一致；重启 Zotero 后凭据可恢复，取消、退出不留下迟到会话。
- [ ] 获邀账号可完成一次已授权的 AI 请求；未获邀账号不因登录而绕过邀请限制。
- [ ] 浏览器和插件分别退出有效；其他账号看不到测试账号的草稿、卡片或笔记。

仅记录版本、环境、步骤和通过/失败摘要；不记录密钥、连接码、Cookie、完整回调查询串或真实论文内容。

启用后 `PAPERMIND_REQUIRE_AI_LOGIN=true` 为默认。邀请制仍独立生效：账号登录成功不自动获得试用邀请；原 UID 被关联后原有邀请继续有效。新账号若未关联受邀 UID，需要维护者按当前同意状态授权其 data_uid。`/api/auth/` 必须允许未登录用户访问，业务 API 仍受邀请规则限制。

## 存储、备份与恢复

服务端 `data/auth.sqlite3` 保存 Zotero 数字 ID、用户名、研雀账号与数据归属，文件权限 600；会话和设备轮询秘密仅保存 SHA-256。授权期间的临时 request-token secret 最长有效 10 分钟，完成交换即删除，不保存长期 Zotero 密钥。登录尝试按 ASGI peer 哈希作 10 分钟限流。代理部署应确认受信任代理设置，避免所有用户共用同一来源额度。

网站会话放 HttpOnly、SameSite=Lax cookie，HTTPS 下启用 Secure。插件凭据放 Zotero/Firefox Login Manager；不放普通首选项、打开网页的 URL 或阅读文件。插件登录中取消、切换服务或退出后收到的迟到凭据会被撤销。

`scripts/backup_data.py` 已将存在的 `auth.sqlite3` 纳入一致性检查与私有快照。恢复保留账号归属，并清空会话、未完成授权、设备连接和旧限流记录；用户需要重新登录，不恢复已撤销的会话。备份仍包含身份数据，应放受控目录。

禁止只还原旧 `paperdiary.db` 后丢弃账号归属库，或回退到不认识已关联账号的旧后端。关闭 OAuth 配置不会解除已经登记的归属限制。需要回退时保持业务入口关闭，保留当前 auth 数据和备份，先验证恢复组合。日志和监控不得记录 cookie、Bearer、POST 登录体或带 verifier 的完整回调查询串；生产反向代理应为回调关闭查询串日志。

## 验证与未完成项

`scripts/test_backend_isolated.py` 在临时源码副本测试，禁用真实模型和 OAuth 凭据；`tests/test_accounts.py` 只模拟 Zotero 服务端交换，真实运行本站路由与数据库。覆盖签名向量、浏览器绑定、重放、跨站请求、设备取消与一次性领取、旧归属、不同账号、过期和退出。

`scripts/smoke_backend.py` 启动隔离后端，通过真实本机 HTTP 检查登录状态、AI 门槛、设备连接与取消，不连接 Zotero 或模型。前端 `scripts/check-accounts-browser.mjs` 使用合成账号验证按钮、草稿、退出和插件确认。插件 `test-account.mjs` 模拟 Login Manager 与服务响应；这不能代替真实 Zotero 版本中的登录存储验收。

真实 Zotero OAuth、原生凭据存取及正式部署待完成。自动同步、跨旧空间合并、全设备会话列表和账号删除界面不在本轮范围内。
