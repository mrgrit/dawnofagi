"""JSONL 읽기·쓰기 — **`splitlines()` 를 쓰지 않는다.**

파이썬 `str.splitlines()` 는 `\\n` 말고도 나눈다:

    \\x0b \\x0c \\x1c \\x1d \\x1e \\x85(NEL) \\u2028(LS) \\u2029(PS)

JSON 문자열 안에 이 문자들이 **원문 그대로** 들어갈 수 있다 (`json.dumps` 는
`ensure_ascii=False` 일 때 `\\u2028` 를 이스케이프하지 않는다). 그러면 한 레코드가
두 줄로 쪼개지고, 파싱은 실패하고, 그 실패는 조용히 건너뛰어진다 —
**감사 로그와 트레이스 레이크가 소리 없이 빈다.**

JSONL 의 줄 구분자는 `\\n` 하나뿐이다. 그것만 쓴다.

발견 경위: P5 에서 고객 문의 본문이 latin-1 로 잘못 디코딩돼 `\\x85` 를 포함하게
됐고, 접수함 한 건이 7줄로 쪼개져 전부 파싱 실패했다. 인코딩 버그가 먼저였지만
**깨진 한 줄이 파일 전체를 못 읽게 만든 것**은 이쪽 문제다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def iter_lines(text: str) -> Iterator[str]:
    """`\\n` 으로만 나눈다. 빈 줄은 건너뛴다."""
    for raw in text.split("\n"):
        if raw.strip():
            yield raw


def read(path: Path | str, *, limit: int | None = None,
         on_error: str = "skip") -> list[dict[str, Any]]:
    """JSONL 파일을 읽는다.

    Args:
        limit: 마지막 N줄만.
        on_error: "skip" 이면 깨진 줄을 건너뛴다. "raise" 면 올린다.
                  기본이 skip 인 이유는 append 중 잘린 마지막 줄이 흔하기 때문이다 —
                  다만 **건너뛴 줄 수는 호출부가 알 수 있어야 한다** (`read_counted`).
    """
    rows, _bad = read_counted(path, limit=limit, on_error=on_error)
    return rows


def read_counted(path: Path | str, *, limit: int | None = None,
                 on_error: str = "skip") -> tuple[list[dict[str, Any]], int]:
    """`(레코드, 파싱 실패 줄 수)`. 실패가 몇 줄인지 숨기지 않는다."""
    p = Path(path)
    if not p.is_file():
        return [], 0
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = list(iter_lines(text))
    if limit is not None:
        lines = lines[-limit:]
    out: list[dict[str, Any]] = []
    bad = 0
    for raw in lines:
        try:
            obj = json.loads(raw)
        except ValueError:
            if on_error == "raise":
                raise
            bad += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        else:
            bad += 1
    return out, bad


def append(path: Path | str, record: dict[str, Any]) -> None:
    """한 줄 추가. `O_APPEND` 로 동시 기록이 섞이지 않게 한다.

    본문의 `\\u2028`·`\\u2029`·`\\x85` 는 **이스케이프해서** 쓴다 —
    읽는 쪽이 `\\n` 만 본다는 약속을 쓰는 쪽에서도 지킨다.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    for ch, esc in ((" ", "\\u2028"), (" ", "\\u2029"), ("\x85", "\\u0085")):
        if ch in line:
            line = line.replace(ch, esc)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


__all__ = ["append", "iter_lines", "read", "read_counted"]
