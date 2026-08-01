#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════
#  the dawn of AGI — 원샷 부트스트랩
#
#  깨끗한 리눅스 한 대에서 이 스크립트 하나로 개발·운영 환경이 선다.
#    · 시스템 패키지 (python3-venv/pip, build-essential, git, jq, curl, sqlite3)
#    · Node.js LTS  (P4/P5 웹앱용 — --no-node 로 생략 가능)
#    · Docker + compose (el34 연동용 — 이미 있으면 건너뜀)
#    · Python venv + dawn-core 설치
#    · gitleaks + git 훅
#    · .env 생성 (템플릿에서)
#    · 검증 (레지스트리 · 통제 평면 · 테스트 · el34)
#
#  사용:
#      ./bootstrap.sh                  # 전부
#      ./bootstrap.sh --no-el34        # el34 연동 검사 생략
#      ./bootstrap.sh --no-docker      # 도커 설치 생략
#      ./bootstrap.sh --no-node        # Node 설치 생략
#      ./bootstrap.sh --no-sudo        # sudo 필요한 단계 전부 생략 (CI용)
#      ./bootstrap.sh --help
#
#  지원: Ubuntu/Debian · RHEL/Rocky/Fedora · Arch  (그 외는 수동 안내)
# ════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ── 옵션 ────────────────────────────────────────────────────────────────
WITH_EL34=1; WITH_DOCKER=1; WITH_NODE=1; WITH_SUDO=1; PY_MIN="3.10"
NODE_MAJOR="${NODE_MAJOR:-22}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-el34)   WITH_EL34=0 ;;
    --no-docker) WITH_DOCKER=0 ;;
    --no-node)   WITH_NODE=0 ;;
    --no-sudo)   WITH_SUDO=0; WITH_DOCKER=0 ;;
    -h|--help)   sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 옵션: $1 (--help)"; exit 2 ;;
  esac
  shift
done

# ── 출력 ────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  B=$'\033[1m'; C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; Z=$'\033[0m'
else B=""; C=""; G=""; Y=""; R=""; Z=""; fi

STEP=0
step() { STEP=$((STEP+1)); printf '\n%s[%d/%d]%s %s%s%s\n' "$C" "$STEP" "$TOTAL" "$Z" "$B" "$*" "$Z"; }
ok()   { printf '  %s✔%s %s\n' "$G" "$Z" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$Z" "$*"; }
die()  { printf '  %s✘%s %s\n' "$R" "$Z" "$*" >&2; exit 1; }
info() { printf '    %s\n' "$*"; }

TOTAL=8
[[ $WITH_NODE -eq 1 ]]   || TOTAL=$((TOTAL-1))
[[ $WITH_DOCKER -eq 1 ]] || TOTAL=$((TOTAL-1))
[[ $WITH_EL34 -eq 1 ]]   || TOTAL=$((TOTAL-1))

printf '%s╭──────────────────────────────────────────────────────────╮%s\n' "$C" "$Z"
printf '%s│%s  %sthe dawn of AGI%s — 부트스트랩                          %s│%s\n' "$C" "$Z" "$B" "$Z" "$C" "$Z"
printf '%s│%s  AI 역사가 AGI로 가는 길에 필요한 것들을 만든다.          %s│%s\n' "$C" "$Z" "$C" "$Z"
printf '%s╰──────────────────────────────────────────────────────────╯%s\n' "$C" "$Z"

# ── 배포판 감지 ─────────────────────────────────────────────────────────
PKG=""
if   command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v dnf     >/dev/null 2>&1; then PKG=dnf
elif command -v yum     >/dev/null 2>&1; then PKG=yum
elif command -v pacman  >/dev/null 2>&1; then PKG=pacman
fi

SUDO=""
if [[ $WITH_SUDO -eq 1 && $EUID -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo" || warn "sudo 없음 — 시스템 패키지 설치를 건너뛴다"
fi

pkg_install() {
  [[ -n "$PKG" && ( -n "$SUDO" || $EUID -eq 0 ) ]] || { warn "패키지 관리자/권한 없음 — 건너뜀: $*"; return 0; }
  case "$PKG" in
    apt)    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "$@" >/dev/null ;;
    dnf)    $SUDO dnf install -y -q "$@" >/dev/null ;;
    yum)    $SUDO yum install -y -q "$@" >/dev/null ;;
    pacman) $SUDO pacman -S --noconfirm --needed "$@" >/dev/null ;;
  esac
}

# ══ 1. 시스템 패키지 ════════════════════════════════════════════════════
step "시스템 패키지"
if [[ -z "$PKG" ]]; then
  warn "알 수 없는 배포판 — python3(≥$PY_MIN)·pip·venv·git·curl·jq·sqlite3 를 직접 설치하라"
