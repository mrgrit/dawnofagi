"""dawn_biz — 업무 시스템 (P5).

문서·지식 / CRM / 프로젝트·이슈 / 경리·자산.

**각 시스템의 업무를 에이전트가 수행한다.** 새 실행 계층을 만들지 않고
P2 워커 루프에 업무 스킬을 얹었다 — 그래야 업무 에이전트도 행동 게이트를
통과하고, 스팬을 뱉고, P3 관제에 나타난다. 업무 시스템만 따로 도는 순간
그 부분은 관제 밖이다.

지키는 것:

* **모든 업무 행은 `eg_asset` 을 선언한다.** 어느 EG 자산에 속하는지 모르면
  관제가 심각도를 못 매기고 픽셀 오피스가 방을 못 정한다.
* **EG 에 밀어 넣지 않고 대조한다** (`egsync.check`). 업무 시스템이 EG 에
  노드를 만들면 EG 가 업무 데이터의 사본이 된다.
* **L3(경비·급여)는 로컬 모델 전용.** `touches_l3=True` 로 호출 전에 막는다.
* **비가역 업무 스킬은 실행부가 없다** — 계약 체결·자산 폐기.
* **테넌트에 묶인 커넥션만 존재한다.** 조회 함수가 tenant 를 인자로 안 받는다.
"""

from .egsync import AssetCheck
from .egsync import check as eg_check
from .events import business_dispatcher, ingest_inquiries, run_event
from .seed import seed_all
from .skills import build_registry
from .skills import register as register_skills
from .store import KIND_ASSET, KIND_LEVEL, BizStore, Row
from .workers import (
    CATEGORIES,
    WorkResult,
    assignable,
    coordinate_project,
    handle_expense,
    handle_inquiry,
)

__version__ = "0.1.0"

__all__ = [
    "CATEGORIES",
    "KIND_ASSET",
    "KIND_LEVEL",
    "AssetCheck",
    "BizStore",
    "Row",
    "WorkResult",
    "__version__",
    "assignable",
    "build_registry",
    "business_dispatcher",
    "coordinate_project",
    "eg_check",
    "handle_expense",
    "handle_inquiry",
    "ingest_inquiries",
    "register_skills",
    "run_event",
    "seed_all",
]
