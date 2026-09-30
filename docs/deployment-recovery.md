# PaperMind 部署、试用准入与恢复

更新：2026-09-28。本地未发布改动；服务器尚未更新。唯一开源进度入口为 [open-source-readiness.md](open-source-readiness.md)。

## 环境与检查

后端使用 Python 3.12，Linux/macOS 的文件锁提供跨进程预算与上传配额互斥；Windows 服务端使用 WSL2。前端使用 Node 22.13+（22.x）或 Node 24.x LTS。浏览器端不需要这些运行时。

```bash
python3.12 -m venv papermind/.venv_new
papermind/.venv_new/bin/python -m pip install --upgrade pip==26.2.1
papermind/.venv_new/bin/python -m pip install --require-hashes -r papermind/requirements.lock
(cd web && npm ci --ignore-scripts)
papermind/.venv_new/bin/python scripts/deployment_preflight.py
papermind/.venv_new/bin/python scripts/test_backend_isolated.py
papermind/.venv_new/bin/python scripts/smoke_backend.py
(cd web && npm test && npm run build && npm run lint && npm audit)
```

`requirements.lock` 在 Python 3.12/macOS 生成，固定 37 个运行依赖并保留发行包哈希；Linux CI 仍须验证。更新依赖先修改 `requirements.txt`，再用 Python 3.12 的 pip-tools 重新生成锁文件并重跑上述检查，不直接修改哈希来绕过安装失败。代码测试和依赖审计使用不同隔离环境，不把审计工具带入运行锁文件。

## 给现有试用者保留身份

现有设备 UUID 继续作为数据归属与私有链接凭证，知道它的人可以访问相应设备数据。邀请表只存 UUID 的哈希、到期时间和撤销状态。它提供准入控制，不是邮箱登录、身份实名核验或设备绑定。

1. 从现有试用者的设置页私下取得设备 ID；不截图发群、不复制到代码或 Issue。
2. 在实际服务主机上逐个执行 `python3 scripts/manage_trial.py extend --days 14`，按隐藏提示粘贴旧 UUID。不要发新 UUID 替换旧设备，否则看不到原来的书架。
3. 确认 `.env` 中维护者 `OWNER_UID` 正确，设 `PAPERMIND_INVITE_ONLY=true`，重启后用已授权和未授权设备各验收一次。
4. 新用户执行 `python3 scripts/manage_trial.py issue --days 14`，将生成的 UUID 放入服务 URL 的 `?uid=`，一人一条、私下发放。退出或撤销执行 `python3 scripts/manage_trial.py revoke`。

邀请模式默认关闭，不会擅自锁掉已有试用者。开启后，除公开的两条 Zotero 插件分发接口与 CORS OPTIONS 外，所有 `/api/` 路径都执行准入检查；有效 owner 保留维护入口。缺失、损坏、过期或撤销的授权默认拒绝。撤销从下一次请求起生效，不会追回已经送给第三方服务的内容或取消已经接受的后台任务。关闭邀请模式会重新允许任何有效设备 UUID，不能把它当作个别用户的恢复手段。

## 资源限制

| 配置 | 默认 | 实际含义 |
|---|---:|---|
| `GLOBAL_DAILY_AI_REQUEST_LIMIT` | 500 | 每 UTC 日全服务供应商调用尝试数；失败与回退均计数，重启不清零 |
| `AI_MAX_CONCURRENT_REQUESTS` | 4 | 所有后端 worker 共用的 AI 调用名额；进程退出由系统释放 |
| `UPLOAD_TOTAL_LIMIT_MB` | 2048 | PDF 和图表合计的服务器配额 |
| `UPLOAD_USER_LIMIT_MB` | 250 | 一个设备所属论文的 PDF 和图表合计配额 |

这些配置均须为正整数。同步与异步模型调用共用同一预算；关闭 SDK 隐式重试，每次应用级回退重新计数。异步调用总超时 90 秒，同步调用受客户端网络超时约束。计数不是 token 或金额上限，服务商账户仍需设置实际费用额度。不要用多台服务器共享这些本地文件锁；当前方案面向一台机器上的多个进程。