else
  info "패키지 관리자: $PKG"
  case "$PKG" in
    apt)
      [[ -n "$SUDO" || $EUID -eq 0 ]] && { DEBIAN_FRONTEND=noninteractive $SUDO apt-get update -qq >/dev/null || true; }
      pkg_install python3 python3-pip python3-venv python3-dev build-essential \
                  git curl jq sqlite3 ca-certificates gnupg ;;
    dnf|yum)
      pkg_install python3 python3-pip python3-devel gcc gcc-c++ make \
                  git curl jq sqlite ca-certificates ;;
    pacman)
      pkg_install python python-pip base-devel git curl jq sqlite ca-certificates ;;
  esac
  ok "시스템 패키지 준비"
fi

# python 버전 확인
command -v python3 >/dev/null 2>&1 || die "python3 가 없다. 수동 설치 후 다시 실행하라."
PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
python3 -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)" \
  || die "Python $PY_VER — $PY_MIN 이상이 필요하다"
ok "Python $PY_VER"

# ══ 2. Node.js (P4/P5 웹앱) ═════════════════════════════════════════════
if [[ $WITH_NODE -eq 1 ]]; then
  step "Node.js (홈페이지·그룹웨어·픽셀오피스용)"
  if command -v node >/dev/null 2>&1; then
    ok "Node $(node --version) 이미 설치됨"
  elif [[ "$PKG" == "apt" && ( -n "$SUDO" || $EUID -eq 0 ) ]]; then
    info "NodeSource ${NODE_MAJOR}.x 등록 중…"
    if curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | $SUDO -E bash - >/dev/null 2>&1; then
      pkg_install nodejs && ok "Node $(node --version 2>/dev/null || echo '설치됨')"
    else
      warn "NodeSource 실패 — 배포판 기본 nodejs 로 대체"
      pkg_install nodejs npm || warn "Node 설치 실패 (P4 착수 전까지는 불필요)"
    fi
  else
    pkg_install nodejs npm || warn "Node 설치 실패 (P4 착수 전까지는 불필요)"
  fi
fi

# ══ 3. Docker (el34 연동) ═══════════════════════════════════════════════
if [[ $WITH_DOCKER -eq 1 ]]; then
  step "Docker"
  if command -v docker >/dev/null 2>&1; then
    ok "Docker $(docker --version 2>/dev/null | cut -d, -f1) 이미 설치됨"
  elif [[ -n "$SUDO" || $EUID -eq 0 ]]; then
    info "get.docker.com 설치 스크립트 실행…"
    if curl -fsSL https://get.docker.com | $SUDO sh >/dev/null 2>&1; then
      ok "Docker 설치 완료"
      $SUDO usermod -aG docker "${SUDO_USER:-$USER}" 2>/dev/null \
        && warn "docker 그룹에 추가됨 — 재로그인 후 sudo 없이 사용 가능"
    else
      warn "Docker 설치 실패 — el34 연동은 수동 설치 필요"
    fi
  else
    warn "권한 없음 — Docker 설치 건너뜀"
  fi
fi

# ══ 4. Python 가상환경 + dawn-core ══════════════════════════════════════
step "Python 가상환경 · dawn-core"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv || die "venv 생성 실패 (python3-venv 설치 확인)"
  ok "가상환경 생성 → .venv"
else
  ok "기존 가상환경 사용 → .venv"
fi
./.venv/bin/python -m pip install -q --upgrade pip setuptools wheel
# 의존 순서대로: core → agents(하네스) → aoc(관제). 뒤엣것이 앞엣것을 의존한다.
./.venv/bin/python -m pip install -q -e "packages/dawn_core[dev]" \
  || die "dawn-core 설치 실패"
[[ -d agents ]] && { ./.venv/bin/python -m pip install -q -e "agents[dev]" \
  || die "dawn-agents 설치 실패"; }
[[ -d aoc ]] && { ./.venv/bin/python -m pip install -q -e "aoc[dev]" \
  || die "dawn-aoc 설치 실패"; }
[[ -d apps/groupware ]] && { ./.venv/bin/python -m pip install -q -e "apps/groupware[dev]" \
  || die "dawn-web 설치 실패"; }
[[ -d biz ]] && { ./.venv/bin/python -m pip install -q -e "biz[dev]" \
  || die "dawn-biz 설치 실패"; }
