"""테스트가 **운영 상태를 바꾸지 않게** 하는 공용 장치.

이 저장소의 테스트는 실 트리에서 돈다 (`Paths().root`). 실 매니페스트·EG·
레지스트리를 검증해야 하므로 의도된 설계다. 대가는 **쓰기가 일어나는 계층은
세션이 끝날 때 원상복구해야 한다**는 것이다.

## 왜 `conftest.py` 하나로 안 되나

하위 패키지마다 자기 `pyproject.toml` 에 pytest 설정이 있다. 그래서
`pytest apps/groupware` 처럼 한 패키지만 돌리면 **rootdir 가 그 디렉터리가 되고
저장소 루트의 `conftest.py` 는 로드되지 않는다**(실측 2026-08-03).

보호 장치가 "어떻게 실행하느냐"에 따라 켜졌다 꺼졌다 하면 그건 보호가 아니다.
그래서 로직을 여기 두고, 루트와 **쓰는 패키지 양쪽**의 conftest 가 이걸 부른다.

## 무엇을 지키나

`RESTORE` 에 든 파일은 세션 전에 뜨고 후에 되돌린다. 판단 기준은 하나다 —
**테스트가 바꾸면 사람이 시스템을 못 쓰게 되는가.**

실측: `test_web.py` 의 `_login()` 은 호출할 때마다 비밀번호를 임의로 새로
세팅한다(픽스처에 상수를 두면 커밋된 자격증명이 되므로 그 판단은 옳다).
문제는 그 쓰기가 **실 계정 저장소**로 간다는 것 — `pytest` 한 번에 `admin`
포함 7개 계정이 아무도 모르는 값이 되어 운영자가 로그인하지 못했다.
**테스트는 통과하는데 시스템은 못 쓰는 상태**가 가장 나쁘다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# 테스트가 쓰지만 **운영 상태이기도 한** 파일 (루트 기준 상대경로).
RESTORE: tuple[Path, ...] = (
    Path("var") / "groupware" / "users.json",
)


def disable_judgment_collection() -> None:
    """판단 수집을 끈다 (P8).

    테스트가 결재·승인을 흉내내면 그게 그대로 사람의 판단 말뭉치가 되고,
    트윈은 사유 "승인" 을 진짜 판단으로 배운다. 실측으로 판단 5건 중 4건이
    그렇게 들어와 있었다.
    """
    os.environ.setdefault("DAWN_JUDGMENT_COLLECT", "0")


def snapshot(root: Path) -> dict[Path, bytes | None]:
    """복원 대상을 뜬다. 없는 파일은 `None` — 테스트가 만들면 지워야 한다."""
    out: dict[Path, bytes | None] = {}
    for rel in RESTORE:
        p = Path(root) / rel
        out[p] = p.read_bytes() if p.is_file() else None
    return out


def restore(saved: dict[Path, bytes | None]) -> list[str]:
    """되돌린다. **테스트가 실패해도 부른다.** 바뀐 것만 손댄다."""
    changed = []
    for p, data in saved.items():
        if data is None:
            if p.is_file():
                p.unlink()
                changed.append(str(p))
            continue
        if not p.is_file() or p.read_bytes() != data:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".restore")
            tmp.write_bytes(data)
            shutil.move(str(tmp), str(p))    # 중간에 죽어도 반쪽 파일이 안 남는다
            changed.append(str(p))
    return changed


def operator_state_fixture():
    """세션 스코프 autouse 픽스처 본체. conftest 에서 감싸 쓴다.

    `pytest.fixture` 데코레이터를 여기서 안 붙이는 이유: conftest 마다
    스코프·autouse 를 명시하게 두는 편이 무엇이 켜져 있는지 읽기 쉽다.
    """
    from .paths import Paths

    disable_judgment_collection()
    saved = snapshot(Paths().root)
    try:
        yield saved
    finally:
        restore(saved)


__all__ = ["RESTORE", "disable_judgment_collection", "operator_state_fixture",
           "restore", "snapshot"]
