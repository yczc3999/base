#!/usr/bin/env bash
# 一键启动前后端 + worker
#   - 后端 uvicorn: 从 9000 起找空闲端口（dev 单进程 --reload；PROD=1 时 --workers 4）
#   - 前端 vite:    从 9200 起找空闲端口
#   - Worker:       SEO 4 任务 + 其他定时
#   - 两端都绑 0.0.0.0，输出局域网 IP
#   - Ctrl+C 同时 kill 三端

set -e
cd "$(dirname "$0")"

API_PORT=9000
ADMIN_PORT=9200

# ---- 端口被占就杀掉占用进程（保证固定端口可用）----
free_port() {
  local p=$1
  local pids
  pids=$(ss -lntp "sport = :$p" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
  [ -z "$pids" ] && return 0
  echo "[port] $p 被占（PID: $(echo "$pids" | tr '\n' ' ')），终止后重用" >&2
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i=0
  while [ $i -lt 10 ] && ss -lnt "sport = :$p" 2>/dev/null | grep -q LISTEN; do
    sleep 0.3; i=$((i+1))
  done
  if ss -lnt "sport = :$p" 2>/dev/null | grep -q LISTEN; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.5
  fi
}

lan_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || echo "0.0.0.0"
}

free_port "$API_PORT"
free_port "$ADMIN_PORT"
IP=$(lan_ip)

echo ""
echo "==============================="
echo "  Base Framework  dev 启动"
echo "==============================="
echo "  后端 API   → http://${IP}:${API_PORT}    (docs: /docs)"
echo "  前端 Admin → http://${IP}:${ADMIN_PORT}"
echo "  Worker    → SEO 4 任务 + 其他定时"
echo "==============================="
echo ""

# ---- 后端 ----
(
  cd serve
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if [ "$PROD" = "1" ]; then
    exec uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" --workers 4 --timeout-graceful-shutdown 30
  else
    exec uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" --reload
  fi
) &
BACKEND_PID=$!

# ---- Worker（SEO 自动发布依赖）----
(
  cd serve
  # shellcheck disable=SC1091
  source .venv/bin/activate
  exec python -m app.worker
) &
WORKER_PID=$!

# ---- 前端 ----
(
  cd admin
  API_PORT="$API_PORT" ADMIN_PORT="$ADMIN_PORT" exec npm run dev
) &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "[stop] 关闭前后端 + worker..."
  kill "$BACKEND_PID" "$WORKER_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

wait
