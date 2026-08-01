#!/usr/bin/env bash
# P2 자기검증 — docs/instructions/P2_harness_loop.md 의 DoD 와 자기검증 절차.
#
#   ./scripts/verify-p2.sh            # 모델 호출 없이 구조 검증
#   ./scripts/verify-p2.sh --live     # 사내 GPU 로 실제 워커 실행까지
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
AGENT="$ROOT/.venv/bin/dawn-agent"; [[ -x "$AGENT" ]] || AGENT="$PY -m dawn_agents.cli"

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
echo "  P2 자기검증 — 에이전트 하네스·루프·행동 게이트"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')   live=$LIVE"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 워커 루프 4단계 ───────────────────────────────────────────────
check "DoD-1  워커 루프 4단계 (eg_search→preview→run→record)" \
  "$PY" -m pytest agents/tests -q -k "four_step or preview_always or record_missing"

# ── DoD-2 행동 게이트: destructive + L3 → HITL ──────────────────────────
check "DoD-2  행동 게이트 — 비가역·L3 가 HITL 로 라우팅되는가" bash -c "
  set -e
  echo '결제 실행(pay.execute — 비가역·L3):'
  $AGENT preview corp-admin-clerk-01 pay.execute --args '{\"amount\":1000000}' 2>&1 | head -8 || true
  echo
  echo '원장 기입(fin.ledger_write — 비가역):'
  $AGENT preview corp-admin-clerk-01 fin.ledger_write 2>&1 | grep -E '행동 게이트|control_plane|skill_risk' || true
  echo
  '$PY' -m pytest agents/tests -q -k 'destructive_l3 or ledger_write_blocked or hitl_blocks or undeclared'
"

# ── DoD-3 모델 라우팅 (EG 기반, 조직별로 다름) ──────────────────────────
check "DoD-3  모델 라우팅 — 조직마다 다른 모델, L3 는 로컬 강제" bash -c "
  set -e
  '$PY' -m dawn_core.cli eg routing
  echo
  '$PY' -m pytest agents/tests -q -k 'routing_differs or l3_forces or cloud_never or l3_run_uses'
"

# ── DoD-4 팀 오케스트레이터 ─────────────────────────────────────────────
check "DoD-4  팀 오케스트레이터 (리더 무발화 · 검증자 ≠ 생산자 · 의존 순서)" \
  "$PY" -m pytest agents/tests -q -k "verifier_must or dependency or orchestrator_rejects"

# ── DoD-5 이벤트 구동 (상시 폴링 아님) ──────────────────────────────────
check "DoD-5  이벤트 구동 — 훅으로 기동, 폴링 루프 없음" bash -c "
  set -e
  echo '등록된 트리거:'
  $AGENT emit siem.alert --dry-run --payload '{\"alert_id\":\"W-1\",\"summary\":\"테스트\"}' 2>&1 | head -6
  echo
  echo '핸들러 없는 이벤트 → 아무 일도 일어나지 않는다:'
  $AGENT emit nothing.registered 2>&1 | head -3
  echo
  '$PY' -m pytest agents/tests -q -k 'triggers_registered or unknown_event or queue_roundtrip or no_polling'
"

# ── DoD-6 OTel 스팬 방출 ────────────────────────────────────────────────
check "DoD-6  OTel GenAI 스팬 (invoke_agent → chat/execute_tool)" \
  "$PY" -m pytest agents/tests -q -k "span_tree or gate_decision_is_on or pii_is_masked"

# ── DoD-7 2개 조직 워커 실제 실행 ───────────────────────────────────────
if [[ $LIVE -eq 1 ]]; then
  check "DoD-7  2개 조직 워커 실제 실행 (사내 GPU)" \
    "$PY" scripts/lib/demo_two_orgs.py
else
  skip "DoD-7  2개 조직 워커 실제 실행" "--live 없이는 모델을 부르지 않는다"
fi

# ── 실증: 서킷 브레이커 ─────────────────────────────────────────────────
check "실증   서킷 브레이커 (예산 초과 시 중단 + 기록은 남는다)" \
  "$PY" -m pytest agents/tests -q -k "circuit_breaker"

# ── 실증: 개입 (Persona.prohibited 추가 → 행동 회피) ────────────────────
check "실증   EG 개입 — persona 금지 추가가 워커 프롬프트에 반영되는가" bash -c "
  set -e
  SEED=eg/seed/03_personas.json
  MARK='개입 실증 — sec.trace_query 를 사용하지 않는다'
  cp \$SEED \$SEED.bak
  trap 'mv \$SEED.bak \$SEED 2>/dev/null; \"$PY\" -m dawn_core.cli eg load >/dev/null 2>&1' EXIT

  before=\$('$PY' - <<'PYEOF'
from dawn_agents import Worker
print(len(Worker('ccc-soc-triage-01').system_prompt()))
PYEOF
)
  '$PY' - <<PYEOF
import json, pathlib
p = pathlib.Path('eg/seed/03_personas.json')
d = json.loads(p.read_text(encoding='utf-8'))
for per in d['Persona']:
    if per['id'] == 'persona:secops':
        per['prohibited'].insert(0, '\$MARK')
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PYEOF
  '$PY' eg/validate.py --no-demo >/dev/null
  '$PY' -m dawn_core.cli eg load >/dev/null
  if '$PY' - <<'PYEOF'
from dawn_agents import Worker
import sys
sys.exit(0 if '개입 실증' in Worker('ccc-soc-triage-01').system_prompt() else 1)
PYEOF
  then echo '→ EG 의 금지 항목이 워커 시스템 프롬프트에 나타났다 (코드 변경 0)'
  else echo 'EG 수정이 워커 프롬프트에 반영되지 않았다'; exit 1; fi
  echo \"프롬프트 길이: 수정전 \$before → 수정후 반영 확인\"
"

# ── 실증: HITL 승인 왕복 ────────────────────────────────────────────────
check "실증   HITL 승인 왕복 (요청 → 승인 → 재판정 불가)" \
  "$PY" -m pytest agents/tests -q -k "hitl_request_lands or approval_is_append_only"

# ── 실증: 스킬 안전 ─────────────────────────────────────────────────────
check "실증   스킬 안전 (카탈로그 강제 · traversal 차단 · 비가역 미구현)" \
  "$PY" -m pytest agents/tests -q -k "must_be_in_catalog or traversal or state_changing or irreversible_skills"

echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s   %s%d SKIP%s\n" \
  "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z" "$Y" "$SKIP" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
