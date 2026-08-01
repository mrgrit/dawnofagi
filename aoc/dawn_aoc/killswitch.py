"""킬 스위치 — **별도 제어 계층**. 에이전트가 수정할 수 없다.

01_aoc_architecture: "Kill switch는 별도 제어 계층(에이전트가 수정 불가), stop≠de-authorize."

두 가지를 구조로 강제한다:

1. **에이전트는 이 파일을 못 건드린다.** `ctl.*` 는 전사 gate 에서 deny 되어 있고,
   여기 쓰기는 `ctl.modify_kill_switch` 스킬을 통해서만 가능한데 그 스킬은
   실행부가 없다. 코드 경로 자체가 없다.

2. **stop 과 de-authorize 를 분리한다.**
   `pause`  — 실행만 멈춘다. 권한 그대로. 되돌릴 수 있다.
   `kill`   — 실행 중단 + 자율화 A0 강등. 되돌리려면 사람이 명시적으로 해제.
   `revoke` — 자격증명 무효화. 별도 행동이다.

상태 파일은 `var/aoc/control/` 에 있고, 워커는 **기동 시 여기를 먼저 본다**.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATES = ("running", "paused", "killed", "isolated")


@dataclass
class ControlState:
    """에이전트 하나의 제어 상태."""

    agent_id: str
    state: str = "running"
    reason: str = ""
    case_id: str = ""
    by: str = ""
    at: str = ""
    credentials_revoked: bool = False
    blocked_tools: list[str] = field(default_factory=list)
    autonomy_override: str = ""      # kill 시 A0 로 강등
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def can_run(self) -> bool:
        return self.state == "running"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def line(self) -> str:
        icon = {"running": "▶", "paused": "⏸", "killed": "⛔", "isolated": "🔒"}[self.state]
        extra = []
        if self.credentials_revoked:
            extra.append("자격증명 회수됨")
        if self.blocked_tools:
            extra.append(f"차단 도구 {len(self.blocked_tools)}")
        if self.autonomy_override:
            extra.append(f"자율화→{self.autonomy_override}")
        return (f"{icon} {self.agent_id:<24} {self.state:<9} "
                f"{self.reason[:40]}{'  [' + ', '.join(extra) + ']' if extra else ''}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KillSwitch:
    """제어 계층. 워커는 기동 전에 `can_run()` 을 물어본다."""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "var" / "aoc" / "control"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return self.dir / f"{agent_id}.json"

    def get(self, agent_id: str) -> ControlState:
        p = self._path(agent_id)
        if not p.is_file():
            return ControlState(agent_id=agent_id)
        d = json.loads(p.read_text(encoding="utf-8"))
        return ControlState(**{k: v for k, v in d.items() if k in ControlState.__annotations__})

    def _save(self, st: ControlState, action: str, by: str, reason: str,
              case_id: str = "") -> ControlState:
        st.history.append({
            "action": action, "at": _now(), "by": by,
            "reason": reason, "case_id": case_id, "resulting_state": st.state,
        })
        st.at = _now()
        st.by = by
        st.reason = reason
        st.case_id = case_id or st.case_id
        self._path(st.agent_id).write_text(
            json.dumps(st.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return st

    # ── 대응 액션 ───────────────────────────────────────────────────────
    def pause(self, agent_id: str, *, reason: str, by: str = "aoc",
              case_id: str = "") -> ControlState:
        """실행만 멈춘다. **권한은 그대로** — stop ≠ de-authorize."""
        st = self.get(agent_id)
        st.state = "paused"
        return self._save(st, "pause", by, reason, case_id)

    def kill(self, agent_id: str, *, reason: str, by: str = "aoc",
             case_id: str = "") -> ControlState:
        """실행 중단 + 자율화 A0 강등. 사람이 명시적으로 풀어야 한다."""
        st = self.get(agent_id)
        st.state = "killed"
        st.autonomy_override = "A0"
        return self._save(st, "kill", by, reason, case_id)

    def isolate(self, agent_id: str, *, reason: str, by: str = "aoc",
                case_id: str = "") -> ControlState:
        """샌드박스 격리 — 외부 자산 접근 차단."""
        st = self.get(agent_id)
        st.state = "isolated"
        return self._save(st, "isolate", by, reason, case_id)

    def revoke_credentials(self, agent_id: str, *, reason: str, by: str = "aoc",
                           case_id: str = "") -> ControlState:
        """자격증명 회수 — 멈추는 것과는 **다른 행동**이다."""
        st = self.get(agent_id)
        st.credentials_revoked = True
        return self._save(st, "revoke_credentials", by, reason, case_id)

    def block_tool(self, agent_id: str, tool: str, *, reason: str, by: str = "aoc",
                   case_id: str = "") -> ControlState:
        st = self.get(agent_id)
        if tool not in st.blocked_tools:
            st.blocked_tools.append(tool)
        return self._save(st, f"block_tool:{tool}", by, reason, case_id)

    def unblock_tool(self, agent_id: str, tool: str, *, reason: str, by: str,
                     case_id: str = "") -> ControlState:
        """도구 차단 해제 — **사람만**. 되돌리는 쪽이 더 위험한 방향이다."""
        if not by.startswith("human"):
            raise PermissionError("도구 차단 해제는 사람만 한다 (by=human:<이름>)")
        st = self.get(agent_id)
        if tool in st.blocked_tools:
            st.blocked_tools.remove(tool)
        return self._save(st, f"unblock_tool:{tool}", by, reason, case_id)

    def resume(self, agent_id: str, *, by: str, reason: str = "") -> ControlState:
        """해제는 **사람만** 한다. killed 는 자율화 강등이 남는다."""
        st = self.get(agent_id)
        if st.state == "killed" and not by.startswith("human"):
            raise PermissionError(
                "killed 상태는 사람만 해제할 수 있다 (by=human:<이름>)"
            )
        st.state = "running"
        return self._save(st, "resume", by, reason or "해제")

    # ── 조회 ────────────────────────────────────────────────────────────
    def can_run(self, agent_id: str) -> tuple[bool, str]:
        st = self.get(agent_id)
        if st.can_run:
            return True, ""
        return False, f"{st.state} — {st.reason} (case={st.case_id or '-'})"

    def all(self) -> list[ControlState]:
        out = [self.get(p.stem) for p in sorted(self.dir.glob("*.json"))]
        return sorted(out, key=lambda s: (s.state == "running", s.agent_id))

    def clear(self) -> int:
        n = 0
        for p in self.dir.glob("*.json"):
            p.unlink()
            n += 1
        return n
