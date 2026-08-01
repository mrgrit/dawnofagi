"""dawn_ops — 통합·검증·운영 (P6).

전 계층을 하나의 회사로 가동하고, **우리 자신을 공격해** 관제를 검증한다.

    e2e         요구 → 에이전트 → 업무 → 관제 → 축적. **구간별로** 검사한다
    redteam     자사 에이전트에 인젝션·jailbreak. 놓친 것이 본체다
    rehearsal   인시던트 3축 + 비가역 대응 3종 실증 (kill·자격증명 회수·롤백)
    tenant      멀티테넌트 준비 — 실제로 고객 테넌트를 만들어 보고 지운다

원칙:

* **모델 거절은 방어로 세지 않는다.** 게이트가 잡아야 게이트다 —
  모델이 바뀌면 뚫린다.
* **놓친 공격마다 보강 제안이 나와야 완료다.** 커버리지 숫자만으로는 부족하다.
* **리허설은 원상복구한다.** 안 눌러본 버튼은 사고 때도 안 눌린다.
"""

from .e2e import E2EResult, Hop
from .e2e import run as run_e2e
from .redteam import ATTACKS, AttackResult, coverage, hardening_proposals, static_scan
from .rehearsal import HardAction, Rehearsal
from .rehearsal import run_all as run_rehearsal
from .tenant import ONBOARDING_STEPS, TenantReport
from .tenant import run as check_tenant

__version__ = "0.1.0"

__all__ = [
    "ATTACKS",
    "ONBOARDING_STEPS",
    "AttackResult",
    "E2EResult",
    "HardAction",
    "Hop",
    "Rehearsal",
    "TenantReport",
    "__version__",
    "check_tenant",
    "coverage",
    "hardening_proposals",
    "run_e2e",
    "run_rehearsal",
    "static_scan",
]
