#!/usr/bin/env bash
# Author: InstaBox Tool 用户扩展
# Managed-by: InstaBox Tool CLI
# ============================================================
# 定时重启任务（变相持久化）
# 在【当前机器 A】上，每 INTERVAL_HOURS 小时执行一轮：
#   ① 暂停【所选机器 B】  ② 等待 DELAY_SECONDS 秒  ③ 启动机器 B
# 通过周期性地"暂停→启动"机器 B，让它持续保持可用（避免空闲暂停 / 长驻判定）。
# 使用现有 `ibt cloud pause/start` 接口，按机器 ID 非交互操作。
# 子命令：setup | run | loop | start | stop | status | logs | remove
# ============================================================
set -uo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

APP="$HOME/.local/share/insta-box"
CFG="$HOME/.config/insta-box"
CONF="$CFG/cycle.conf"          # 配置：MACHINE_ID / INTERVAL_HOURS / DELAY_SECONDS
LOG="$CFG/cycle.log"            # 运行日志
UNIT='insta-box-cycle'          # systemd 用户服务名
BIN="$HOME/.local/bin/ibt"      # 管理入口

say() { printf '  %s\n' "$*"; }
error() { printf '  %s[失败]%s %s\n' '\033[31m' '\033[0m' "$*" >&2; }
ask() { local a; read -r -p "  $1 " a </dev/tty || return 1; printf '%s' "$a"; }
confirm() { local a; a="$(ask "$1 [y/N]：")" || return 1; [[ ${a,,} == y || ${a,,} == yes ]]; }
log() { printf '[%(%F %T)T] %s\n' -1 "$*" >> "$LOG"; }

load_conf() {
  [[ -s "$CONF" ]] || { error '尚未配置定时重启任务，请先运行 ibt cycle setup。'; return 1; }
  # shellcheck disable=SC1090
  source "$CONF"
  [[ -n "${MACHINE_ID:-}" && "$MACHINE_ID" == m_* ]] \
    || { error '配置中的机器 ID 无效，请重新运行 ibt cycle setup。'; return 1; }
  INTERVAL_HOURS="${INTERVAL_HOURS:-7}"
  DELAY_SECONDS="${DELAY_SECONDS:-10}"
}

# ---- 执行一轮：暂停 B → 等待 → 启动 B ----
do_cycle() {
  load_conf || return 1
  say "开始一轮：暂停 ${MACHINE_ID} → ${DELAY_SECONDS} 秒后启动"
  log "cycle: 暂停机器 ${MACHINE_ID}"
  if "$BIN" cloud pause "$MACHINE_ID" --yes --wait 120 >>"$LOG" 2>&1; then
    log "cycle: 已暂停 ${MACHINE_ID}"
  else
    log "cycle: 暂停未确认 ${MACHINE_ID}（可能已暂停/停止，继续等待后启动）"
  fi
  sleep "$DELAY_SECONDS"
  log "cycle: 启动机器 ${MACHINE_ID}"
  if "$BIN" cloud start "$MACHINE_ID" --wait 120 >>"$LOG" 2>&1; then
    log "cycle: 已启动 ${MACHINE_ID}"
  else
    log "cycle: 启动失败 ${MACHINE_ID}，请查看日志"
  fi
  say "本轮完成，日志：$LOG"
}

# ---- 常驻循环（供 systemd 服务调用；精确每 INTERVAL_HOURS 小时）----
loop() {
  while :; do
    do_cycle
    log "cycle: 等待 ${INTERVAL_HOURS} 小时后进入下一轮"
    sleep "$((INTERVAL_HOURS * 3600))"
  done
}

# ---- 写入 systemd 用户服务 ----
install_service() {
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/$UNIT.service" <<EOF
[Unit]
Description=InstaBox Tool machine cycle (pause -> start)
After=network-online.target

[Service]
Type=simple
ExecStart=$BIN cycle loop
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload 2>/dev/null
}

# ---- 交互式配置 ----
setup() {
  say '选择要被循环的【机器 B】（在 A 机器上运行本任务，周期暂停+启动 B）。'
  say '当前授权可见的机器如下：'
  "$BIN" cloud list || { error '无法读取机器列表，请先检查 API Key（ibt key status）。'; return 1; }
  say ''
  local mid hours delay
  mid="$(ask '输入机器 B 的完整 ID（m_ 开头）：')" || return 1
  [[ "$mid" == m_* ]] || { error '机器 ID 格式无效。'; return 1; }
  hours="$(ask '执行间隔（小时，默认 7）：')"; hours="${hours:-7}"
  delay="$(ask '暂停后等待秒数（默认 10）：')"; delay="${delay:-10}"
  [[ "$hours" =~ ^[0-9]+$ && "$hours" -ge 1 ]] || { error '间隔必须是不小于 1 的整数。'; return 1; }
  [[ "$delay" =~ ^[0-9]+$ ]] || { error '等待秒数必须是非负整数。'; return 1; }
  printf 'MACHINE_ID=%s\nINTERVAL_HOURS=%s\nDELAY_SECONDS=%s\n' "$mid" "$hours" "$delay" > "$CONF"
  chmod 600 "$CONF"
  install_service
  say '已保存配置并安装常驻服务。运行 ibt cycle start 启动任务。'
}

start_service() {
  load_conf || return 1
  install_service
  # 允许开机/登录自启（配合 linger）
  loginctl enable-linger "$(id -un)" 2>/dev/null || true
  systemctl --user enable --now "$UNIT.service" >/dev/null 2>&1 \
    && say '任务服务已启动，并按每 INTERVAL_HOURS 小时循环。' \
    || error '启动失败，请检查用户级 systemd。'
}

stop_service() {
  systemctl --user disable --now "$UNIT.service" >/dev/null 2>&1 || true
  say '任务服务已停止。'
}

status() {
  say '—— 定时重启任务状态 ——'
  if [[ -s "$CONF" ]]; then
    # shellcheck disable=SC1090
    source "$CONF"
    say "目标机器 B：${MACHINE_ID}"
    say "执行间隔：  ${INTERVAL_HOURS:-7} 小时"
    say "暂停后等待：${DELAY_SECONDS:-10} 秒"
  else
    say '尚未配置（ibt cycle setup）。'
  fi
  local active
  active="$(systemctl --user is-active "$UNIT.service" 2>/dev/null || echo inactive)"
  say "任务服务：  $active"
  [[ -s "$LOG" ]] && say "日志：      $LOG"
}

remove() {
  confirm '删除定时重启任务（含配置与常驻服务）？' || { say '已取消。'; return 1; }
  systemctl --user disable --now "$UNIT.service" >/dev/null 2>&1 || true
  rm -f "$HOME/.config/systemd/user/$UNIT.service" "$HOME/.config/systemd/user/$UNIT.timer"
  systemctl --user daemon-reload 2>/dev/null
  rm -f "$CONF" "$LOG"
  say '已删除定时重启任务。'
}

case "${1:-status}" in
  setup)  setup ;;
  run)    do_cycle ;;
  loop)   loop ;;
  start)  start_service ;;
  stop)   stop_service ;;
  status) status ;;
  logs)   [[ -s "$LOG" ]] && tail -n 60 "$LOG" || say '暂无日志。' ;;
  remove) remove ;;
  *) error '用法：ibt cycle setup|run|start|stop|status|logs|remove' >&2; exit 2 ;;
esac
#（注：内容由AI生成）
