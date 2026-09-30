#!/bin/bash
# PaperMind 维护窗口更新：干净工作树、停止写入、验证备份、锁文件安装。
# 用法：sudo bash update.sh
set -euo pipefail

PROJECT_DIR="/opt/papermind"
DEPLOY_REF="${DEPLOY_REF:-main}"
OLD_COMMIT=""
SERVICE_STOPPED=false
MAINTENANCE_STARTED=false
on_failure() {
    local result=$?
    trap - ERR
    if [ "$MAINTENANCE_STARTED" = true ]; then
        systemctl stop papermind || true
        SERVICE_STOPPED=true
    fi
    echo "更新失败；旧提交 ${OLD_COMMIT:-unknown}。已生成的备份和旧构建均保留。后端停止状态：$SERVICE_STOPPED。按 docs/deployment-recovery.md 恢复，勿盲目重启。" >&2
    exit "$result"
}
trap on_failure ERR

if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本：sudo bash update.sh"
    exit 1
fi

cd "$PROJECT_DIR"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "工作树存在未提交修改；保留现场并退出。"
    exit 1
fi
[[ "$DEPLOY_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { echo "DEPLOY_REF 格式无效"; exit 1; }
OLD_COMMIT="$(git rev-parse HEAD)"
"$PROJECT_DIR/papermind/.venv/bin/python" scripts/deployment_preflight.py --server
echo "=== 获取目标提交；尚未改动运行版本 ==="
git fetch origin "$DEPLOY_REF"
git merge-base --is-ancestor HEAD FETCH_HEAD || { echo "目标不能快进；退出，未切换版本。"; exit 1; }

echo "=== 停止写入并创建验证备份 ==="
systemctl stop papermind
SERVICE_STOPPED=true
MAINTENANCE_STARTED=true
runuser -u ubuntu -- "$PROJECT_DIR/papermind/.venv/bin/python" "$PROJECT_DIR/scripts/backup_data.py" create
git merge --ff-only FETCH_HEAD

echo "=== 安装/更新 Python 依赖 ==="
cd "$PROJECT_DIR/papermind"
.venv/bin/python -m pip install --upgrade pip==26.2.1 -q
.venv/bin/python -m pip install --require-hashes -r requirements.lock -q
.venv/bin/python -m pip check
.venv/bin/python "$PROJECT_DIR/scripts/deployment_preflight.py" --server
.venv/bin/python "$PROJECT_DIR/scripts/test_backend_isolated.py"

echo "=== 构建前端 ==="
cd "$PROJECT_DIR/web"
npm ci --ignore-scripts --silent
npm test
npm run lint
BUILD_DIR="$(mktemp -d "$PROJECT_DIR/web/.dist-next.XXXXXX")"
npm run build -- --outDir "$BUILD_DIR"
chmod -R a+rX "$BUILD_DIR"
if [ -d dist ]; then
    mv dist "$PROJECT_DIR/web/.dist-previous.$(date +%s)"
fi
mv "$BUILD_DIR" dist

echo "=== 同步服务配置 ==="
cp "$PROJECT_DIR/deploy/papermind.service" /etc/systemd/system/papermind.service
cp "$PROJECT_DIR/deploy/papermind-backup.service" /etc/systemd/system/papermind-backup.service
cp "$PROJECT_DIR/deploy/papermind-backup.timer" /etc/systemd/system/papermind-backup.timer
chmod +x "$PROJECT_DIR/deploy/backup.sh"

ensure_spa_no_cache() {
    local conf="$1"
    [ -f "$conf" ] || return 0
    if grep -q 'Cache-Control "no-cache, no-store, must-revalidate"' "$conf"; then
        return 0
    fi
    cp "$conf" "$conf.bak.$(date +%s)"
    perl -0pi -e 's/location \/ \{\n\s*try_files \$uri \$uri\/ \/index\.html;\n\s*\}/location \/ {\n        add_header Cache-Control "no-cache, no-store, must-revalidate" always;\n        add_header Pragma "no-cache" always;\n        add_header Expires "0" always;\n        try_files \$uri \$uri\/ \/index.html;\n    }/g' "$conf"
}

ensure_spa_no_cache /etc/nginx/sites-available/papermind
ensure_spa_no_cache /etc/nginx/sites-available/papermind-domain
systemctl daemon-reload
systemctl enable papermind-backup.timer >/dev/null 2>&1 || true
systemctl restart papermind-backup.timer
nginx -t
systemctl reload nginx

echo "=== 重启后端 ==="
systemctl restart papermind
for attempt in {1..10}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:8000/api/zotero-plugin/update.json >/dev/null \
        && curl --fail --silent --max-time 2 http://127.0.0.1:8000/ >/dev/null; then
        SERVICE_STOPPED=false
        break
    fi
    sleep 1
done
if [ "$SERVICE_STOPPED" = true ]; then
    systemctl stop papermind
    echo "HTTP 健康检查失败，后端已停止；保留日志与备份。"
    exit 1
fi
systemctl is-active --quiet papermind
echo "=== 备份定时器 ==="
systemctl is-active --quiet papermind-backup.timer

echo "更新完成；请验证阅读、上传和已授权的试用链接。旧提交：$OLD_COMMIT"
