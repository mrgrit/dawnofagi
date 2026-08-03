"""테스트 세션 전역 설정.

이 저장소의 테스트는 **실 트리에서 돈다** (`Paths().root`). 의도된 설계지만,
쓰기가 일어나는 계층에는 그만큼 조심해야 한다.

판단 수집(P8)을 여기서 끈다. 테스트가 결재·승인을 흉내내면 그게 그대로
사람의 판단 말뭉치가 되고, 트윈은 사유 "승인" 을 진짜 판단으로 배운다.
실측으로 판단 5건 중 4건이 그렇게 들어와 있었다.

판단 로직 자체는 `packages/dawn_core/tests/test_judgment.py` 가 임시 DB 로
검증한다 — 거기서는 이 스위치를 켜고 돌린다.
"""

import os

os.environ.setdefault("DAWN_JUDGMENT_COLLECT", "0")
