.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := .venv/bin/python
EL34 ?= $(HOME)/el34

# ══ 도움말 ══════════════════════════════════════════════════════════════
.PHONY: help
help:  ## 이 목록
	@echo ""
	@echo "  the dawn of AGI — 명령"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ══ 환경 ════════════════════════════════════════════════════════════════
.PHONY: setup
setup:  ## 원샷 설치 (fresh Linux 포함) — bootstrap.sh
	@./bootstrap.sh

.PHONY: hooks
hooks:  ## git 훅 재설치 (gitleaks 포함)
	@bash scripts/install-hooks.sh

# ══ 품질 ════════════════════════════════════════════════════════════════
.PHONY: lint
lint:  ## ruff lint
	@$(PY) -m ruff check packages infra scripts

.PHONY: fmt
fmt:  ## ruff format (수정)
	@$(PY) -m ruff format packages infra
	@$(PY) -m ruff check --fix packages infra scripts

.PHONY: test
test:  ## pytest
	@$(PY) -m pytest packages -q

# ══ 레지스트리 · 통제 평면 ══════════════════════════════════════════════
.PHONY: registry
registry:  ## 조직·사업 레지스트리 검증 + 조직도
	@$(PY) -m dawn_core.cli registry --tree

.PHONY: control-lint
control-lint:  ## Control Readiness Score (합격선 80)
	@$(PY) -m dawn_core.cli lint

.PHONY: compile
compile:  ## 전 에이전트 통제 평면 컴파일
	@$(PY) -m dawn_core.cli compile --all

.PHONY: bundles
bundles:  ## 통제 평면 번들 생성 → var/control-plane/
	@$(PY) -m dawn_core.cli compile --all --write

.PHONY: gate
gate:  ## 에이전트 실효 게이트 조회 — make gate A=<agent-id>
	@test -n "$(A)" || { echo "사용법: make gate A=ccc-soc-triage-01"; exit 2; }
	@$(PY) -m dawn_core.cli gate $(A)

.PHONY: prompt
prompt:  ## 에이전트 실효 시스템 프롬프트 출력 — make prompt A=<agent-id>
	@test -n "$(A)" || { echo "사용법: make prompt A=ccc-soc-triage-01"; exit 2; }
	@$(PY) -m dawn_core.cli compile $(A) --prompt

# ══ el34 ════════════════════════════════════════════════════════════════
.PHONY: health
health:  ## el34 Assessor 헬스체크 (+ 존 도달성)
	@$(PY) infra/el34/healthcheck.py --zones

.PHONY: el34-assessor-up
el34-assessor-up:  ## el34 Assessor 기동 (el34 저장소는 수정하지 않음)
	@cd $(EL34) && docker compose \
	  -f docker-compose.yaml \
	  -f $(CURDIR)/infra/el34/compose.assessor.yaml \
	  --profile assessor up -d assessor

.PHONY: ci-enable
ci-enable:  ## CI 활성화 — infra/ci/*.yml → .github/workflows/ (PAT 에 workflow 스코프 필요)
	@mkdir -p .github/workflows
	@cp infra/ci/github-actions-ci.yml .github/workflows/ci.yml
	@echo "→ .github/workflows/ci.yml 생성. git add & commit & push 하라."
	@echo "  push 가 거부되면 PAT 에 'workflow' 스코프를 추가하라 (infra/ci/README.md)."

# ══ 통합 ════════════════════════════════════════════════════════════════
.PHONY: check
check: lint test registry compile control-lint  ## CI와 동일한 전체 검사

.PHONY: verify
verify:  ## P0 자기검증 (DoD + 개입·게이트 실증)
	@bash scripts/verify-p0.sh

.PHONY: secrets
secrets:  ## 저장소 전체 시크릿 스캔
	@if [ -x bin/gitleaks ]; then bin/gitleaks detect --redact --no-banner --config .gitleaks.toml; \
	 elif command -v gitleaks >/dev/null; then gitleaks detect --redact --no-banner --config .gitleaks.toml; \
	 else echo "gitleaks 없음 — make hooks"; exit 1; fi

.PHONY: clean
clean:  ## 캐시·빌드 산출물 정리 (.venv 는 유지)
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) \
	   -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@rm -rf var/control-plane
	@echo "정리 완료"
