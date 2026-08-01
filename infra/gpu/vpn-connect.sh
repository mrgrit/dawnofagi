#!/usr/bin/env bash
# 사내 GPU 서버로 가는 VPN(GlobalProtect) 연결.
#
# ⚠️ 이 스크립트는 **사람이 실행한다.** 에이전트는 VPN 을 붙이지 않는다 —
#    네트워크 경계 변경은 비가역 행동이고 sec.firewall_change 급으로 다룬다.
#
#   ./infra/gpu/vpn-connect.sh            연결 (스플릿 라우팅)
#   ./infra/gpu/vpn-connect.sh --status   상태
#   ./infra/gpu/vpn-connect.sh --down     해제
#
# 스플릿 라우팅을 쓰는 이유: 이 호스트는 el34 랩(10.20.30/31/32/40.0/24, 40+ 컨테이너)을
# 돌리고 있다. 풀터널로 붙으면 기본 경로가 VPN 으로 넘어가 랩이 끊긴다.
# vpn-slice 로 **GPU 호스트 한 대만** VPN 경로로 보낸다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

GPU_HOST="${GPU_HOST:-211.170.162.109}"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi
ok(){ printf '%s✔%s %s\n' "$G" "$Z" "$*"; }
warn(){ printf '%s!%s %s\n' "$Y" "$Z" "$*"; }
die(){ printf '%s✘%s %s\n' "$R" "$Z" "$*" >&2; exit 1; }

status() {
  if pgrep -a openconnect >/dev/null 2>&1; then
    ok "openconnect 실행 중"
    pgrep -a openconnect | sed 's/--passwd-on-stdin/[pw]/' | sed 's/^/    /'
  else
    warn "openconnect 미실행"
  fi
  printf '\n%s GPU 호스트 경로%s\n' "$B" "$Z"
  ip route get "$GPU_HOST" 2>&1 | head -1 | sed 's/^/    /'
  printf '\n%s el34 랩 경로 (끊기면 안 됨)%s\n' "$B" "$Z"
  ip route get 10.20.32.55 2>&1 | head -1 | sed 's/^/    /'
  echo
  "$ROOT/.venv/bin/python" infra/gpu/check.py || true
}

case "${1:-up}" in
  --status|status) status; exit 0 ;;
  --down|down)
    pgrep openconnect >/dev/null 2>&1 || { warn "연결돼 있지 않다"; exit 0; }
    sudo pkill -SIGINT openconnect && sleep 2
    ok "VPN 해제"
    ip route get 10.20.32.55 >/dev/null 2>&1 && ok "el34 경로 정상"
    exit 0 ;;
esac

# ── 연결 ────────────────────────────────────────────────────────────────
[[ -f .env ]] || die ".env 가 없다. cp .env.example .env 후 값을 채워라."
set -a; source .env; set +a

: "${VPN_PORTAL:?.env 에 VPN_PORTAL 이 없다}"
: "${VPN_USER:?.env 에 VPN_USER 가 없다}"
: "${VPN_PASSWORD:?.env 에 VPN_PASSWORD 가 없다}"

command -v openconnect >/dev/null || die "openconnect 가 없다:  sudo apt-get install -y openconnect"
SLICE="$ROOT/.venv/bin/vpn-slice"
[[ -x "$SLICE" ]] || die "vpn-slice 가 없다:  .venv/bin/pip install vpn-slice"

if pgrep openconnect >/dev/null 2>&1; then
  warn "이미 연결돼 있다. 다시 붙이려면 --down 먼저."
  status
  exit 0
fi

ARGS=(--protocol=gp --user="$VPN_USER" --passwd-on-stdin --background
      --script="$SLICE $GPU_HOST" )
if [[ -n "${VPN_SERVERCERT:-}" ]]; then
  ARGS+=(--servercert "$VPN_SERVERCERT")
else
  warn "VPN_SERVERCERT 가 없다 — 첫 연결 시 openconnect 가 알려주는 pin-sha256 을 .env 에 넣어라"
fi

printf '%sVPN 연결 중%s  포털=%s  사용자=%s  (스플릿: %s 만)\n' \
  "$B" "$Z" "$VPN_PORTAL" "$VPN_USER" "$GPU_HOST"

printf '%s\n' "$VPN_PASSWORD" | sudo openconnect "${ARGS[@]}" "$VPN_PORTAL" 2>&1 \
  | grep -vE 'passwd|password' | tail -8 || true

sleep 4
status
