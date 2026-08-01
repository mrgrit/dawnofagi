#!/usr/bin/env bash
# P5 자기검증 — docs/instructions/P5_business_systems.md 의 DoD 와 자기검증 절차.
#
#   ./scripts/verify-p5.sh            # 모델 호출 없이 구조 검증
#   ./scripts/verify-p5.sh --live     # 사내 GPU 로 업무 에이전트 실제 실행까지
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
BIZ="$ROOT/.venv/bin/dawn-biz"; [[ -x "$BIZ" ]] || BIZ="$PY -m dawn_biz.cli"

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
echo "  P5 자기검증 — 업무 시스템 (문서·CRM·프로젝트·경리)"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')   live=$LIVE"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 문서·지식 + EG 연동 ───────────────────────────────────────────
check "DoD-1  문서·지식 관리 + EG 연동 (문서 = Asset)" bash -c "
  set -e
  $BIZ docs --search '관제'
  echo
  echo '문서는 EG asset:knowledge 에 매인다:'
  $BIZ egcheck | grep -E 'knowledge|정합|불일치'
  echo
  '$PY' -m pytest biz/tests -q -k 'document or search or every_kind or declared_assets'
"

# ── DoD-2 CRM + 에이전트 자동 처리 ──────────────────────────────────────
check "DoD-2  CRM 최소셋 + 정형 업무 자동 처리 (HITL 경계 포함)" bash -c "
  set -e
  $BIZ crm | head -20
  echo
  '$PY' scripts/lib/biz_drill.py crm $([[ $LIVE -eq 1 ]] && echo --live)
  echo
  '$PY' -m pytest biz/tests -q -k 'contract_signing or inquiry_draft or categories'
"

# ── DoD-3 프로젝트·이슈 + 오케스트레이터 연동 ───────────────────────────
check "DoD-3  프로젝트·이슈 — 의존 판정은 코드가, 위임은 오케스트레이터가" bash -c "
  set -e
  $BIZ proj AOC_PLATFORM
  echo
  '$PY' -m pytest biz/tests -q -k 'assignable or task_close or business_workers'
"

# ── DoD-4 경리 L3: 로컬 모델 + HITL ─────────────────────────────────────
check "DoD-4  경리·총무 — L3 는 로컬 모델 전용 + HITL 게이트" \
  "$PY" scripts/lib/biz_drill.py expense $([[ $LIVE -eq 1 ]] && echo --live)

# ── DoD-5 업무 데이터가 EG Asset 으로 분류·검증 ─────────────────────────
check "DoD-5  업무 데이터가 EG Asset 으로 분류·검증됨" \
  "$PY" scripts/lib/biz_drill.py asset

# ── DoD-6 업무 에이전트 행위가 관제에 나타남 ────────────────────────────
check "DoD-6  업무 에이전트 행위가 P3 관제에 나타남 (픽셀오피스 방)" \
  "$PY" scripts/lib/biz_drill.py aoc

# ── 실증: 업무 에이전트는 P2 루프를 탄다 ────────────────────────────────
check "실증   업무 에이전트가 P2 워커 루프를 탄다 (새 실행 경로 없음)" \
  "$PY" -m pytest biz/tests -q -k "business_workers or expense_path or crm_org or business_skills"

# ── 실증: 비가역 업무는 실행부가 없다 ───────────────────────────────────
check "실증   비가역 업무 (계약 체결·자산 폐기·원장 기입) — 실행부 부재" bash -c "
  set -e
  '$PY' -m pytest biz/tests -q -k 'irreversible or contract_signing'
  echo
  echo '게이트 판정:'
  '$PY' - <<'PYEOF'
from dawn_agents import Worker
from dawn_biz.skills import build_registry
from dawn_core import Registry
from dawn_core.eg.cli import db_path
from dawn_core.eg.store import EGStore
from dawn_core.paths import Paths

root = Paths().root
reg = Registry.load(root)
eg = EGStore(db_path(reg.paths))
w = Worker('corp-admin-clerk-01', registry=reg, eg_store=eg,
           skills=build_registry(root, eg_store=eg))
for sk in ('fin.expense_read', 'fin.expense_write', 'fin.ledger_write'):
    pv = w.skills.preview(sk)
    d = w.gate.evaluate(pv, declared_tools=w.compiled.declared_tools)
    print(f'   {sk:<20} action={pv.action:<13} {d.decision:<13} [{d.severity_label}/{d.severity}]')
PYEOF
"

# ── 실증: 테넌트 격리 ───────────────────────────────────────────────────
check "실증   테넌트 격리 — 조회 함수가 tenant 를 인자로 받지 않는다" \
  "$PY" -m pytest biz/tests -q -k "tenant or public_site"

# ── 실증: 이벤트 구동 (폴링 아님) ───────────────────────────────────────
check "실증   이벤트 구동 — 훅으로 기동, 폴링 루프 없음" bash -c "
  set -e
  $BIZ emit crm.inquiry.new --payload '{\"inquiry_id\":1}' --dry-run
  echo
  $BIZ emit nothing.registered --dry-run
  echo
  '$PY' -m pytest biz/tests -q -k 'triggers or unknown_event or polling or ingest'
"

# ── 실증: JSONL 회귀 (splitlines 버그) ──────────────────────────────────
check "실증   JSONL 은 \\n 으로만 나눈다 (감사 로그가 조용히 비지 않게)" \
  "$PY" -m pytest biz/tests -q -k "jsonl"

# ── 실증: 시드는 레지스트리에서 나온다 ──────────────────────────────────
check "실증   업무 데이터가 레지스트리에서 파생된다 (사업 = 플러그인)" \
  "$PY" -m pytest biz/tests -q -k "seed"

echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s   %s%d SKIP%s\n" \
  "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z" "$Y" "$SKIP" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
