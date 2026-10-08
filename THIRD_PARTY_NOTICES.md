# Third Party Notices

PaperMind 主项目原创代码采用 [AGPL-3.0-only](LICENSE)。本文件列明第三方依赖和资源；它们仍遵循各自许可。前端版本以 `web/package-lock.json` 为准；后端以 Python 3.12 生成的 `papermind/requirements.lock` 为准，`requirements.txt` 用于声明直接依赖范围。正式分发构建产物时，还需随包保留实际安装依赖（含间接依赖）的版权、LICENSE 和必要的 NOTICE 文本。

## 自动收集实际安装环境

按锁文件安装后运行 `papermind/.venv_new/bin/python scripts/collect_third_party.py --output /新的受控目录/notices`。输出版本清单、许可文本副本、缺失全文及未安装平台包清单；不会打包模型配置、用户文件或浏览器。2026-09-28 的 macOS 环境收集到 360 包记录、303 份文本；61 个可选/平台包未安装。5 个已安装条目未携带独立全文：`@humanfs/types`、`@napi-rs/canvas-darwin-arm64`、`@rolldown/binding-darwin-arm64`、`keyv`、`natural-compare`。收集器没有据 SPDX 名称自行编造版权声明。

其中 canvas 平台包为浏览器阅读器未使用的可选 Node 原生包，其余 4 个属于开发/构建链；纯源码导出不携带这些安装目录。若以后分发 node_modules、venv 或原生安装包，应针对目标平台补齐上游正文及二进制内嵌第三方声明，不能把本机自动收集清单当作法律兼容性结论。Connector 保留自身完整 LICENSE；字体仍外部加载，未复制字体文件。

## 前端直接运行依赖

| 库 | 许可证 | 用途 |
|---|--------|------|
| [react / react-dom](https://github.com/facebook/react) | MIT | UI 与 DOM 渲染 |
| [react-router-dom](https://github.com/remix-run/react-router) | MIT | 路由 |
| [react-markdown](https://github.com/remarkjs/react-markdown) | MIT | Markdown 渲染 |
| [pdfjs-dist](https://github.com/mozilla/pdf.js) | Apache-2.0 | 6.2.108 legacy PDF 主库、worker 和文字层 |
| [@sentry/react](https://github.com/getsentry/sentry-javascript) | MIT | 可选错误监控与回放 |
| [lucide-react](https://github.com/lucide-icons/lucide) | ISC | 图标 |

## 前端直接开发依赖

以下包的当前锁文件许可字段均为 MIT：

- `vite`、`@vitejs/plugin-react`：构建与 React 转换。
- `tailwindcss`、`@tailwindcss/vite`：样式构建。
- `@types/react`、`@types/react-dom`：类型定义。
- `eslint`、`@eslint/js`、`eslint-plugin-react-hooks`、`eslint-plugin-react-refresh`、`globals`：静态检查。

另有 `playwright` / `playwright-core`（1.62.1，Apache-2.0）用于合成 PDF 浏览器回归，仅作为开发/测试依赖；测试浏览器另行下载，未放入前端构建产物或源码发布包。

这些条目仅覆盖直接依赖，不代表已经收齐间接依赖的许可证。

PDF.js 同时声明可选 Node 依赖 `@napi-rs/canvas`（当前锁定 1.0.9，MIT）及平台二进制包；本阅读器使用浏览器 canvas，不把 Node 原生模块作为运行路径。若另行分发 `node_modules` 或原生二进制，仍须核对它们携带的第三方许可，不能以此清单代替完整声明。

构建链间接依赖 [source-map-js](https://github.com/7rulnik/source-map-js) 为 BSD-3-Clause；2026-10-08 将锁定版本从 1.2.1 更新到上游修正版 1.2.2，处理 GHSA-68fv-2mgg-jv7q。现有纯源码导出不带安装目录；构建/安装包仍需保留其实际许可正文。9 月 28 日收集记录是当时环境，不代表后续依赖版本未变化。

## 后端直接依赖

| 库 | 许可证 | 用途 |
|---|--------|------|
| [FastAPI](https://github.com/tiangolo/fastapi) | MIT | Web 框架 |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI 服务器（使用 standard extras） |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | 配置加载 |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | 请求校验 |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | HTTP 客户端 |
| [httpcore](https://github.com/encode/httpcore) | BSD-3-Clause | PDF 代理连接阶段校验与地址固定 |
| [openai (Python SDK)](https://github.com/openai/openai-python) | Apache-2.0 | LLM API 调用 |
| [requests](https://github.com/psf/requests) | Apache-2.0 | HTTP 请求 |
| [sentry-sdk](https://github.com/getsentry/sentry-python) | MIT | 可选后端错误监控 |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | 文件上传解析 |
| [python-pptx](https://github.com/scanny/python-pptx) | MIT | PowerPoint 导出 |

`python-pptx` 使用 Pillow、lxml、XlsxWriter 等间接依赖；`httpx[socks]` 引入 socksio。Uvicorn extras、FastAPI、HTTP 和模型 SDK 也带入间接依赖，二进制发布前须按实际环境核对。不能把“主依赖的许可证”视为整个分发包的统一许可证。

## 外部 API

| 服务 | 用途 | 许可 |
|------|------|------|
| [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/) | PubMed 文献检索 | 遵守服务条款、请求限流和数据使用条件 |
| [Semantic Scholar API](https://www.semanticscholar.org/product/api) | 学术论文检索 | 遵守实际账户和数据使用条款 |
| [Unpaywall API](https://unpaywall.org/products/api) | 开放获取 PDF 查找 | 遵守 API 与数据使用条款 |

通义、智谱、DeepSeek 及部署者配置的 OpenAI 兼容服务适用实际账户的收费、数据处理与模型条款。SDK 的开源许可不授予外部模型服务的使用权。Sentry SDK 的许可也不代替托管诊断服务条款。

## 字体

| 字体 | 许可证 | 用途 |
|------|--------|------|
| [DM Sans](https://github.com/google/fonts/tree/main/ofl/dmsans) | SIL OFL 1.1 | 正文字体 |
| [Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) | SIL OFL 1.1 | 中文正文 |
| [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc) | SIL OFL 1.1 | 标题衬线字体 |

`web/index.html` 通过 `fonts.googleapis.cn` / `fonts.gstatic.cn` 请求字体，浏览器会联系这些服务；关闭 Sentry 不等于网页完全不发生第三方请求。离线部署可改用系统字体；若打包字体文件，需附带相应 OFL 文本。

## 文献内容与 Zotero Connector

用户上传的论文、截图和导出内容仍受其原有权利与保密约束。能阅读或查到开放获取地址，不自动意味着可以随源码包再分发。

`zotero-plugin/` 保留该目录的 AGPL-3.0-only `LICENSE`；独立发布源为 [papermind-zotero](https://github.com/lvyrana/papermind-zotero)。主项目的许可由作者于 2026-09-28 单独确认，本次不更改 Connector 的既有许可或发布包。
