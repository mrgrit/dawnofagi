"""dawn_groupware — 공개 홈페이지 + 사내 그룹웨어 (P4).

두 개의 **다른 앱**이다. 존이 다르고, 신뢰 경계가 다르고, 프로세스가 다르다.

    build_site()    공개 (L0, dmz 앞단)  — 세션·인증·내부 DB 없음
    build_portal()  사내 (user/int 존)   — 인증·조직 권한·감사

그룹웨어가 존재하는 이유는 게시판이 아니라 **관문**이다:

* 승인 큐 — 에이전트가 비가역 행동 앞에서 멈춰 사람을 기다리는 곳
* EG 조정 — 사람이 에이전트 행동을 바꾸는 유일한 정식 경로 (코드가 아니라 EG)
* 관제 연동 — P3 콘솔·픽셀 오피스로 가는 입구

지키는 것:

* **권한 = 조직 × 능력.** 능력을 줘도 자기 조직 트리 밖은 승인 못 한다.
* **EG 변경은 검증을 통과해야만 반영된다.** 실패하면 시드가 자동 롤백된다.
* **감사 로그는 append-only.** 지우는 API 를 만들지 않았다.
* **공개 사이트는 내부에 손이 닿지 않는다.** 임포트 경로 자체가 없다.
"""

from .app import build_portal, build_site
from .audit import AuditLog
from .auth import CAPABILITIES, User, UserStore, can_approve, org_chain
from .egedit import EDITABLE, ChangeResult, EGEditError, EGEditor
from .store import SECURITY_LEVELS, Store

__version__ = "0.1.0"

__all__ = [
    "CAPABILITIES",
    "EDITABLE",
    "SECURITY_LEVELS",
    "AuditLog",
    "ChangeResult",
    "EGEditError",
    "EGEditor",
    "Store",
    "User",
    "UserStore",
    "__version__",
    "build_portal",
    "build_site",
    "can_approve",
    "org_chain",
]
