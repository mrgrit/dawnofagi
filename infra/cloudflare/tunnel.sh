#!/usr/bin/env bash
# Cloudflare Tunnel — 이 호스트의 서비스를 외부 URL 로 연다.
#
# ⚠️ 이 스크립트는 **사람이 실행한다.** 에이전트는 외부 노출을 만들지 않는다 —
#    공개 범위 변경은 `comm.external_send`·`sec.firewall_change` 와 같은 급이다.
#
#   ./infra/cloudflare/tunnel.sh                 공개 홈페이지(:8810) 를 연다  ← 기본
#   ./infra/cloudflare/tunnel.sh --target portal 사내 그룹웨어(:8811)
#   ./infra/cloudflare/tunnel.sh --target office 픽셀 오피스(:8800)  ⚠ 인증 없음
#   ./infra/cloudflare/tunnel.sh --status        지금 열려 있는 것
#   ./infra/cloudflare/tunnel.sh --down          전부 닫는다
#
# 기본이 홈페이지인 이유: **셋 중 유일하게 공개용으로 설계된 것**이다.
#   :8810 공개 홈페이지   인증 없음 — 원래 외부인이 보는 화면 (zone:ext / asset:site)
#   :8811 사내 그룹웨어   로그인 필요 — 승인 큐·EG 조정. 열면 무차별 대입 표적이 된다
#   :8800 픽셀 오피스     **인증 없음** — 전 에이전트 텔레메트리·케이스 제목·자산 이름이
#                        그대로 보인다. 인터넷에 열면 회사 내부 구조를 공개하는 것이다
#
# 여기서 만드는 것은 **퀵 터널**이다 (계정 불필요, 임의 URL, 프로세스가 죽으면 사라짐).
# 고정 도메인이 필요하면 아래 "고정 URL" 절 참조.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
RUN="$ROOT/var/cloudflare"
mkdir -p "$RUN"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; D=$'\033[2m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; D=""; Z=""; fi
ok(){   printf '%s✔%s %s\n' "$G" "$Z" "$*"; }
warn(){ printf '%s!%s %s\n' "$Y" "$Z" "$*"; }
die(){  printf '%s✘%s %s\n' "$R" "$Z" "$*" >&2; exit 1; }

TARGET="site"
FORCE=0
ACTION="up"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2;;
    --yes|-y) FORCE=1; shift;;
    --status) ACTION="status"; shift;;
    --down)   ACTION="down"; shift;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "알 수 없는 인자: $1";;
  esac
done

case "$TARGET" in
  site)   PORT=8810; NAME="공개 홈페이지";   AUTH="없음 (설계상 공개)";;
  portal) PORT=8811; NAME="사내 그룹웨어";   AUTH="로그인 필요";;
  office) PORT=8800; NAME="픽셀 오피스";     AUTH="${R}없음${Z}";;
  *) die "--target 은 site|portal|office (받은 값: $TARGET)";;
esac

PIDF="$RUN/$TARGET.pid"
LOGF="$RUN/$TARGET.log"
URLF="$RUN/$TARGET.url"

# ── 상태 ────────────────────────────────────────────────────────────────
status() {
  local any=0
  printf '%sCloudflare 터널%s\n' "$B" "$Z"
  for t in site portal office; do
    local p="$RUN/$t.pid"
    [[ -s "$p" ]] || continue
    if kill -0 "$(cat "$p")" 2>/dev/null; then
      any=1
      printf '  %-7s pid %-8s %s\n' "$t" "$(cat "$p")" "$(cat "$RUN/$t.url" 2>/dev/null || echo '(URL 대기 중)')"
    else
      rm -f "$p"
    fi
  done
  [[ $any -eq 1 ]] || printf '  %s열려 있는 터널 없음%s\n' "$D" "$Z"
}

down() {
  local n=0
  for t in site portal office; do
    local p="$RUN/$t.pid"
    [[ -s "$p" ]] || continue
    if kill "$(cat "$p")" 2>/dev/null; then n=$((n+1)); fi
    rm -f "$p" "$RUN/$t.url"
  done
  [[ $n -gt 0 ]] && ok "터널 $n 개 닫음" || warn "닫을 터널이 없다"
}

case "$ACTION" in
  status) status; exit 0;;
  down)   down;   exit 0;;
esac

# ── 사전 확인 ───────────────────────────────────────────────────────────
command -v cloudflared >/dev/null 2>&1 \
  || die "cloudflared 가 없다. 설치: https 로 .deb 를 받아 dpkg -i (infra/cloudflare/README.md)"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/" || echo 000)
[[ "$code" =~ ^(200|30[0-9])$ ]] \
  || die "$NAME(:$PORT) 가 응답하지 않는다 (HTTP $code). 먼저 기동하라 — make web-bg / make office-bg"

if [[ -s "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  warn "이미 열려 있다: $(cat "$URLF" 2>/dev/null)"
  exit 0
fi

# 인증 없는 화면을 인터넷에 여는 것은 되돌리기 어려운 노출이다 — 사람이 한 번 더 확인한다.
if [[ "$TARGET" == "office" && $FORCE -eq 0 ]]; then
  printf '%s⚠ 픽셀 오피스는 인증이 없다.%s\n' "$R" "$Z"
  printf '  전 에이전트의 텔레메트리·케이스 제목·자산 이름·조직 구조가 그대로 보인다.\n'
  printf '  인터넷에 열면 회사 내부 구조를 공개하는 것이다.\n'
  printf '  URL 을 아는 사람은 누구나 볼 수 있다 (Cloudflare 는 인증을 걸지 않는다).\n\n'
  read -r -p "  정말 열겠나? 'open' 을 입력: " a
  [[ "$a" == "open" ]] || die "취소했다"
fi
if [[ "$TARGET" == "portal" ]]; then
  warn "그룹웨어를 외부에 연다 — 세션 쿠키를 위해 DAWN_PORTAL_HTTPS=1 로 재기동하는 게 맞다"
fi

# ── 기동 ────────────────────────────────────────────────────────────────
: > "$LOGF"; rm -f "$URLF"
setsid nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" \
  >>"$LOGF" 2>&1 </dev/null &
echo $! > "$PIDF"

printf '%s%s%s (:%s) 를 여는 중...\n' "$B" "$NAME" "$Z" "$PORT"
URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGF" 2>/dev/null | head -1)
  [[ -n "$URL" ]] && break
  sleep 1
done

if [[ -z "$URL" ]]; then
  warn "URL 을 받지 못했다. 로그: $LOGF"
  tail -5 "$LOGF"
  exit 1
fi
echo "$URL" > "$URLF"

# 터널이 붙는 데 몇 초 걸린다 — 한 번 찔러 보고 000 이면 재시도한다.
code=000
for _ in 1 2 3 4 5; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL/" 2>/dev/null || echo 000)
  [[ "$code" != "000" ]] && break
  sleep 3
done
ok "$NAME  →  ${B}$URL${Z}"
printf '  인증      %s\n' "$AUTH"
printf '  외부 응답 HTTP %s\n' "$code"
printf '  로그      %s\n' "$LOGF"
printf '  %s닫기: ./infra/cloudflare/tunnel.sh --down   (프로세스가 죽으면 URL 도 사라진다)%s\n' "$D" "$Z"
