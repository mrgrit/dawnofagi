"""dawn_agents — 에이전트 하네스·워커 루프·행동 게이트 (P2).

telemetry     OTel GenAI 스팬 (P3 수집 계층이 받는다)
skills        skill_preview / skill_run
actiongate    통제평면 × 스킬위험도 × EG → block|require_hitl|warn|log_only
llm           EG 가 고른 모델로 실제 호출 (L3 는 로컬 강제)
hitl          승인 큐 (P4 그룹웨어의 백엔드)
worker        4단계 루프
orchestrator  팀 위임 (리더 무발화 · 검증자 ≠ 생산자)
events        이벤트 구동 훅 + 큐 (상시 폴링 아님)
"""

from __future__ import annotations

from .actiongate import ActionGate, GateDecision
from .config import load_dotenv
from .events import Dispatcher, Event, Handler, WorkQueue, default_dispatcher
from .hitl import Approval, ApprovalQueue
from .llm import LLMClient, LLMError, PolicyViolation, Resolved, resolve
from .orchestrator import Assignment, OrchestratorError, TeamOrchestrator, TeamRun
from .skills import Preview, SkillRegistry, SkillResult, build_default_registry
from .telemetry import GEN_AI_SEMCONV_VERSION, Tracer, get_tracer, mask_pii, reset_tracer
from .worker import CircuitBreaker, Worker, WorkerError, WorkerRun

__version__ = "0.1.0"

load_dotenv()  # 저장소 루트의 .env — 이미 설정된 값은 덮지 않는다

__all__ = [
    "GEN_AI_SEMCONV_VERSION",
    "ActionGate",
    "Approval",
    "ApprovalQueue",
    "Assignment",
    "CircuitBreaker",
    "Dispatcher",
    "Event",
    "GateDecision",
    "Handler",
    "LLMClient",
    "LLMError",
    "OrchestratorError",
    "PolicyViolation",
    "Preview",
    "Resolved",
    "SkillRegistry",
    "SkillResult",
    "TeamOrchestrator",
    "TeamRun",
    "Tracer",
    "WorkQueue",
    "Worker",
    "WorkerError",
    "WorkerRun",
    "__version__",
    "build_default_registry",
    "default_dispatcher",
    "get_tracer",
    "load_dotenv",
    "mask_pii",
    "reset_tracer",
    "resolve",
]
