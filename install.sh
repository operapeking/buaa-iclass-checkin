#!/usr/bin/env bash
set -euo pipefail

# BUAA iClass Checkin 一键安装脚本
# 安装依赖 + 配置每日 07:00 查询课表的 cron

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/iclass_checkin.py"
CONFIG_PATH="$SCRIPT_DIR/config.json"
LOG_FILE="$SCRIPT_DIR/state/iclass-checkin.log"
CRON_MARKER="buaa-iclass-checkin-daily-query"

log() { echo "[install] $*"; }

# ─── Python / 依赖 ─────────────────────────────────────

find_python() {
    local candidates=()
    [[ -n "${PYTHON_BIN:-}" ]] && candidates+=("$PYTHON_BIN")
    [[ -n "${VIRTUAL_ENV:-}" ]] && candidates+=("$VIRTUAL_ENV/bin/python3" "$VIRTUAL_ENV/bin/python")
    candidates+=("$(command -v python3 2>/dev/null || true)")
    candidates+=("$(command -v python 2>/dev/null || true)")
    candidates+=("$HOME/.hermes/hermes-agent/venv/bin/python3")

    for py in "${candidates[@]}"; do
        [[ -x "$py" ]] || continue
        if "$py" -c "import requests, bs4" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done

    for py in "${candidates[@]}"; do
        [[ -x "$py" ]] || continue
        if "$py" -m pip install requests beautifulsoup4 >/dev/null 2>&1 && \
           "$py" -c "import requests, bs4" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done

    return 1
}

log "检查 Python 依赖..."
PYTHON_BIN="$(find_python)" || {
    log "ERROR: 找不到可用 Python，或无法安装依赖 requests/beautifulsoup4"
    log "可尝试手动指定: PYTHON_BIN=/path/to/python bash install.sh"
    exit 1
}
log "✓ 使用 Python: $PYTHON_BIN"

# ─── 检查配置 ──────────────────────────────────────────

if [[ ! -f "$CONFIG_PATH" ]]; then
    log "ERROR: 配置文件不存在: $CONFIG_PATH"
    log "请参考 config.json.example 创建配置文件"
    exit 1
fi

# 验证 JSON
if ! "$PYTHON_BIN" -c "import json; json.load(open('$CONFIG_PATH'))" 2>/dev/null; then
    log "ERROR: 配置文件不是有效 JSON: $CONFIG_PATH"
    exit 1
fi

log "✓ 配置文件已就绪: $CONFIG_PATH"

# ─── 设置 cron ──────────────────────────────────────────

log "配置 crontab (每天 07:00 查询课表)..."

cron_cmd="0 7 * * * $PYTHON_BIN $SCRIPT_PATH --query --config $CONFIG_PATH --state-dir $SCRIPT_DIR/state >> $LOG_FILE 2>&1  # $CRON_MARKER"

# 清除旧的
existing=$(crontab -l 2>/dev/null || true)
new_crontab=$(echo "$existing" | grep -v "$CRON_MARKER" || true)
new_crontab="$new_crontab
$cron_cmd"

echo "$new_crontab" | crontab - 2>/dev/null || {
    log "WARNING: 写入 crontab 失败，请手动添加:"
    log "  crontab -e"
    log "  $cron_cmd"
}

log ""
log "========================================="
log "  ✓ 安装完成！"
log "========================================="
log ""
log "  配置: $CONFIG_PATH"
log "  日志: $LOG_FILE"
log ""
log "  手动测试:"
log "    $PYTHON_BIN $SCRIPT_PATH --query"
log ""
log "  查看定时任务:"
log "    $PYTHON_BIN $SCRIPT_PATH --show-cron"
log ""
log "  清除所有任务:"
log "    $PYTHON_BIN $SCRIPT_PATH --clear-cron"
log ""
