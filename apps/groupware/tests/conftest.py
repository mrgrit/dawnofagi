"""그룹웨어 테스트 — **실 계정 저장소를 되돌린다.**

`_login()` 이 호출마다 비밀번호를 임의로 새로 세팅한다(픽스처에 상수를 두면
커밋된 자격증명이 되므로 그 판단은 옳다). 다만 그 쓰기가 실 트리로 가므로
세션이 끝나면 되돌려야 한다 — 실측으로 `pytest` 한 번에 `admin` 을 포함해
7개 계정이 아무도 모르는 값이 되어 운영자가 로그인하지 못했다.

저장소 루트 `conftest.py` 에만 두면 `pytest apps/groupware` 로 돌릴 때
rootdir 이 여기가 되어 **로드되지 않는다.** 보호가 실행 방법을 타면 보호가 아니다.
"""

import pytest
from dawn_core.testsupport import disable_judgment_collection, operator_state_fixture

disable_judgment_collection()


@pytest.fixture(scope="session", autouse=True)
def _operator_state():
    yield from operator_state_fixture()
