#!/usr/bin/env bash
# P0 자기검증 — docs/instructions/P0_bootstrap.md 의 DoD 와 자기검증 절차를 실제로 돌린다.
# 결과는 BUILD_LOG.md 에 붙일 수 있도록 그대로 출력한다.
#
#   ./scripts/verify-p0.sh              # 전부
#   ./scripts/verify-p0.sh --no-el34    # el34 없는 환경(CI)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_EL34=1
[[ "${1:-}" == "--no-el34" ]] && WITH_EL34=0

PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi

PASS=0; FAIL=0; SKIP=0
check() {   # check "<DoD 항목>" <command...>
  local name="$1"; shift
  printf '\n%s── %s%s\n' "$B" "$name" "$Z"
  if "$@" 2>&1 | sed 's/^/   /'; then
    printf '   %s✔ PASS%s\n' "$G" "$Z"; PASS=$((PASS+1)); return 0
  else
    printf '   %s✘ FAIL%s\n' "$R" "$Z"; FAIL=$((FAIL+1)); return 1
  fi
}
skip() { printf '\n%s── %s%s\n   %s⊘ SKIP%s — %s\n' "$B" "$1" "$Z" "$Y" "$Z" "$2"; SKIP=$((SKIP+1)); }

echo "════════════════════════════════════════════════════════════════"
echo "  P0 자기검증 — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  host=$(hostname)  python=$($PY -V 2>&1)"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 모노레포 구조 ─────────────────────────────────────────────────
check "DoD-1  모노레포 구조" bash -c '
  miss=""
  for d in apps agents aoc eg packages infra docs org work; do
    [ -d "$d" ] || miss="$miss $d"
  done
  if [ -n "$miss" ]; then echo "누락된 디렉터리:$miss"; exit 1; fi
  echo "apps agents aoc eg packages infra docs org work — 전부 존재"
  echo "커밋 수: $(git rev-list --count HEAD 2>/dev/null || echo 0)"
'

# ── DoD-2 CLAUDE.md ─────────────────────────────────────────────────────
check "DoD-2  CLAUDE.md + 가드레일 요약" bash -c '
  [ -f CLAUDE.md ] || { echo "CLAUDE.md 없음"; exit 1; }
  [ -f COMPANY.md ] || { echo "COMPANY.md 없음"; exit 1; }
  n=0
  for k in "시크릿 하드코딩 금지" "파괴적 작업" "최소권한" "테넌트 격리" "L3" "EG"; do
    grep -q "$k" CLAUDE.md && n=$((n+1)) || echo "  가드레일 누락: $k"
  done
  echo "CLAUDE.md 가드레일 $n/6 포함"
  [ $n -ge 6 ]
'

# ── DoD-3 CI 스캐폴드 ───────────────────────────────────────────────────
check "DoD-3  CI (lint + test) 로컬 재현" bash -c "
  set -e
  '$PY' -m ruff check packages infra scripts
  echo 'ruff check: 통과'
  '$PY' -m pytest packages -q 2>&1 | tail -2
"

# ── DoD-4 시크릿 스캔 훅 ────────────────────────────────────────────────
# 양방향으로 본다. "차단됐다"만 확인하면 gitleaks 설정이 깨져서 *모든* 커밋이
# 막히는 상태도 통과해 버린다 (실제로 한 번 그랬다 — .gitleaks.toml 주석 참조).
check "DoD-4  시크릿 pre-commit 훅 (통과·차단 양방향 실증)" bash -c '
  set -u
  [ -x .git/hooks/pre-commit ] || { echo "pre-commit 훅 없음"; exit 1; }
  GL=""; [ -x bin/gitleaks ] && GL=bin/gitleaks || GL="$(command -v gitleaks || true)"
  [ -n "$GL" ] || { echo "gitleaks 없음 — make hooks"; exit 1; }

  # 0) 설정이 실제로 로드되는가 (깨진 설정은 전부 차단으로 위장된다)
  if "$GL" detect --no-git --redact --no-banner --config .gitleaks.toml 2>&1 | grep -q "Failed to load config"; then
    echo ".gitleaks.toml 로드 실패 — 스캔이 도는 것처럼 보이지만 실제로는 설정 오류다"; exit 1
  fi
  echo "설정 로드 OK"

  TMP="$(mktemp -d)"; trap "rm -rf $TMP" EXIT
  RC=0

  # A) 시크릿 없는 파일은 통과해야 한다
  echo "SAFE = \"no secret here\"" > canary_clean.py
  git add canary_clean.py 2>/dev/null
  if .git/hooks/pre-commit >"$TMP/a" 2>&1; then
    echo "A) 깨끗한 파일 → 통과 ✔"
  else
    echo "A) 깨끗한 파일이 차단됐다 — 훅이 무조건 막고 있다:"; tail -3 "$TMP/a"; RC=1
  fi
  git reset -q canary_clean.py 2>/dev/null; rm -f canary_clean.py

  # B) 실제 형태의 더미 키는 차단돼야 한다
  printf "ANTHROPIC_API_KEY = \"sk-ant-%s\"\n" "api03aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" > canary_secret.py
  git add canary_secret.py 2>/dev/null
  if .git/hooks/pre-commit >"$TMP/b" 2>&1; then
    echo "B) 더미 키가 통과됐다 — 차단 실패"; RC=1
  else
    echo "B) 더미 키 → 차단 ✔  $(grep -c "시크릿이 스테이지에" "$TMP/b") 건 경고"
  fi
  git reset -q canary_secret.py 2>/dev/null; rm -f canary_secret.py
  exit $RC
