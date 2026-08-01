#!/usr/bin/env bash
# git 훅 설치 — 시크릿 스캔 + 통제 평면 검증.
# bootstrap.sh 가 호출하지만 단독 실행도 가능하다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.28.0}"
BIN_DIR="$ROOT/bin"

log() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m✔\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m!\033[0m %s\n' "$*"; }

# ── gitleaks 확보 ────────────────────────────────────────────────────
install_gitleaks() {
  if command -v gitleaks >/dev/null 2>&1; then
    ok "gitleaks 이미 설치됨 ($(gitleaks version 2>/dev/null || echo '?'))"
    return 0
  fi
  if [[ -x "$BIN_DIR/gitleaks" ]]; then
    ok "gitleaks 로컬 바이너리 존재"
    return 0
  fi

  local arch os url
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) warn "지원하지 않는 아키텍처 $(uname -m) — gitleaks 수동 설치 필요"; return 1 ;;
  esac
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${arch}.tar.gz"

  log "gitleaks v${GITLEAKS_VERSION} 내려받는 중…"
  mkdir -p "$BIN_DIR"
  if curl -fsSL --retry 3 "$url" | tar -xz -C "$BIN_DIR" gitleaks 2>/dev/null; then
    chmod +x "$BIN_DIR/gitleaks"
    ok "gitleaks → $BIN_DIR/gitleaks"
  else
    warn "gitleaks 다운로드 실패 — 훅은 설치하되 스캔은 건너뛴다 (네트워크 확인 후 재실행)"
    return 1
  fi
}

install_gitleaks || true

# ── 훅 작성 ──────────────────────────────────────────────────────────
mkdir -p .git/hooks

cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
# the dawn of AGI — pre-commit
#   1) 시크릿 스캔 (05_conventions #1)
#   2) 통제 평면·레지스트리 검증 (통제되지 않는 에이전트는 커밋되지 않는다)
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
FAIL=0
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }

# ── 1. 시크릿 스캔 ────────────────────────────────────────────────────
GITLEAKS=""
if [[ -x "$ROOT/bin/gitleaks" ]]; then GITLEAKS="$ROOT/bin/gitleaks"
elif command -v gitleaks >/dev/null 2>&1; then GITLEAKS="$(command -v gitleaks)"; fi

if [[ -n "$GITLEAKS" ]]; then
  if ! "$GITLEAKS" protect --staged --redact --no-banner \
        --config "$ROOT/.gitleaks.toml" 2>&1; then
    red "✘ 시크릿이 스테이지에 있다 — 커밋 차단 (05_conventions #1)"
    red "  키·토큰은 .env(커밋 금지) 또는 볼트로 옮겨라."
    red "  오탐이면 .gitleaks.toml 의 allowlist 를 고쳐라. --no-verify 로 우회하지 마라."
    FAIL=1
  else
    grn "✔ 시크릿 스캔 통과"
  fi
else
  red "! gitleaks 없음 — 시크릿 스캔을 건너뛴다. scripts/install-hooks.sh 를 실행하라."
fi

# ── 2. 통제 평면 · 레지스트리 ─────────────────────────────────────────
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"

if [[ -n "$PY" ]] && "$PY" -c "import dawn_core" 2>/dev/null; then
  # org/ 나 work/ 나 COMPANY.md 가 바뀐 커밋에서만 돈다 (빠르게 유지)
  if git diff --cached --name-only | grep -qE '^(org/|work/|COMPANY\.md|packages/dawn_core/)'; then
    if ! "$PY" -m dawn_core.cli registry >/dev/null; then
      red "✘ 레지스트리 정합성 실패 — 커밋 차단"; FAIL=1
    elif ! "$PY" -m dawn_core.cli lint >/dev/null; then
      red "✘ Control Readiness 미달 — 커밋 차단 (make control-lint 로 확인)"; FAIL=1
    else
      grn "✔ 레지스트리 · 통제 평면 통과"
    fi
  fi
fi

exit $FAIL
HOOK
chmod +x .git/hooks/pre-commit
ok "pre-commit 훅 설치"

cat > .git/hooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
# 커밋 메시지 규칙: [Pn][DoD-x] 요약   (05_conventions "작게 커밋")
# merge/revert 커밋은 통과.
MSG_FILE="$1"
FIRST="$(head -1 "$MSG_FILE")"
case "$FIRST" in
  Merge*|Revert*|fixup!*|squash!*) exit 0 ;;
esac
if ! printf '%s' "$FIRST" | grep -qE '^\[P[0-6]\]\[DoD-[0-9x]+([~-][0-9x]+)?(,[0-9x]+)*\] .+'; then
  printf '\033[33m! 커밋 메시지 형식 권장: [Pn][DoD-x] 한 줄 요약\033[0m\n'
  printf '  예: [P0][DoD-3] gitleaks pre-commit 훅 추가\n'
  printf '  (경고만 — 커밋은 진행한다)\n'
fi
exit 0
HOOK
chmod +x .git/hooks/commit-msg
ok "commit-msg 훅 설치"

echo
ok "훅 설치 완료. 테스트: scripts/verify-p0.sh"
