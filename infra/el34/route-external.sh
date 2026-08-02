#!/usr/bin/env bash
# 외부 장비(물리 LAN) ↔ 컨테이너 존(도커 브리지) 경로 — QUESTIONS Q10.
#
#   bash infra/el34/route-external.sh            # 무엇을 바꿀지 보여만 준다 (기본)
#   sudo bash infra/el34/route-external.sh --apply
#   sudo bash infra/el34/route-external.sh --revert
#
# ── 왜 이 방식인가 ────────────────────────────────────────────────────────
# 외부 장비는 192.168.0.x, 컨테이너 존은 10.20.30~40.x 다. 셋 중에 골랐다:
#
#   1. 호스트 라우팅 (이것)  — 이 호스트가 게이트웨이. pipe 존을 거친다
#   2. 오버레이(swarm/VXLAN) — 깔끔하지만 el34 랩 구성을 건드려야 한다
#   3. 존 분리              — 안전하지만 hand-off 가 파일 교환으로 제한된다
#
# 1번을 고른 이유: **pipe 존이 PEP 라는 통제 평면의 전제가 유지된다.** 존을 넘는
# 트래픽이 검사 지점을 지난다는 것이 이 회사의 통제 모델 전체가 기대는 가정이다.
# 2번은 그 가정을 L2 로 우회하고, 3번은 hand-off 를 포기한다.
#
# ── 이 스크립트는 에이전트가 실행하지 않는다 ─────────────────────────────
# 방화벽 변경은 05_conventions 규칙 2 의 게이트 대상이다. 사람이 본 뒤에 건다.
# 적용한 뒤 `infra/pool.yaml` 의 `routing.enabled` 를 true 로 바꿔야 할당기가
# 이 경로를 인정한다 — **연 것과 열렸다고 적은 것을 따로 둔다.**
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:---dry-run}"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; D=$'\033[2m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; D=""; Z=""; fi

# el34 존 네트워크 — 이름과 대역은 el34 의 compose 가 정한다. 여기서 새로 짓지 않는다.
declare -A ZONE_CIDR=(
  [ext]=10.20.30.0/24  [pipe]=10.20.31.0/24  [dmz]=10.20.32.0/24
  [user]=10.20.33.0/24 [int]=10.20.40.0/24
)

# 외부 장비 대역 — pool.yaml 의 호스트 주소에서 읽는다. 하드코딩하지 않는다.
LAN="$(
  "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || echo ""
from dawn_core.infrapool import load_pool
from dawn_core.paths import Paths
_l, hosts = load_pool(Paths().root)
nets = sorted({".".join(h.address.split(".")[:3]) + ".0/24"
               for h in hosts if h.address.count(".") == 3})
print(" ".join(nets))
PY
)"

echo "════════════════════════════════════════════════════════════════"
echo "  외부 장비 ↔ 컨테이너 존 경로 (Q10)"
echo "════════════════════════════════════════════════════════════════"

if [[ -z "$LAN" ]]; then
  printf '%s풀에 외부 장비가 없다.%s\n' "$Y" "$Z"
  echo "  infra/pool.yaml 의 hosts: 에 장비를 먼저 등록하라 (address 필요)."
  echo "  여는 경로는 등록된 장비의 대역에서 나온다 — 여기서 지어내지 않는다."
  exit 0
fi

echo "  외부 대역   $LAN"
echo "  경유 존     pipe (${ZONE_CIDR[pipe]})  ← 존 경계 = 검사 지점"
echo

# 열 존: pool.yaml 의 routing.zones. 비어 있으면 dmz 를 예시로 보여준다.
ZONES="$(
  "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || echo ""
from dawn_core.infrapool import pool_doc
from dawn_core.paths import Paths
r = pool_doc(Paths().root).get("routing") or {}
print(" ".join(r.get("zones") or []))
PY
)"
if [[ -z "$ZONES" ]]; then
  printf '%srouting.zones 가 비어 있다.%s pool.yaml 에 열 존을 먼저 적어라 (예: [dmz]).\n' \
    "$Y" "$Z"
  echo "  ${D}무엇을 열지는 사람이 정한다 — 전부 여는 기본값을 두지 않는다.${Z}"
  exit 0
fi

RULES=()
for z in $ZONES; do
  cidr="${ZONE_CIDR[$z]:-}"
  if [[ -z "$cidr" ]]; then
    printf '%s알 수 없는 존: %s%s\n' "$R" "$z" "$Z"; exit 2
  fi
  for lan in $LAN; do
    # 정방향/역방향 포워딩만 연다. NAT 는 안 건다 — 출발지가 보여야 감사가 된다.
    RULES+=("iptables -A FORWARD -s $lan -d $cidr -j ACCEPT")
    RULES+=("iptables -A FORWARD -s $cidr -d $lan -m state --state ESTABLISHED,RELATED -j ACCEPT")
  done
done

echo "  ${B}적용할 규칙${Z}"
for r in "${RULES[@]}"; do echo "    $r"; done
echo "    sysctl -w net.ipv4.ip_forward=1"
echo

case "$MODE" in
  --apply)
    [[ $EUID -eq 0 ]] || { printf '%ssudo 가 필요하다%s\n' "$R" "$Z"; exit 2; }
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    for r in "${RULES[@]}"; do eval "$r" || exit 1; done
    printf '%s✔ 적용됨%s\n' "$G" "$Z"
    echo "  ${Y}아직 끝이 아니다${Z} — infra/pool.yaml 의 routing.enabled 를 true 로 바꿔야"
    echo "  할당기가 이 경로를 인정한다. 그 뒤 dawn-biz infra --retry."
    ;;
  --revert)
    [[ $EUID -eq 0 ]] || { printf '%ssudo 가 필요하다%s\n' "$R" "$Z"; exit 2; }
    for r in "${RULES[@]}"; do eval "${r/-A FORWARD/-D FORWARD}" 2>/dev/null; done
    printf '%s✔ 되돌림%s  (pool.yaml 의 routing.enabled 도 false 로 되돌려라)\n' "$G" "$Z"
    ;;
  *)
    printf '%s적용하지 않았다%s — 위 규칙을 확인하고 --apply 로 실행하라.\n' "$D" "$Z"
    ;;
esac