ok "설치: $(./.venv/bin/python - <<'PY'
mods = []
for name in ("dawn_core", "dawn_agents", "dawn_aoc", "dawn_groupware", "dawn_biz"):
    try:
        m = __import__(name)
        mods.append(f"{name.replace('_', '-')} {getattr(m, '__version__', '?')}")
    except ImportError:
        pass
print(", ".join(mods))
PY
)"

# ══ 5. 시크릿 · 환경 ════════════════════════════════════════════════════
step "시크릿 · 환경 설정"
if [[ ! -f .env ]]; then
  cp .env.example .env
  ok ".env 생성 (템플릿에서). 값을 채워라 — 커밋되지 않는다."
  # el34 가 같은 호스트에 있으면 API 키를 자동 연결
  if [[ -f "$HOME/el34/.env" ]] && grep -q '^API_KEY=' "$HOME/el34/.env"; then
    K="$(grep '^API_KEY=' "$HOME/el34/.env" | head -1 | cut -d= -f2-)"
    python3 - "$K" <<'PY'
import sys, pathlib
key = sys.argv[1].strip()
p = pathlib.Path(".env")
p.write_text(
    "\n".join(
        (f"EL34_API_KEY={key}" if ln.startswith("EL34_API_KEY=") else ln)
        for ln in p.read_text(encoding="utf-8").splitlines()
    ) + "\n",
    encoding="utf-8",
)
PY
    ok "el34 API 키를 ~/el34/.env 에서 자동 연결"
  fi
else
  ok ".env 이미 존재 (덮어쓰지 않음)"
fi
chmod 600 .env 2>/dev/null || true

# ══ 6. git 훅 (시크릿 스캔) ═════════════════════════════════════════════
step "git 훅 · 시크릿 스캔"
if [[ ! -d .git ]]; then
  git init -q && ok "git 저장소 초기화"
fi
bash scripts/install-hooks.sh 2>&1 | sed 's/^/  /'

# ══ 7. 검증 ═════════════════════════════════════════════════════════════
step "검증 — 레지스트리 · 통제 평면 · 테스트"
FAILED=0
./.venv/bin/python -m dawn_core.cli registry  || FAILED=1
echo
./.venv/bin/python -m dawn_core.cli compile --all || FAILED=1
echo
./.venv/bin/python -m dawn_core.cli lint      || FAILED=1
./.venv/bin/python -m pytest packages -q 2>&1 | tail -3
[[ ${PIPESTATUS[0]:-0} -eq 0 ]] || FAILED=1

# ══ 8. el34 연동 ════════════════════════════════════════════════════════
if [[ $WITH_EL34 -eq 1 ]]; then
  step "el34 연동 확인"
  if ./.venv/bin/python infra/el34/healthcheck.py --zones; then
    ok "el34 도달 — AOC 수집 계층의 전제 충족"
  else
    warn "el34 미도달 — P0 는 진행 가능하나 P3(관제) 전에 해결해야 한다"
    info "el34 가 이 호스트에 있다면:  make el34-assessor-up"
  fi
fi

# ── 마무리 ──────────────────────────────────────────────────────────────
echo
if [[ $FAILED -eq 0 ]]; then
  printf '%s╭──────────────────────────────────────────────────────────╮%s\n' "$G" "$Z"
  printf '%s│%s  ✔ 부트스트랩 완료                                      %s│%s\n' "$G" "$Z" "$G" "$Z"
  printf '%s╰──────────────────────────────────────────────────────────╯%s\n' "$G" "$Z"
else
  printf '%s✘ 일부 검증 실패 — 위 출력을 확인하라%s\n' "$R" "$Z"
fi

cat <<EOF

  ${B}다음${Z}
    source .venv/bin/activate
    make help                 사용 가능한 명령
    make check                lint · test · 통제 평면 · 레지스트리
    dawn registry --tree      조직도
    dawn gate <agent-id>      그 에이전트가 실제로 뭘 할 수 있는지
    make office-bg            픽셀 오피스 관제 콘솔  (:8800)
    make web-bg               홈페이지(:8810) + 그룹웨어(:8811)
    make portal-bootstrap     그룹웨어 첫 관리자 계정 — 비밀번호가 1회만 출력된다
    make aoc                  관제 1회전 — 수집 → 탐지 → 트리아지
    make biz-seed             업무 데모 데이터 (문서·CRM·프로젝트·경리)

  ${B}읽을 것${Z}
    COMPANY.md                            회사 헌법 (모든 에이전트에 주입됨)
    docs/governance/CONTROL_PLANE.md      에이전트 통제 4계층 사용법
    docs/START_HERE.md                    구축 순서 P0→P6
    BUILD_LOG.md                          어디까지 왔는지

EOF
exit $FAILED
