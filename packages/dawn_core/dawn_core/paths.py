"""저장소 경로 해석.

모노레포 루트를 어디서 실행하든 찾아낸다 (COMPANY.md 를 앵커로 사용).
환경변수 DAWN_ROOT 가 있으면 그것이 최우선.
"""

from __future__ import annotations

import os
from pathlib import Path

ANCHOR = "COMPANY.md"


def find_root(start: Path | str | None = None) -> Path:
    """모노레포 루트를 찾는다.

    우선순위: DAWN_ROOT 환경변수 → start(기본 cwd)에서 위로 탐색 → 이 파일 기준 상대경로.
    """
    env = os.getenv("DAWN_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / ANCHOR).is_file():
            return p
        raise FileNotFoundError(f"DAWN_ROOT={p} 에 {ANCHOR} 가 없다")

    cur = Path(start).resolve() if start else Path.cwd().resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ANCHOR).is_file():
            return cand

    # 설치된 패키지에서 호출된 경우: packages/dawn_core/dawn_core/paths.py → 3단계 위
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / ANCHOR).is_file():
            return cand

    raise FileNotFoundError(
        f"{ANCHOR} 를 찾지 못했다. 저장소 안에서 실행하거나 DAWN_ROOT 를 설정하라."
    )


class Paths:
    """루트 기준 표준 경로 모음."""

    def __init__(self, root: Path | str | None = None) -> None:
        if root is not None:
            cand = Path(root).resolve()
            if (cand / ANCHOR).is_file():
                self.root = cand
                return
        self.root = find_root(root)

    # 통제 평면
    @property
    def company_md(self) -> Path:
        return self.root / "COMPANY.md"

    @property
    def root_gate(self) -> Path:
        return self.root / "org" / "gate.yaml"

    # 레지스트리
    @property
    def org(self) -> Path:
        return self.root / "org"

    @property
    def businesses(self) -> Path:
        return self.org / "businesses"

    @property
    def divisions(self) -> Path:
        return self.org / "divisions"

    @property
    def agents(self) -> Path:
        return self.org / "agents"

    @property
    def work(self) -> Path:
        return self.root / "work"

    # 산출물
    @property
    def build_dir(self) -> Path:
        return self.root / "var" / "control-plane"

    def __repr__(self) -> str:  # pragma: no cover
        return f"Paths(root={self.root})"
