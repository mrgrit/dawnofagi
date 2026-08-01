"""감사 로그 — append-only.

그룹웨어는 **사람이 에이전트에 개입하는 통로**다. 그 통로에서 일어난 일은
전부 남아야 한다. 누가 승인했나 · 누가 EG 를 고쳤나 · 누가 로그인에 실패했나.

파일 하나(`var/groupware/audit.jsonl`)에 붙여 쓴다. 수정·삭제 API 는 만들지 않는다 —
지울 수 있는 감사 로그는 감사 로그가 아니다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 감사 로그에 절대 들어가면 안 되는 키 (값이 통째로 마스킹된다)
_SECRET_KEYS = {"password", "passwd", "pw", "token", "secret", "api_key", "session"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scrub(value: Any, key: str = "") -> Any:
    if key.lower() in _SECRET_KEYS:
        return "***"
    if isinstance(value, dict):
        return {k: _scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + f"…(+{len(value) - 2000}자)"
    return value


class AuditLog:
    """append-only 감사 로그."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "var" / "groupware" / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, *, actor: str = "-", target: str = "",
              result: str = "ok", ip: str = "", **detail: Any) -> dict[str, Any]:
        rec = {
            "at": _now(), "action": action, "actor": actor,
            "target": target, "result": result, "ip": ip,
            "detail": _scrub(detail),
        }
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        # O_APPEND — 동시 요청이 겹쳐도 줄이 섞이지 않는다.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return rec

    def tail(self, limit: int = 100, *, action_prefix: str = "",
             actor: str = "") -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if action_prefix and not rec.get("action", "").startswith(action_prefix):
                continue
            if actor and rec.get("actor") != actor:
                continue
            out.append(rec)
        return out[-limit:][::-1]


__all__ = ["AuditLog"]