'

# ── DoD-5 el34 Assessor ─────────────────────────────────────────────────
if [[ $WITH_EL34 -eq 1 ]]; then
  check "DoD-5  el34 Assessor 헬스체크 (200 확인)" "$PY" infra/el34/healthcheck.py --zones
else
  skip "DoD-5  el34 Assessor 헬스체크" "--no-el34"
fi

# ── DoD-6 문서 ──────────────────────────────────────────────────────────
check "DoD-6  참조 문서 복사 · BUILD_LOG · QUESTIONS" bash -c '
  miss=""
  for f in docs/START_HERE.md docs/context/00_charter.md docs/context/05_conventions.md \
           docs/instructions/P0_bootstrap.md docs/instructions/P6_integration.md \
           BUILD_LOG.md QUESTIONS.md; do
    [ -f "$f" ] || miss="$miss $f"
  done
  [ -n "$miss" ] && { echo "누락:$miss"; exit 1; }
  echo "참조 문서 $(ls docs/context/*.md | wc -l)종 · 지시문 $(ls docs/instructions/*.md | wc -l)종 · BUILD_LOG · QUESTIONS 존재"
'

# ── 추가: 통제 평면 (이번 P0 확장분) ────────────────────────────────────
check "확장   조직·사업 레지스트리 정합성" "$PY" -m dawn_core.cli registry
check "확장   통제 평면 컴파일 (전 에이전트)" "$PY" -m dawn_core.cli compile --all
check "확장   Control Readiness Score ≥ 80" "$PY" -m dawn_core.cli lint

# ── 개입 실증: 사람이 문서를 고치면 에이전트가 바뀌는가 ─────────────────
check "실증   통제 평면 개입 (SOUL.md 수정 → 프롬프트 반영 → 원복)" bash -c "
  set -e
  A=ccc-soc-triage-01
  S=org/agents/\$A/SOUL.md
  BEFORE=\$('$PY' -m dawn_core.cli compile \$A --prompt | sha256sum | cut -c1-16)
  cp \$S \$S.bak
  printf '\n## (개입 테스트) 임시 지침\n- 이 줄은 검증 후 즉시 제거된다.\n' >> \$S
  AFTER=\$('$PY' -m dawn_core.cli compile \$A --prompt | sha256sum | cut -c1-16)
  '$PY' -m dawn_core.cli compile \$A --prompt | grep -q '개입 테스트' || { mv \$S.bak \$S; echo '수정이 프롬프트에 반영되지 않았다'; exit 1; }
  mv \$S.bak \$S
  RESTORED=\$('$PY' -m dawn_core.cli compile \$A --prompt | sha256sum | cut -c1-16)
  echo \"프롬프트 해시: 수정전 \$BEFORE → 수정후 \$AFTER → 원복 \$RESTORED\"
  [ \"\$BEFORE\" != \"\$AFTER\" ] && [ \"\$BEFORE\" = \"\$RESTORED\" ]
"

# ── 실증: 게이트가 실제로 막는가 ────────────────────────────────────────
check "실증   게이트 강제 (권한 확대 시도 → 컴파일 실패)" bash -c "
  set -u
  G=org/divisions/corp/admin/gate.yaml
  cp \$G \$G.bak
  # 경리 팀이 외부 발송·결제 권한을 스스로 부여하려 시도
  '$PY' - <<'PY'
import pathlib, re
p = pathlib.Path('org/divisions/corp/admin/gate.yaml')
t = p.read_text(encoding='utf-8')
t = t.replace('    - fin.expense_write', '    - fin.expense_write\n    - comm.external_send\n    - pay.execute')
p.write_text(t, encoding='utf-8')
PY
  if '$PY' -m dawn_core.cli compile corp-admin-clerk-01 >/dev/null 2>&1; then
    mv \$G.bak \$G; echo '권한 확대가 통과됐다 — 단조 축소 불변식 실패'; exit 1
  fi
  echo '권한 확대(comm.external_send · pay.execute) 시도가 컴파일 단계에서 차단됨:'
  '$PY' -m dawn_core.cli compile corp-admin-clerk-01 2>&1 | grep -E 'deny|범위 밖' | head -3
  mv \$G.bak \$G
  '$PY' -m dawn_core.cli compile corp-admin-clerk-01 >/dev/null && echo '원복 후 정상 컴파일 확인'
"

# ── 요약 ────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s   %s%d SKIP%s\n" "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z" "$Y" "$SKIP" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
