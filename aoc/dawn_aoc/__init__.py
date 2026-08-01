"""dawn_aoc — AI Operations Center (AOC) 관제 시스템.

5계층 (01_aoc_architecture):

    [1] 수집    collect     P2 스팬 → run 정규화, PII 재검증, 트레이스 레이크
    [2] 탐지    detect      동기 가드레일 3종(입력·행동·출력) + 비동기(judge·이상탐지)
    [3] 트리아지 triage      심각도 = 행동 비가역성 × EG 자산 등급, 플레이북 권고
        대응    respond     가역=자동 집행 / 비가역=HITL 승인 큐
        제어    killswitch  별도 계층 — 에이전트가 수정 불가, stop ≠ de-authorize
    [4] 거버넌스 kpi         KPI 실측 + 자율화 등급 승급/강등
    [5] 시각화  console      픽셀 오피스가 읽는 단일 상태 스냅샷

원칙:

* **관제는 별도 계층이다.** 감시받는 에이전트가 자기 감시 결과를 못 고친다.
* **judge 는 감시 대상과 다른 모델.** 담합 방지 (`detect.pick_judge_model`).
* **미탐이 오탐보다 비싸다.** 모르는 술어·미분류 자산은 보수적으로 판정한다.
* **시각화는 실측 텔레메트리에만 바인딩.** 임의 데이터로 채우는 칸은 없다.
"""

from .collect import Run, TraceLake, check_masking
from .console import build_state, scan, write_state
from .detect import (
    Detection,
    GuardrailResult,
    JudgeResult,
    action_gate_from_run,
    anomalies,
    input_gate,
    judge,
    judge_to_detections,
    output_gate,
    pick_judge_model,
)
from .killswitch import ControlState, KillSwitch
from .kpi import KPI, AutonomyReview, compute, registry_view, review_autonomy
from .respond import ActionResult, Responder
from .triage import PLAYBOOKS, Case, CaseStore, recommend, triage

__version__ = "0.1.0"

__all__ = [
    "KPI",
    "PLAYBOOKS",
    "ActionResult",
    "AutonomyReview",
    "Case",
    "CaseStore",
    "ControlState",
    "Detection",
    "GuardrailResult",
    "JudgeResult",
    "KillSwitch",
    "Responder",
    "Run",
    "TraceLake",
    "__version__",
    "action_gate_from_run",
    "anomalies",
    "build_state",
    "check_masking",
    "compute",
    "input_gate",
    "judge",
    "judge_to_detections",
    "output_gate",
    "pick_judge_model",
    "recommend",
    "registry_view",
    "review_autonomy",
    "scan",
    "triage",
    "write_state",
]