PDF 代理只访问公开 HTTPS 443，连接时检查所有 DNS 地址并固定连接到已验证 IP，每次重定向重新校验；不使用环境代理、不转发设备身份或 cookie。最多 6 跳、下载 60 秒、50 MiB、每进程 4 个名额；名额和临时文件保留到浏览器接收完成，发送另有 60 秒上限。非 PDF 返回错误，不让带设备身份的请求自动跳到外站。失败保留页面的手动原文出口；上传的 PDF 和图表仍按所属设备校验。

## 备份和隔离恢复

```bash
bash scripts/backup_local.sh --with-files
python3 scripts/backup_data.py verify backups/snapshot-实际名称
python3 scripts/backup_data.py restore backups/snapshot-实际名称 --destination /受控目录/restored-data
```

快照包含 SQLite 在线备份、PDF、图表和已存在的 AI 日用量计数；文件有 SHA-256 清单，备份与恢复均检查 SQLite 完整性。目录 700、文件 600。检测到备份窗口内数据库或附件变化会失败，应低峰重试或停止后端后执行。旧 `.db.gz` 和旧快照保留，不自动清理；默认快照库上限 10 GiB（`PAPERMIND_BACKUP_MAX_MB`），并要求复制后至少保留约 512 MiB 磁盘余量。达到上限会停止新增备份，须人工核对并转移已验证的旧快照。

恢复目标必须尚不存在。禁止把单个 `.db` 直接覆盖运行中的数据库，尤其不能混入另一个日期的 `-wal/-shm`。正式切换前：

1. 停止后端及备份定时器，保存当前代码提交号，验证备份并恢复到新目录。
2. 私下重配 `.env` 和 `config.json`；它们不进入未加密快照。按当前试用同意状态重新授权已有 UUID，不能从旧邀请名单恢复撤销过的权限。
3. AI 计数只能恢复到快照时刻，之后的消耗无法由旧快照推断；同日恢复时保留当前可用计数数据库，或暂停模型服务至下一个 UTC 日/人工核定服务商额度后再开放。
4. 保留原数据目录作为回退，将新目录赋予服务用户，确认 owner、邀请和密钥设置后再切换。先验证自己的书架、PDF、图表和测试设备隔离，再重新开放。

本机演练快照为 `backups/snapshot-20260928T054923Z-e8d39b26`，14 个文件通过校验并恢复到独立临时目录；没有覆盖原数据。该演练不证明服务器定时器或异地备份已运行。真实快照和恢复副本含用户内容，不得上传公共仓库；独立恢复副本位于受限临时目录。

## ECS 更新与回退

部署模板固定 `/opt/papermind`、`ubuntu` 服务用户，面向 Ubuntu 24.04。首次安装默认源码地址为 `https://github.com/lvyrana/papermind-source.git`，不再从旧历史仓库安装。新仓库目前私有，服务主机需要已授权的 Git 访问方式；也可通过 `PAPERMIND_REPO_URL` 选择自己有权部署的仓库，不能把私有链接当作公众可直接克隆的入口。设置域名及 Basic Auth，新站点默认开启访问口令。配置/运行环境与源码安装在启动前检查。`.env` 必须是权限 600 的普通文件。

后续更新先拒绝脏工作树，再 fetch 并确认可快进；停止服务、做完整备份后才更新代码。按哈希锁文件安装依赖并运行后端回归；前端构建在新目录完成，成功后才换入 `web/dist`，旧构建保留。HTTPS 与既有认证配置不被整文件覆盖。启动后检查 API 和首页；失败保持停机，避免自动重新开放一个未验证版本。

这是有维护窗口的更新脚本，不是事务化零停机部署。失败后以终端记录的旧提交号、快照路径、旧 `web/.dist-previous.*` 为依据：先保存故障日志与现场；在独立目录重建旧提交的依赖和构建，按上节恢复/核验数据，再切换服务。不能只换回数据库而保留不匹配的新代码。不要在当前目录执行 `git reset --hard` 丢弃现场。

Linux systemd、Nginx、Certbot、定时器及公网模型连通性尚未在本轮实机验收；本地完成的是语法、隔离 API、数据恢复和浏览器验证。部署后还需检查备份服务退出码和异地副本。
