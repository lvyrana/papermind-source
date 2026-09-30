# PaperMind

面向学术论文的精读与知识沉淀工具：从 PDF / Zotero 进入原文，经过带读、追问和自测，留下可回溯的阅读卡片与汇报材料。文献发现与推荐作为辅助入口保留。

> 主项目采用 [AGPL-3.0-only](LICENSE)，保留现有作者署名。新建的 [`papermind-source`](https://github.com/lvyrana/papermind-source) 为私有源码验收仓库，尚未公开发布本批改动。旧仓库的公开历史需单独处理。准备进度、验证方法和剩余发布条件见 [开源准备与自部署说明](docs/open-source-readiness.md)。

## 项目信息

- **项目名称**: PaperMind
- **启动时间**: 2026 年 3 月 25 日
- **最近发布版本**: v0.16.10；当前工作区另含未发布的开源准备改动
- **独立开发**: 雀雀（主导设计、需求定义、产品决策、测试与迭代）
- **开发方式**: 使用 AI 编程工具（Claude）辅助代码实现

> 本项目由雀雀主导设计、需求定义、测试与迭代，开发过程中使用 AI 编程工具辅助实现。所有产品决策、功能规划、视觉风格和交互设计均由作者独立完成。

## 核心功能

- **首页精读工作台**（桌面 W2 单栏）: 一进来就是「放入一篇论文开始精读」＋「继续上次精读」＋「最近的精读工程」，把入口对准精读闭环而非发现流；每个工程带在读/读过状态、卡片/笔记/对话计数与「已导出」标记（推荐流以旗标保留，移动端仍为推荐首页）
- **智能文献追踪**: 从 PubMed、Semantic Scholar 自动获取领域最新论文
- **个性化推荐**: 基于研究画像生成检索词，结合方法兴趣动态打分；系统观察摘要只在手动修正后参与理解层，不主导搜索召回
- **排除不感兴趣的方向**: 画像中设置排除领域，搜索和评分双重屏蔽
- **三栏精读阅读页**: 左栏论文元数据 / 中栏 PDF 阅读器（翻页、缩放、划词，长文档按当前页附近窗口化渲染；页级渲染失败时自动降级重试并提供原生 PDF 打开出口）/ 右栏精读工作台与沉淀区
- **精读工作台**: 路线图（整篇怎么读）/ 摘要带读 / 当前页陪读三种模式；PDF 划词可用「精读这段」拆解并继续追问，精读结果可随时收起或保存为笔记（功能与实现总览见 `docs/deep-reading-overview.md`）
- **阅读卡片**: 精读时把读懂的内容沉淀为方法/发现/批判/迁移四类结构化卡片，支持划词入卡、对话归卡、AI 起草，卡片带原文页码锚点可跳转
- **本地 PDF 精读**: 无免费全文时可直接上传手头的 PDF，重进页面自动加载
- **Zotero 一键精读**: Connector `0.3.0` 支持在 Zotero 里右键文献 →「用 PaperMind 精读」，元数据和 PDF 自动进入阅读页。首次使用只需粘贴设置页复制的专属链接；旧版会自动保留身份并清除已经失效的预览密码。公开源码与后续发布位于 [`lvyrana/papermind-zotero`](https://github.com/lvyrana/papermind-zotero)
- **对话式阅读**: 和 AI 深度讨论论文，提问方法学、核心发现、研究启发
- **对话记忆**: 聊天记录自动保存，收藏后永久留存；对话总结可保存为笔记并送入汇报板，不再需要的记录可从标题栏一键清空
- **研究笔记**: 边读边记，AI 对话可一键保存为笔记
- **汇报板导出**: 一键导出真 **PowerPoint**（`.pptx`，16:9，双击即开，无需 Marp / Office / Chromium），
  长板块按容量自动续页、图表独占一页；也可导出**纯 Markdown**（粘进 Obsidian / Notion）。封面 + 七板块，
  空板块出「待填入」——骨架即进度
- **笔记导出**: 支持将全部笔记导出为 Markdown，便于沉淀到 Obsidian / Notion / 本地文件
- **标题 & 摘要翻译**: 点击即译，原文/中文随时切换（首页、阅读页、收藏页均支持）
- **书架**（原收藏页）: 精读工程列表 + 标题搜索 + `全部/在读/读过/有导出` 筛选；每个工程点进真精读台
- **精读画像 + 记忆面板**: 书架顶部深色卡展示主题分布与四类卡片构成；点开是记忆面板——
  **阅读画像**（从精读行为总结，可直接改写 / 按历史重新生成 / 清空）＋ **你说过的**（对话里明确讲过的课题与兴趣，跨对话保留、逐条可删）。
  **所见即所得**：面板里显示什么，AI 拿到的就是什么；删空了它就什么背景都不知道
- **精读台右栏**: 沉淀（卡片流）+ 出口（汇报板 / 导出 PPT·Markdown）常驻；带读与自由笔记采用手风琴互斥展开，对话和其他操作按需唤出，不挤压卡片主轨
- **苏格拉底自测**: 读完让 papermind 考考你——围绕五个核心问题（研究问题 / 方法—问题匹配 / 结果有多强 / 哪里站不住 / 外推边界）一次一问、三问收口。出题混合「已有卡片 + 划了没做卡的段落 + 完全没碰的板块」，刻意往盲区问；判定分「站住了 / 部分对 / 还不对」三档且**每条反馈都带可点回原文的锚点**（后端逐字校验，编造的锚点会被剥掉）。答不上来可「不确定·转到对话」带上下文过去、再返回作答；暴露的概念累积成「方法学盲区」
- **文献导出**: 一键导出 RIS/BibTeX，直接导入 Zotero/EndNote/Mendeley
- **PDF 获取**: 自动查找开放获取全文
- **跨设备访问**: 新设备默认独立；主动打开设置页生成的专属链接后才同步同一份数据
- **手动添加论文**: 输入 PMID / DOI / 标题关键词搜索并收藏任意论文，自动触发 AI 解读
- **设备隔离**: 无需个人账号，书架、PDF、笔记、对话、卡片、汇报板和自测按匿名设备 ID 隔离
- **测试期安全兜底**: Owner 专属连通性测试、按端点限额、可配置 CORS 来源
- **前端故障与隐私兜底**: 禁止浏览器自动翻译直接改写动态阅读页，未处理异常提供重新加载出口；诊断上报默认关闭，仅部署者配置自己的 Sentry DSN 后启用，回放另需明确开启且遮罩文本、阻止媒体录制；部署后 HTML 入口强制重新校验，避免旧前端继续运行已修复错误

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 19 + Vite + Tailwind CSS 4 + Lucide Icons |
| 后端 | Python + FastAPI + SQLite + systemd/nginx 部署 |
| 数据源 | PubMed E-utilities + Semantic Scholar API |
| AI | 可在设置页配置自定义 API（OpenRouter / DeepSeek / 智谱 / 通义 / Kimi / 硅基流动 / OpenAI 兼容接口）→ 内置：阿里云通义（同 key 多模型顺序回退）→ 智谱 GLM → DeepSeek |

## v0.5.9 相比 v0.5 的主要变化

- 研究画像页从旧表单升级为新的分区式页面，新增 `method_interests` 和“系统观察摘要”
- 搜索链路在 `v0.5` 主题检索基础上，开始显式参考方法兴趣，但系统观察摘要已退出关键词生成，只保留在手动修正后的理解层
- 首页增加本地缓存恢复与当前批次补解读机制
- 收藏 / 笔记 / AI 对话相关接口的用户归属校验更严格
- 收藏页调整为更紧凑的行列表，增加“有笔记”筛选，移动端删除入口可见
- 新增按端点拆分的 AI 限速与全局熔断
- 设置页新增 Markdown 笔记导出
- 设置页新增"切换账号"同步码入口，支持跨设备数据迁移
- UID 持久化升级为 localStorage + cookie 双写，手机刷新不再丢失设备身份
- 设置页移动端兼容性增强，设备 ID 读取失败不再导致整页空白
- `/api/settings/test` 改为仅 owner 设备可调用，生产环境可通过 `ALLOWED_ORIGINS` 收紧跨域
- 新增面向阿里云 ECS 的部署脚本与服务配置
- 新增 SQLite 日备份脚本与 `systemd timer`，默认每天自动备份并保留 14 天
- 修复首页详情页与收藏详情之间的对话/总结同步问题，首页先聊再收藏也能完整继承历史记录
- 支持通过 `QWEN_FALLBACK_MODELS` 为同一个阿里云 API Key 配置多模型顺序回退，并在后端日志里显示实际命中的模型
- 修复收藏详情里长笔记编辑区过矮的问题，移动端也能直接看到编辑 / 删除按钮
- 修复 Semantic Scholar 时间窗失真问题，并放宽 PubMed 长查询，提升近一个月检索的稳定性
- 首页、收藏页、研究画像页的空状态与标题层级进一步统一，去掉“本周论文”等旧周报遗留文案

## 快速开始

### 环境准备

源码验收仓库为 `lvyrana/papermind-source`，当前私有，克隆需 GitHub 账号权限。首次部署脚本已默认指向新仓库；自部署者可用 `PAPERMIND_REPO_URL` 指定自己的授权仓库。不要从旧历史仓库安装来替代本批验收代码。

以下命令均从**本仓库根目录**执行。后端验证环境为 Python 3.12、Linux/macOS；Windows 后端使用 WSL2。前端构建使用 Node.js 22.13 及以上的 22.x 版本，或 Node.js 24 LTS。浏览器用户不需要安装这些环境。安装外部依赖需要网络。

```bash
python3 -m venv papermind/.venv_new
papermind/.venv_new/bin/python -m pip install --upgrade pip==26.2.1
papermind/.venv_new/bin/python -m pip install --require-hashes -r papermind/requirements.lock
papermind/.venv_new/bin/python scripts/deployment_preflight.py
```

### 配置 API Key

首次复制配置模板，已有文件不覆盖。按需填入至少一个服务商的 API Key；不配置时可查看页面，但 AI 功能不可用：

```bash
cp -n papermind/.env.example papermind/.env
```

### 构建前端与启动后端

```bash
(cd web && npm ci && npm run build)
(cd papermind && .venv_new/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000)
```

打开 http://127.0.0.1:8000 即可使用。前端构建产物位于 `web/dist/`，由 FastAPI 提供。默认仅本机可访问，不应直接改为公网监听来招募试用者。

要管理全局模型配置，在网站设置页复制当前设备 UUID，填入 `papermind/.env` 的 `OWNER_UID` 后重启。留空时任何设备都不能修改或探测主机模型配置，普通阅读和 Zotero 连接不受此管理权限限制。设备 UUID / 专属链接具有访问凭证作用，不得发送到公开群或提交到 Git。

如果本机访问 `127.0.0.1:8000` 出现 `502 Bad Gateway`，通常是 shell 里配置了全局代理。请确认 `NO_PROXY` / `no_proxy` 包含：

```bash
localhost,127.0.0.1,::1
```

开发调试时也可以分别启动：

```bash
# 终端一（仓库根目录）
(cd papermind && .venv_new/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000)

# 终端二（仓库根目录）
(cd web && npm ci && npm run dev)
```

此时 Vite 会把 `/api` 代理到本机 `8000` 端口。

前端诊断配置参见 `web/.env.example`。只有需要上报时才创建 `web/.env.local` 并填写自己的 `VITE_SENTRY_DSN`，配置更改后重新构建；后端 `SENTRY_DSN` 不会自动开启前端诊断。所有 `VITE_` 变量会进入浏览器代码，严禁放模型密钥。

### 本地验证

```bash
# 临时源码副本运行后端测试，不加载本地 .env 或真实 data/
papermind/.venv_new/bin/python scripts/test_backend_isolated.py
(cd web && npm test && npm run build)
# 仅检查当前 Git 候选文件；不检查历史、图片内容或完整密钥格式
python3 scripts/check_public_release.py --code-only
# 包含主项目 LICENSE 存在性检查；通过不等于历史或发布授权已验收
python3 scripts/check_public_release.py
```

PDF 阅读器当前锁定 `pdfjs-dist@6.2.108`，主库、worker 和文字层样式使用同版本 legacy 构建。升级阅读依赖时，还需运行真实浏览器回归：

```bash
(cd web && npx playwright install chromium && npm run test:pdf-reader)
(cd web && npm audit --omit=dev --audit-level=high)
```

Linux 缺少浏览器系统库时使用 `npx playwright install --with-deps chromium`。已有 Chrome 可通过 `PAPERMIND_CHROMIUM_EXECUTABLE` 指定绝对可执行路径。测试临时启动本地页面与无个人资料的无头浏览器，使用合成 PDF，不调用业务后端或模型；覆盖划词、高亮、缩放、43 页窗口化渲染、页图截取、文档切换/卸载、损坏文件出口与无脚本执行边界。浏览器下载需要网络，测试运行只允许本地测试源；不能替代 Windows/微信浏览器、复杂中文 PDF 的实际验收。`npm audit` 无告警也不是安全认证。

## 数据与备份（本地运行）

所有用户数据都在本地文件里，请纳入备份习惯：

| 内容 | 位置 |
|---|---|
| 主数据库（收藏/笔记/对话/卡片） | `papermind/data/paperdiary.db` |
| 上传的 PDF | `papermind/data/pdfs/` |
| 汇报板中的图表截图 | `papermind/data/figures/` |
| 主机级 LLM 配置（含 API Key） | `papermind/data/config.json` |

备份与恢复：

```bash
# 完整备份：数据库、PDF、图表和已存在的 AI 用量计数
bash scripts/backup_local.sh

# 同时打包上传的 PDF 和图表截图
bash scripts/backup_local.sh --with-files

# 可选：直接备份到移动硬盘或受控云盘目录
BACKUP_DIR="/Volumes/你的备份盘/PaperMind" bash scripts/backup_local.sh --with-files

# 校验指定快照，路径替换为实际生成的目录
python3 scripts/backup_data.py verify backups/snapshot-实际名称

# 先恢复到一个不存在的新目录；禁止覆盖现有 data/
python3 scripts/backup_data.py restore backups/snapshot-实际名称 --destination /安全目录/restored-data
```

快照在 `backups/`（已忽略）内，带 SHA-256 清单和 SQLite 完整性校验，旧备份不自动删除。备份期间检测到数据库或附件变化会拒绝发布快照，可在低峰重试或停止服务后执行。快照含真实内容，须保管在受控、最好加密的磁盘；本地快照不等于异地备份。

`.env`、`config.json` 和邀请授权名单不进入快照；密钥需安全重配，邀请按当前同意状态重新授权，避免恢复已经撤销的访问权限。运行目录切换、预算计数和回滚步骤见 [部署与恢复](docs/deployment-recovery.md)。旧 `.db.gz` 备份保留，但不自动转换为新格式。

## 小范围试用

给同事/同学开放试用前，请阅读 [docs/trial-guide.md](docs/trial-guide.md)——包含试用者使用须知、隐私说明（数据存储位置、内容会发送至第三方 LLM API）、主持人准备清单和反馈收集模板。

新增可选邀请模式、全局 AI 请求预算/并发限制及文件总量/单设备配额。邀请模式默认关闭以保留现有设备访问；转向科研群招募前，应先授权旧设备再开启 `PAPERMIND_INVITE_ONLY=true`，逐人发放私有链接。默认 AI 每 UTC 日最多 500 次供应商调用、同时 4 次；默认上传总量 2 GiB、单设备 250 MiB。失败和回退也计入调用预算，预算不等同于金额上限。具体命令见 [部署与恢复](docs/deployment-recovery.md)。

当前产品验证采用“中文核心闭环优先”路线。先按 [中文文献精读验收方案](docs/chinese-reading-validation.md) 验证研究逻辑、证据忠实、原文回溯和沉淀价值；通过后再增加英文术语、长句拆解和翻译辅助。

## 部署（ECS）

仓库内提供面向 Ubuntu 24.04 + Python 3.12 的 ECS 部署模板，固定路径 `/opt/papermind` 和服务用户 `ubuntu`；其他服务器需先调整模板。更新使用维护窗口，失败时保留备份、旧构建和提交号，不自动把失败版本重新上线：

- `deploy/setup.sh`：首次部署
- `deploy/update.sh`：后续更新
- `deploy/papermind.service`：systemd 服务
- `deploy/papermind-backup.service`：数据库备份任务
- `deploy/papermind-backup.timer`：数据库日备份定时器
- `deploy/backup.sh`：SQLite 备份脚本
- `deploy/nginx-papermind.conf`：nginx 站点配置

首次部署示例：

```bash
sudo -E bash /opt/papermind/deploy/setup.sh
sudo nano /opt/papermind/papermind/.env
sudo systemctl restart papermind
journalctl -u papermind -f
```

首次部署前需设置 `PREVIEW_HOST`、`PAPERMIND_BASIC_AUTH_USER`、`PAPERMIND_BASIC_AUTH_PASSWORD`；部署脚本不会替代权属确认或新仓库创建。首次生成的 Nginx 配置开启 Basic Auth；既有站点的认证和 HTTPS 配置不会被更新脚本覆盖。没有域名/TLS 时只通过 SSH 隧道验收，不直接分发明文 HTTP 试用链接。服务器脚本尚未在本轮实际执行。

常用线上命令：

```bash
sudo systemctl start papermind-backup
ls -lh /opt/papermind/backups
systemctl status papermind-backup.timer
```

## 项目结构

```
papermind/
├── papermind/              # Python 后端
│   ├── api.py              # FastAPI 主入口
│   ├── .env                # API Keys（不提交 git）
│   ├── src/
│   │   ├── fetch_papers.py         # PubMed 检索
│   │   ├── fetch_semantic_scholar.py
│   │   ├── categorize_papers.py    # LLM 动态打分分类
│   │   └── database.py             # SQLite 数据层（用户隔离）
│   └── data/               # 数据库文件
└── web/                    # React 前端
    └── src/
        ├── api.js           # 统一 API 请求（自动注入用户 ID）
        ├── pages/           # 页面组件
        └── components/      # 通用组件
```

## 许可

PaperMind 主项目原创代码采用 **GNU Affero General Public License 第 3 版，仅此版本（SPDX: `AGPL-3.0-only`）**，完整条款见 [LICENSE](LICENSE)。保留现有作者署名与贡献记录。

允许使用、修改、再分发和商用，须遵守许可中的版权声明、许可保留和对应源码提供要求。修改后以网络服务供用户使用时，须向这些用户显著提供免费取得该运行版本对应源码的方式；分发程序及修改版时，按许可履行相应义务。对应源码应包含构建、安装、运行及修改所需的代码和脚本。软件不提供担保，具体以许可正文为准。

`zotero-plugin/` 中 Connector 保留其自身 [AGPL-3.0-only 许可](zotero-plugin/LICENSE)。第三方代码与资源遵循各自许可，见 [第三方声明](THIRD_PARTY_NOTICES.md)。模型 API 账户、密钥、用户数据库、上传论文和问卷不在源码发布范围内，也不因本许可获得再分发授权。

公开或部署修改版前，应为实际运行版本提供可访问的源码地址或下载入口，不能用私有仓库链接代替。本批改动仅进入私有验收，不代表旧仓库没有公开历史；发布步骤见 [开源准备说明](docs/open-source-readiness.md#维护者发布顺序)。
