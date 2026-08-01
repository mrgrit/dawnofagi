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
	@$(PY) -m ruff check packages infra scripts eg agents

.PHONY: fmt
fmt:  ## ruff format (수정)
	@$(PY) -m ruff format packages infra eg agents scripts
	@$(PY) -m ruff check --fix packages infra scripts eg agents

.PHONY: test
test:  ## pytest
	@$(PY) -m pytest packages agents -q

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

# ══ EG (Experience Graph — 회사의 뇌) ═══════════════════════════════════
BASTION_SEED ?= $(HOME)/el34/bastion/src/data/seed/bastion_graph_seed.db

.PHONY: eg-load
eg-load:  ## EG 시드 주입 (bastion 런타임 EG 위에 거버넌스 계층)
	@$(PY) -m dawn_core.cli eg load --from-bastion $(BASTION_SEED)

.PHONY: eg-validate
eg-validate:  ## EG 검증 — 무결성 + 핵심 순회 실증 (오류 0 이어야 함)
	@$(PY) eg/validate.py --db var/eg/bastion_graph.db

.PHONY: eg-stats
eg-stats:  ## EG 현황 (계층·노드·엣지)
	@$(PY) -m dawn_core.cli eg stats

.PHONY: eg-org
eg-org:  ## 조직 프로파일 = 개입 지점 — make eg-org O=org:ccc
	@test -n "$(O)" || { echo "사용법: make eg-org O=org:ccc"; exit 2; }
	@$(PY) -m dawn_core.cli eg org $(O)

.PHONY: eg-search
eg-search:  ## eg_search — make eg-search Q="로컬 모델"
	@test -n "$(Q)" || { echo ' 사용법: make eg-search Q="로컬 모델"'; exit 2; }
	@$(PY) -m dawn_core.cli eg search "$(Q)"

.PHONY: eg-severity
eg-severity:  ## 전 자산 심각도 (비가역성 × 보안등급)
	@$(PY) -m dawn_core.cli eg severity

.PHONY: eg-bridge
eg-bridge:  ## 통제 평면 ↔ EG 정합성 대조 (어긋나면 실패)
	@$(PY) -m dawn_core.cli eg bridge

.PHONY: eg-routing
eg-routing:  ## 에이전트별 실효 모델 라우팅 표
	@$(PY) -m dawn_core.cli eg routing

.PHONY: eg-snapshot
eg-snapshot:  ## EG 스냅샷 저장 (롤백·감사)
	@$(PY) -m dawn_core.cli eg snapshot --label $${LABEL:-governance}

# ══ 에이전트 하네스 (P2) ════════════════════════════════════════════════
.PHONY: agent-info
agent-info:  ## 에이전트 능력·게이트 조회 — make agent-info A=<id>
	@test -n "$(A)" || { echo "사용법: make agent-info A=ccc-soc-triage-01"; exit 2; }
	@$(PY) -m dawn_agents.cli info $(A)

.PHONY: agent-run
agent-run:  ## 워커 루프 1회 — make agent-run A=<id> T="업무"
	@test -n "$(A)" -a -n "$(T)" || { echo ' 사용법: make agent-run A=<id> T="업무"'; exit 2; }
	@$(PY) -m dawn_agents.cli run $(A) "$(T)"

.PHONY: agent-emit
agent-emit:  ## 이벤트 발생 → 훅 기동 — make agent-emit E=siem.alert
	@test -n "$(E)" || { echo "사용법: make agent-emit E=siem.alert"; exit 2; }
	@$(PY) -m dawn_agents.cli emit $(E) --dry-run

.PHONY: hitl
hitl:  ## HITL 승인 큐
	@$(PY) -m dawn_agents.cli hitl list

.PHONY: trace
trace:  ## 최근 OTel 스팬 트리
	@$(PY) -m dawn_agents.cli trace

# ══ 모델 (사내 GPU — 이 호스트에 GPU 없음) ══════════════════════════════
.PHONY: gpu-check
gpu-check:  ## 사내 GPU 서버(ollama) 도달 확인 — VPN 필요. L3 업무의 전제
	@$(PY) infra/gpu/check.py

.PHONY: gpu-test
gpu-test:  ## GPU 서버에 실제 추론 1회 (경로 검증 — 가장 작은 모델 사용)
	@$(PY) infra/gpu/check.py --generate

.PHONY: vpn
vpn:  ## 사내 GPU VPN 연결 (스플릿 라우팅 — el34 보존). 사람이 실행
	@bash infra/gpu/vpn-connect.sh

.PHONY: vpn-status
vpn-status:  ## VPN 상태 + el34/GPU 경로 확인
	@bash infra/gpu/vpn-connect.sh --status

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
check: lint test registry compile control-lint eg-validate eg-bridge  ## CI와 동일한 전체 검사

.PHONY: verify
verify:  ## P0+P1 자기검증 (DoD + 개입·게이트 실증)
	@bash scripts/verify-p0.sh
	@bash scripts/verify-p1.sh
	@bash scripts/verify-p2.sh

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
