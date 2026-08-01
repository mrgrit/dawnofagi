#!/usr/bin/env bash
# P6 자기검증 — docs/instructions/P6_integration.md 의 DoD 와 자기검증 절차.
#
#   ./scripts/verify-p6.sh            # 모델 호출 없이 구조 검증
#   ./scripts/verify-p6.sh --live     # 사내 GPU 로 E2E·레드팀 실전까지
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
OPS="$ROOT/.venv/bin/dawn-ops"; [[ -x "$OPS" ]] || OPS="$PY -m dawn_ops.cli"

LIVE=0; [[ "${1:-}" == "--live" ]] && LIVE=1

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi

PASS=0; FAIL=0; SKIP=0
check() { local n="$1"; shift
  printf '\n%s── %s%s\n' "$B" "$n" "$Z"
  if "$@" 2>&1 | sed 's/^/   /'; then printf '   %s✔ PASS%s\n' "$G" "$Z"; PASS=$((PASS+1))
  else printf '   %s✘ FAIL%s\n' "$R" "$Z"; FAIL=$((FAIL+1)); fi; }
skip() { printf '\n%s── %s%s\n   %s⊘ SKIP%s — %s\n' "$B" "$1" "$Z" "$Y" "$Z" "$2"; SKIP=$((SKIP+1)); }

echo "════════════════════════════════════════════════════════════════"
echo "  P6 자기검증 — 통합 · 레드팀 · 인시던트 리허설 · 자사 운영 개시"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')   live=$LIVE"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 E2E ───────────────────────────────────────────────────────────
check "DoD-1  E2E — 요구→에이전트→업무→관제→표시→축적 (8구간 개별 검사)" \
  "$OPS" e2e $([[ $LIVE -eq 1 ]] && echo --live)

# ── DoD-2 레드팀 ────────────────────────────────────────────────────────
check "DoD-2  오펜시브 레드팀 — 탐지 커버리지 + 미탐 보강" bash -c "
  set -e
  $OPS redteam $([[ $LIVE -eq 1 ]] && echo --live)
  echo
  echo '정상 업무가 오탐으로 걸리지 않는가 (오탐이 쌓이면 게이트는 무시된다):'
  '$PY' -m pytest ops/tests -q -k 'benign or every_family or model_refusal or scope'
"

# ── DoD-3 자율화 A1 가동 + KPI ──────────────────────────────────────────
check "DoD-3  자율화 A1 가동 — KPI(개입률·오탐율) 수집" bash -c "
  set -e
  '$PY' -m dawn_aoc.cli kpi
  echo
  echo '전 에이전트가 A1 이상으로 가동 중인가:'
  '$PY' - <<'PYEOF'
from dawn_aoc.console import build_state
from dawn_core.paths import Paths

st = build_state(Paths().root, limit=100)
bad = []
for a in st['agents']:
    mark = '✔' if a['control_state'] == 'running' else '✘'
    print(f\"   {mark} {a['agent_id']:<22} {a['autonomy']}  {a['control_state']:<9} \"
          f\"run {a['runs']:<4} {a['room']}\")
    if a['control_state'] != 'running':
        bad.append(a['agent_id'])
if bad:
    raise SystemExit(f'가동 중이 아닌 에이전트: {bad}')
print(f\"   전 {len(st['agents'])}기 가동 중\")
PYEOF
"

# ── DoD-4·5 인시던트 리허설 + 비가역 대응 실증 ──────────────────────────
check "DoD-4·5 인시던트 3종 리허설 + kill·자격증명 회수·롤백 실증" \
  "$OPS" rehearsal

# ── DoD-6 멀티테넌트 + 온보딩 절차 ──────────────────────────────────────
check "DoD-6  멀티테넌트 확장성 + 고객 온보딩 절차" \
  "$OPS" tenant

# ── DoD-7 운영 러너북 ───────────────────────────────────────────────────
check "DoD-7  운영 러너북" bash -c "
  set -e
  test -f docs/governance/RUNBOOK.md || { echo 'RUNBOOK.md 가 없다'; exit 1; }
  echo '러너북 목차:'
  grep -E '^## ' docs/governance/RUNBOOK.md | sed 's/^## /   /'
  echo
  '$PY' - <<'PYEOF'
import pathlib, re
t = pathlib.Path('docs/governance/RUNBOOK.md').read_text(encoding='utf-8')
need = ['아침에 하는 것', '킬 스위치', '장애 대응', '정기 점검',
        '고객 온보딩', '절대 하지 않는 것']
missing = [n for n in need if n not in t]
if missing:
    raise SystemExit(f'러너북에 빠진 절: {missing}')
# 러너북이 가리키는 make 타깃이 실제로 있나 — 없는 명령을 적으면 러너북이 아니다
mk = pathlib.Path('Makefile').read_text(encoding='utf-8')
targets = set(re.findall(r'^([a-zA-Z0-9_.-]+):', mk, re.M))
used = set(re.findall(r'make ([a-z0-9-]+)', t))
ghost = sorted(used - targets)
if ghost:
    raise SystemExit(f'러너북이 없는 make 타깃을 안내한다: {ghost}')
print(f'   절 {len(need)}개 확인 · make 타깃 {len(used)}개 전부 실재')
PYEOF
"

# ── 실증: 통제 평면 불변식 ──────────────────────────────────────────────
check "실증   전 에이전트가 L4(SOUL.md) 를 갖고 컴파일된다" \
  "$PY" -m pytest ops/tests -q -k "soul or under_control"

# ── 실증: 인시던트 3축이 전부 케이스가 된다 ─────────────────────────────
check "실증   인시던트 3축 (보안·품질·정합성) 탐지 → 케이스 → 대응" \
  "$PY" -m pytest ops/tests -q -k "three_axes or irreversible_responses or reversible"

# ── 실증: 리허설이 흔적을 남기지 않는다 ─────────────────────────────────
check "실증   리허설 후 원상복구 (안 눌러본 버튼은 사고 때도 안 눌린다)" \
  "$PY" -m pytest ops/tests -q -k "leaves_no_trace or credentials_can_be or hard_actions or rollback_quarantines"

# ── 실증: 전 단계 자기검증이 여전히 통과한다 ────────────────────────────
check "실증   P0~P6 전체 테스트" \
  "$PY" -m pytest -q

# ── 실증: 통제 평면·EG·업무 자산 정합 ───────────────────────────────────
check "실증   통제 평면 ↔ EG ↔ 업무 자산 3자 정합" bash -c "
  set -e
  '$PY' -m dawn_core.cli lint | tail -3
  '$PY' -m dawn_core.cli eg bridge | tail -3
  '$PY' -m dawn_biz.cli egcheck | tail -3
"

echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s   %s%d SKIP%s\n" \
  "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z" "$Y" "$SKIP" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
