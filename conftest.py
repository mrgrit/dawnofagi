"""테스트 세션 전역 설정 — 저장소 전체를 한 번에 돌릴 때.

로직은 [`dawn_core.testsupport`](packages/dawn_core/dawn_core/testsupport.py) 에 있다.
여기 두면 **`pytest apps/groupware` 처럼 한 패키지만 돌릴 때 로드되지 않기**
때문이다 — 하위 패키지마다 자기 pyproject 가 있어 rootdir 이 거기로 바뀐다.
쓰는 패키지에도 같은 conftest 가 있다.
"""

import pytest
from dawn_core.testsupport import disable_judgment_collection, operator_state_fixture

disable_judgment_collection()          # 수집 훅이 import 시점에 읽는다


@pytest.fixture(scope="session", autouse=True)
def _operator_state():
    yield from operator_state_fixture()
