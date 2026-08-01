#!/usr/bin/env bash
# P1 자기검증 — docs/instructions/P1_experience_graph.md 의 DoD 와 자기검증 절차.
#
#   ./scripts/verify-p1.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
DB="${EG_DB_PATH:-$ROOT/var/eg/bastion_graph.db}"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi

PASS=0; FAIL=0
check() {
  local name="$1"; shift
  printf '\n%s── %s%s\n' "$B" "$name" "$Z"
  if "$@" 2>&1 | sed 's/^/   /'; then
    printf '   %s✔ PASS%s\n' "$G" "$Z"; PASS=$((PASS+1))
  else
    printf '   %s✘ FAIL%s\n' "$R" "$Z"; FAIL=$((FAIL+1))
  fi
}

echo "════════════════════════════════════════════════════════════════"
echo "  P1 자기검증 — Experience Graph"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')   DB=$DB"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 회사명 갱신 ───────────────────────────────────────────────────
check "DoD-1  시드 회사명이 the dawn of AGI 로 갱신됨" \
  "$PY" scripts/lib/check_company_name.py

# ── DoD-2 로더 동작 ─────────────────────────────────────────────────────
check "DoD-2  EG 로더 동작 · 거버넌스 노드/엣지 주입" bash -c "
  '$PY' -m dawn_core.cli eg load --dry-run
  test -f '$DB' || { echo 'DB 가 없다 — make eg-load 를 먼저 실행'; exit 1; }
  '$PY' -m dawn_core.cli eg stats | head -20
"

# ── DoD-3 validate.py 오류 0 ────────────────────────────────────────────
check "DoD-3  validate.py 오류 0 (노드 74 · 엣지 136)" bash -c "
  out=\$('$PY' eg/validate.py --no-demo 2>&1)
  echo \"\$out\" | grep -E '노드|엣지' | head -2
  echo \"\$out\" | tail -2
  echo \"\$out\" | grep -q '오류 0' || exit 1
  echo \"\$out\" | grep -qE '노드 +74' || { echo '노드 수 불일치'; exit 1; }
  echo \"\$out\" | grep -qE '엣지 +136' || { echo '엣지 수 불일치'; exit 1; }
"

# ── DoD-4 조직→페르소나→정책 체인 (3개 조직) ────────────────────────────
check "DoD-4  eg_search 조직→페르소나→정책 체인 (CCC·인사·보안AX)" \
  "$PY" scripts/lib/check_org_chain.py org:ccc org:hr org:ax-sec

# ── DoD-5 핵심 순회 3종 ─────────────────────────────────────────────────
check "DoD-5  핵심 순회 3종 (심각도 · 게이트 · 개입지점)" bash -c "
  echo '① 심각도'
  '$PY' -m dawn_core.cli eg severity | grep -E '최고' | head -3
  echo '② 게이트'
  '$PY' -m dawn_core.cli eg gate asset:payment | grep -E '심각도|게이트'
  echo '③ 개입지점'
  '$PY' -m dawn_core.cli eg org org:hr --json | '$PY' -c 'import json,sys;d=json.load(sys.stdin);print(\"   \",d[\"personas\"],\"→\",d[\"policies\"])'
"

# ── DoD-6 스냅샷 ────────────────────────────────────────────────────────
check "DoD-6  스냅샷 저장" bash -c "
  '$PY' -m dawn_core.cli eg snapshot --label verify
  ls -la eg/snapshots/*.json | tail -3
"

# ── 확장: 통제 평면 ↔ EG 브리지 ─────────────────────────────────────────
check "확장   통제 평면 ↔ EG 정합성" "$PY" -m dawn_core.cli eg bridge
check "확장   모델 라우팅 (조직별로 다른 모델 + L3 로컬 강제)" bash -c "
  '$PY' -m dawn_core.cli eg routing
  # 조직마다 다른 모델이 나와야 한다
  n=\$('$PY' -m dawn_core.cli eg routing --json | '$PY' -c 'import json,sys;print(len({r[\"model_normal\"] for r in json.load(sys.stdin)}))')
  echo \"   서로 다른 모델 \$n 종\"
  test \"\$n\" -ge 2 || { echo '모든 조직이 같은 모델을 쓴다 — 라우팅이 동작하지 않는다'; exit 1; }
"

# ── 자기검증 #3: 개입 시뮬레이션 ────────────────────────────────────────
# "사람이 EG를 고치면 반영된다" 를 실증한다. 코드는 건드리지 않는다.
MARKER="개입 실증 — 이 원칙은 검증 후 즉시 제거된다"
intervention_demo() {
  local seed=eg/seed/03_personas.json rc=0
  cp "$seed" "$seed.bak"

  local before after restored
  before=$("$PY" -m dawn_core.cli eg org org:hr --json | sha256sum | cut -c1-16)
  echo "수정 전 인사팀 프로파일 해시: $before"

  "$PY" scripts/lib/intervene.py add persona:corporate "$MARKER" || { mv "$seed.bak" "$seed"; return 1; }

  if ! "$PY" eg/validate.py --no-demo >/dev/null 2>&1; then
    mv "$seed.bak" "$seed"; echo "수정본이 validate 를 통과하지 못했다"; return 1
  fi
  echo "validate 통과 — 재주입"
  "$PY" -m dawn_core.cli eg load >/dev/null 2>&1

  after=$("$PY" -m dawn_core.cli eg org org:hr --json | sha256sum | cut -c1-16)
  echo "수정 후 인사팀 프로파일 해시: $after"

  if "$PY" scripts/lib/intervene.py check persona:corporate "$MARKER"; then
    echo "→ 인사팀 에이전트가 조회하는 원칙에 새 항목이 나타났다 (코드 변경 0)"
  else
    echo "EG 수정이 조직 프로파일에 반영되지 않았다"; rc=1
  fi

  mv "$seed.bak" "$seed"
  "$PY" -m dawn_core.cli eg load >/dev/null 2>&1
  restored=$("$PY" -m dawn_core.cli eg org org:hr --json | sha256sum | cut -c1-16)
  echo "원복 후 인사팀 프로파일 해시: $restored"

  [[ "$before" != "$after" ]] || { echo "수정이 프로파일을 바꾸지 않았다"; rc=1; }
  [[ "$before" == "$restored" ]] || { echo "원복이 되지 않았다"; rc=1; }
  return $rc
}
check "실증   개입 시뮬레이션 (persona:corporate 수정 → 반영 → 원복)" intervention_demo

# ── 실증: pol:l3-local-only 가 실제로 막는가 ────────────────────────────
check "실증   L3 로컬 강제 (pol:l3-local-only)" bash -c "
  echo '   인사팀(로컬 모델 배정) + L3 자산:'
  '$PY' -m dawn_core.cli eg model org:hr --l3
  echo
  echo '   AOC 개발부(클라우드만 배정) + L3 자산 → 차단되어야 함:'
  if '$PY' -m dawn_core.cli eg model org:aoc-dev --l3; then
    echo '   차단되지 않았다 — pol:l3-local-only 미작동'; exit 1
  fi
  echo '   → 차단 확인'
"

echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s\n" "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
