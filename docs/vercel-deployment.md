# Vercel 部署与云端维护

研雀支持两种存储方式：本地/服务器使用 SQLite 与磁盘；Vercel 使用三个 Turso/libSQL 数据库及私有 Blob。2026-10-23 正式网站已迁入后一种架构。本页不包含维护者的账号、资源编号、数据快照或凭据。

## 架构

- React 静态构建与 Python/FastAPI 函数同站部署；Node 上传授权函数只签发临时单文件许可。
- library 保存文库和持久任务，auth 保存账号，runtime 保存预算、租约和共享配置。三个库须独立。
- PDF 和图表从客户端直传私有 Blob；后端核对归属、大小、类型和哈希后确认入库。下载仍经过账号权限检查。
- 后台任务以数据库状态和有限租约接续；云函数临时磁盘不保存业务状态。

## 准备源码

使用 Python 3.12 和 Node.js 22.13+。按主 README 安装基础依赖，再安装云端锁定依赖：

```sh
python -m pip install --require-hashes -r deploy/vercel-preview/requirements.txt
(cd web && npm ci --ignore-scripts && npm run build)
python scripts/stage_vercel_production_candidate.py /absolute/new/staging-directory
```

输出目录必须尚不存在。生成器只复制选定源码、前端构建和旧插件更新镜像，不复制 `.env`、本机数据库、文献、备份或 Git 历史。部署入口为 `deploy/vercel-production/app.py`；生产路由与每日维护任务模板为 `deploy/vercel-production.example.json`。

## 资源和配置

在自己的 Vercel 项目创建独立生产资源：三个 Turso 数据库、一个私有 Blob。Preview 使用另一组资源，不能复用生产凭据。免费方案仍有用量、带宽和调用次数限制；AI 供应商费用独立计算。

按 `deploy/vercel-production/environment.example` 配置服务器环境变量，填写自己的域名、角色库 URL/token、Blob ID、Zotero OAuth 配置和维护密钥。`OWNER_UID` 用于管理权限；邀请和 AI 登录策略须按实际服务政策确认。所有凭据只进入服务器配置，不使用 `VITE_*`，不提交环境文件。

生产入口还校验非合成模式、独立生产资源和 `PAPERMIND_MIGRATION_APPROVED`。这些护栏与迁移脚本包含维护者原生产目标的限定，不能直接套用到另一套资源；自部署须按自己的目标调整并通过测试。不要为绕过错误关闭权限检查或把私有 Blob 改为公开。

Zotero 应用回调为 `https://自己的域名/api/auth/zotero/callback`，仅请求身份。设置正确的站点 origin 后再开放登录、跨站上传授权和域名。

## 迁移和验收顺序

1. 先用合成数据验证空资源、账号隔离、上传确认、任务恢复、导出和预算。
2. 对旧服务停写并创建可验证快照；在新环境导入库和附件，逐表核对摘要、逐文件核对哈希。
3. 完成真实账号登录、原编号与记录恢复、私有附件下载、AI 请求和退出验证，再切 DNS。
4. 只保留一个业务写入端；保留离线快照。新端开始写入后，不能直接切回旧数据库。

`scripts/import_cloud_production.py` 属于维护者的受限一次性迁移工具：目标固定、不覆盖已有业务表。重复成功须匹配已有标记；不把它当作通用一键导入器。

## 备份与恢复

`scripts/backup_cloud.py` 导出角色库、下载私有附件、校验哈希并重读业务摘要。配置、邀请、会话和在途租约不进入普通恢复包；服务凭据需另行安全保存。运行前通过私有环境提供资源凭据，先用 `--help` 查看参数。

`scripts/run_production_backup.py` 额外固定维护者的生产目标，执行备份后恢复到新目录校验。自部署应使用自己的资源护栏和调度。Mac 定时任务依赖机器开机联网，不等于全天候云备份。维护任务只回收过期附件，不替代数据库和文件备份。

回退时先停止新端写入，创建新的云端一致性备份，再恢复到全新目录，核对账号归属、附件和编号后切换流量。

## 客户端与验证范围

网站提供云端 PDF/图表直传，Zotero 客户端使用 [0.7.1](https://github.com/lvyrana/papermind-zotero/releases/tag/v0.7.1)。旧包不支持大 PDF 直传，可升级或从网页上传；主站旧自动更新清单暂不切换。

CI 验证隔离后端、真实本地 HTTP、前端测试/构建/lint、依赖审计及三组浏览器回归。实机验收范围为 macOS / Zotero 9.0.6；其他平台须另行验证。
