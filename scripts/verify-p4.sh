#!/usr/bin/env bash
# P4 자기검증 — docs/instructions/P4_web_groupware.md 의 DoD 와 자기검증 절차.
#
#   ./scripts/verify-p4.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
WEB="$ROOT/.venv/bin/dawn-web"; [[ -x "$WEB" ]] || WEB="$PY -m dawn_groupware.cli"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi

PASS=0; FAIL=0; SKIP=0
check() { local n="$1"; shift
  printf '\n%s── %s%s\n' "$B" "$n" "$Z"
  if "$@" 2>&1 | sed 's/^/   /'; then printf '   %s✔ PASS%s\n' "$G" "$Z"; PASS=$((PASS+1))
  else printf '   %s✘ FAIL%s\n' "$R" "$Z"; FAIL=$((FAIL+1)); fi; }
skip() { printf '\n%s── %s%s\n   %s⊘ SKIP%s — %s\n' "$B" "$1" "$Z" "$Y" "$Z" "$2"; SKIP=$((SKIP+1)); }

echo "════════════════════════════════════════════════════════════════"
echo "  P4 자기검증 — 공개 홈페이지 · 사내 그룹웨어"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1 공개 홈페이지 ─────────────────────────────────────────────────
check "DoD-1  공개 홈페이지 — 미션·사업·조직 표시 + 폼 검증" bash -c "
  set -e
  '$PY' - <<'PYEOF'
from starlette.testclient import TestClient
from dawn_core import Registry
from dawn_groupware.app import build_site

c = TestClient(build_site(), follow_redirects=False)
reg = Registry.load()
home = c.get('/').text
assert 'AGI' in home and '공급하고 확산한다' in home, '미션이 없다'
print('   미션      AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다')

biz = c.get('/business').text
for b in reg.businesses.values():
    assert b.data['name'] in biz, b.data['name']
print('   사업      ' + ', '.join(sorted(b.data['name'] for b in reg.businesses.values())))

org = c.get('/org').text
for d in reg.divisions.values():
    assert d.data['name'] in org, d.data['name']
print('   조직      ' + ', '.join(sorted(d.data['name'] for d in reg.divisions.values())))
print('   → 전부 org/ 레지스트리에서 렌더된다 (하드코딩 아님)')
PYEOF
  echo
  echo '공개 사이트가 내부 자산에 손이 닿는지:'
  '$PY' -m pytest apps/groupware/tests -q -k 'site_cannot_reach or site_pages or contact'
"

# ── DoD-2 인증·조직 기반 권한 ───────────────────────────────────────────
check "DoD-2  인증 + 조직 기반 권한 (권한 = 조직 × 능력)" bash -c "
  set -e
  $WEB users
  echo
  '$PY' -m pytest apps/groupware/tests -q -k 'password or capability or org_chain or approver or critical_needs or missing_agent_org or open_redirect or csrf'
"

# ── DoD-3 공지·문서·일정·디렉터리 ───────────────────────────────────────
check "DoD-3  공지·문서·일정·디렉터리 최소셋 + 테넌트 격리" \
  "$PY" -m pytest apps/groupware/tests -q -k "tenant or document_level or seed_uses or directory or portal_pages"

# ── DoD-4 자기검증① 권한 없는 계정 차단 ─────────────────────────────────
check "DoD-4  자기검증① 권한 없는 계정 → EG·관제 접근 차단" \
  "$PY" scripts/lib/web_intervention_drill.py rbac

# ── DoD-5 자기검증② HITL 승인이 실행 계층에 반영 ────────────────────────
check "DoD-5  자기검증② 워커 정지 → 그룹웨어 승인 → 실행 계층 반영" \
  "$PY" scripts/lib/web_intervention_drill.py approval

# ── DoD-6 자기검증③ EG 조정 ─────────────────────────────────────────────
check "DoD-6  자기검증③ EG 조정 → 검증 → 재주입 → 워커 프롬프트 변화 → 이력" \
  "$PY" scripts/lib/web_intervention_drill.py eg

# ── DoD-7 AOC 콘솔 권한별 접근 ──────────────────────────────────────────
check "DoD-7  AOC 콘솔·픽셀 오피스 — 권한별 접근" \
  "$PY" -m pytest apps/groupware/tests -q -k "aoc_view"

# ── 실증: EG 검증 실패는 롤백 ───────────────────────────────────────────
check "실증   EG 검증 실패 → 시드 자동 롤백 (DB 는 손도 안 댄다)" \
  "$PY" -m pytest apps/groupware/tests -q -k "eg_invalid or eg_valid or eg_editor_rejects or editable_scope"

# ── 실증: 감사 로그 ─────────────────────────────────────────────────────
check "실증   감사 로그 — append-only · 시크릿 미기록" bash -c "
  set -e
  '$PY' -m pytest apps/groupware/tests -q -k 'audit or password_hash_in_any_page'
  echo
  echo '최근 감사 로그:'
  $WEB audit --limit 8
"

# ── 실증: XSS 는 타입으로 막는다 ────────────────────────────────────────
check "실증   XSS — 이스케이프가 기본, Safe 만 원문" \
  "$PY" -m pytest apps/groupware/tests -q -k "escaping or safe_ or markdownish or page_escapes"

# ── 실증: 서버 기동 ─────────────────────────────────────────────────────
check "실증   두 앱이 실제로 뜬다 (별도 프로세스 = 존 분리)" bash -c "
  set -e
  '$PY' - <<'PYEOF'
import socket, subprocess, sys, time, urllib.request

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

for cmd, label in [('site', '공개 홈페이지'), ('portal', '그룹웨어')]:
    port = free_port()
    p = subprocess.Popen([sys.executable, '-m', 'dawn_groupware.cli', cmd,
                          '--port', str(port), '--host', '127.0.0.1'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                code = urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz',
                                              timeout=2).getcode()
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise SystemExit(f'{label} 가 뜨지 않았다')
        print(f'   {label:<12} :{port}  healthz {code}')
    finally:
        p.terminate(); p.wait(timeout=10)
PYEOF
"

echo
echo "════════════════════════════════════════════════════════════════"
printf "  결과:  %s%d PASS%s   %s%d FAIL%s   %s%d SKIP%s\n" \
  "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z" "$Y" "$SKIP" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
