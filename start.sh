#!/usr/bin/env bash
# 一键启动前后端 + worker（dev 模式）
#   - 后端 uvicorn: 从 9000 起找空闲端口
#   - 前端 vite:    从 9200 起找空闲端口
#   - Worker:       SEO 4 任务 + 其他定时
#   - 两端都绑 0.0.0.0，输出局域网 IP
#   - Ctrl+C 同时 kill 三端

set -e
cd "$(dirname "$0")"

API_PORT_BASE=9000
ADMIN_PORT_BASE=9200

pick_port() {
  local p=$1
  while ss -lnt "sport = :$p" 2>/dev/null | grep -q LISTEN; do
    p=$((p + 1))
  done
  echo "$p"
}

lan_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || echo "0.0.0.0"
}

API_PORT=$(pick_port $API_PORT_BASE)
ADMIN_PORT=$(pick_port $ADMIN_PORT_BASE)
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
  exec uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" --reload
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
