"""작업 지시별 에이전트 편성 — 매니페스트를 만든다 (P7 DoD-4).

**코드를 생성하지 않는다.** 하네스·루프는 P2 것을 그대로 쓴다. 여기서 만드는 것은
`org/agents/<id>/` 세 파일뿐이고, 그 순간부터 그 에이전트는 기존 레지스트리·통제
평면 컴파일러·관제가 **아는** 존재가 된다. 실행기를 새로 만들면 게이트가 두 벌이
되고 그게 통제 평면의 구멍이 된다.

    agent.yaml   소속 팀 · 페르소나 · 선언 도구 · 존
    SOUL.md      L4 — 이 작업에서 나는 누구인가
    gate.yaml    이 작업으로 **좁힌** 경계

## 왜 게이트를 굳이 또 쓰나

팀 게이트가 이미 있는데 작업별 게이트를 또 두는 이유는 **좁히기 위해서다.**
경리팀 에이전트는 `fin.*` 을 다 쓸 수 있지만, "8월 경비 마감" 작업에 편성된
에이전트는 그중 필요한 것만 쓰면 된다. 단조 축소라 넓힐 수는 없다 —
`check_narrowing` 이 기계적으로 강제하고, 넓히려 들면 컴파일이 실패한다.

## 회수

작업이 끝나면 매니페스트를 지운다. 남겨 두면 레지스트리가 끝난 작업의 에이전트로
계속 부풀고, 관제가 "가동 중이 아닌 에이전트"를 계속 보고한다.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 편성 id 접두어. 상시 에이전트(`org/agents/` 의 손으로 만든 것)와 섞이면
# 회수할 때 무엇을 지워도 되는지 알 수 없다.
PREFIX = "wo"
ID_RE = re.compile(rf"^{PREFIX}(\d+)-([a-z0-9-]+)$")


class CrewError(Exception):
    """편성 실패."""


@dataclass
class Member:
    """편성할 에이전트 한 명."""

    role_key: str                       # 파일명·id 에 들어간다 (소문자·하이픈)
    name: str
    team: str
    persona: str
    works: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    autonomy: str = "A1"
    zone: str = ""
    phase: str = "P1"                   # 오케스트레이터 위상
    depends_on: list[str] = field(default_factory=list)
    mission: str = ""                   # SOUL 에 들어간다

    def agent_id(self, order_id: int) -> str:
        return f"{PREFIX}{order_id}-{self.role_key}"


def _team_tools(root: Path, team: str) -> set[str]:
    """이 팀이 실제로 쓸 수 있는 도구. 작업 게이트는 이 안에서만 고를 수 있다."""
    from . import Registry
    from .gate import merge

    reg = Registry.load(root)
    t = reg.teams.get(team)
    if t is None:
        raise CrewError(f"없는 팀: {team}")
    div = reg.divisions[t.division_id]
    layers = []
    for label, p in (("company", reg.paths.root_gate),
                     (f"division:{div.id}", div.dir / "gate.yaml"),
                     (f"team:{team}", t.dir / "gate.yaml")):
        if p.is_file():
            layers.append((label, yaml.safe_load(p.read_text(encoding="utf-8")) or {}))
    gate = merge(layers)
    return {t2 for t2 in reg.tool_catalog.tools if gate.permits(t2)}


def plan(root: Path, *, order_id: int, members: list[Member]) -> list[dict[str, Any]]:
    """편성안을 검증만 한다 — **쓰지 않는다.** 승인 전에 보여 주기 위한 것."""
    out = []
    for m in members:
        allowed = _team_tools(root, m.team)
        asked = set(m.tools)
        over = sorted(asked - allowed)
        out.append({
            "agent_id": m.agent_id(order_id), "team": m.team, "name": m.name,
            "tools": sorted(asked), "over_scope": over,
            "phase": m.phase, "depends_on": list(m.depends_on),
        })
    return out


def form(root: Path, *, order_id: int, members: list[Member],
         approved: bool) -> list[str]:
    """매니페스트를 만든다. 만들어진 에이전트 id 목록을 돌려준다.

    Args:
        approved: 이 작업 지시가 결재를 **끝냈는가**. False 면 만들지 않는다.

    Raises:
        CrewError: 미승인 · 팀 경계를 넘는 도구 · 이미 편성됨
    """
    if not approved:
        raise CrewError(
            "결재가 끝나지 않았다 — 에이전트를 만들지 않는다. "
            "편성은 권한을 만드는 행위다"
        )
    if not members:
        raise CrewError("편성할 사람이 없다")

    from . import Registry

    root = Path(root)
    reg = Registry.load(root)
    # 실재하는 업무만 참조한다 — 레지스트리 무결성이 없는 SOP 를 거부한다.
    for m in members:
        missing = [w for w in m.works if w not in reg.works]
        if missing:
            raise CrewError(
                f"{m.agent_id(order_id)}: 없는 업무 SOP — {', '.join(missing)} "
                f"(있는 것: {', '.join(sorted(reg.works))})")

    # 팀에 에이전트가 생기면 L2(AGENT_TEAM.md)가 **필수**다 (통제 평면 컴파일러).
    # **자동 생성하지 않는다.** L2 는 그 팀 에이전트 전체의 행동 규칙이고,
    # 사람이 쓸 문서다. 없는 채로 사람을 넣으면 규칙 없이 일하는 팀이 생긴다.
    for m in {x.team for x in members}:
        team = reg.teams.get(m)
        if team is None:
            raise CrewError(f"없는 팀: {m}")
        if not (team.dir / "AGENT_TEAM.md").is_file():
            raise CrewError(
                f"팀 {m} 에 AGENT_TEAM.md(L2)가 없다 — 여기에 사람을 넣을 수 없다.\n"
                f"  이 팀 에이전트 전체의 행동 규칙을 먼저 써라: "
                f"{(team.dir / 'AGENT_TEAM.md').relative_to(root)}\n"
                f"  자동 생성하지 않는다 — 규칙 없이 일하는 팀을 만들지 않기 위해서다."
            )

    made: list[str] = []
    for m in members:
        aid = m.agent_id(order_id)
        d = root / "org" / "agents" / aid
        if d.exists():
            raise CrewError(f"이미 편성돼 있다: {aid}")

        allowed = _team_tools(root, m.team)
        over = sorted(set(m.tools) - allowed)
        if over:
            raise CrewError(
                f"{aid}: 팀({m.team}) 경계 밖 도구 — {', '.join(over)}. "
                "작업 게이트는 좁힐 수만 있다 (단조 축소)"
            )

        d.mkdir(parents=True)
        (d / "agent.yaml").write_text(_agent_yaml(m, aid, order_id), encoding="utf-8")
        (d / "SOUL.md").write_text(_soul_md(m, aid, order_id), encoding="utf-8")
        (d / "gate.yaml").write_text(_gate_yaml(m), encoding="utf-8")
        # 팀 매니페스트에도 등록한다 — 레지스트리는 **양방향** 참조를 요구한다.
        # 한쪽만 있으면 `make registry` 가 무결성 실패로 잡는다.
        _team_roster(root, reg.teams[m.team].source, add=aid)
        made.append(aid)
    return made


# `agents:` 한 줄만 고친다. `yaml.safe_dump` 로 통째로 다시 쓰면 **사람이 쓴 주석이
# 사라지고** 흐름 스타일(`[a, b]`)이 블록으로 바뀐다. 실제로 team.yaml 의
# `eg_org: org:mgmt  # EG 미분화 — ...` 주석을 날린 적이 있다.
_FLOW_RE = re.compile(r"^(?P<i>\s*)agents:\s*\[(?P<items>[^\]]*)\]\s*(?P<c>#.*)?$")
_BLOCK_RE = re.compile(r"^(?P<i>\s*)agents:\s*(?P<c>#.*)?$")


def _team_roster(root: Path, path: Path, *, add: str = "", remove: str = "") -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for n, line in enumerate(lines):
        mf = _FLOW_RE.match(line.rstrip("\n"))
        if mf:
            items = [x.strip() for x in mf["items"].split(",") if x.strip()]
            if add and add not in items:
                items.append(add)
            if remove and remove in items:
                items.remove(remove)
            tail = f"  {mf['c']}" if mf["c"] else ""
            lines[n] = f"{mf['i']}agents: [{', '.join(items)}]{tail}\n"
            path.write_text("".join(lines), encoding="utf-8")
            return
        mb = _BLOCK_RE.match(line.rstrip("\n"))
        if mb:
            end = n + 1
            while end < len(lines) and lines[end].lstrip().startswith("- "):
                end += 1
            items = [x.strip()[2:].strip() for x in lines[n + 1:end]]
            if add and add not in items:
                items.append(add)
            if remove and remove in items:
                items.remove(remove)
            body = [f"{mb['i']}- {x}\n" for x in items]
            lines[n:end] = [line, *body]
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise CrewError(f"{path}: agents 목록을 찾지 못했다")


def disband(root: Path, *, order_id: int) -> list[str]:
    """작업이 끝났다 — 편성 회수. 남기면 레지스트리가 끝난 작업으로 부푼다."""
    root = Path(root)
    base = root / "org" / "agents"
    removed = []
    for d in sorted(base.glob(f"{PREFIX}{order_id}-*")):
        if not (d.is_dir() and ID_RE.match(d.name)):
            continue
        doc = yaml.safe_load((d / "agent.yaml").read_text(encoding="utf-8")) or {}
        team = doc.get("team", "")
        shutil.rmtree(d)
        removed.append(d.name)
        # 팀 명부에서도 뺀다 — 안 빼면 없는 에이전트를 가리켜 무결성이 깨진다
        for tf in (root / "org" / "divisions").rglob("team.yaml"):
            td = yaml.safe_load(tf.read_text(encoding="utf-8")) or {}
            if td.get("id") == team:
                _team_roster(root, tf, remove=d.name)
                break
    return removed


def formed(root: Path, *, order_id: int | None = None) -> list[str]:
    """지금 편성돼 있는 작업 에이전트."""
    base = Path(root) / "org" / "agents"
    if not base.is_dir():
        return []
    pat = f"{PREFIX}{order_id}-*" if order_id else f"{PREFIX}*"
    return sorted(d.name for d in base.glob(pat) if d.is_dir() and ID_RE.match(d.name))


# ── 파일 본문 ────────────────────────────────────────────────────────────


def _agent_yaml(m: Member, aid: str, order_id: int) -> str:
    doc = {
        "id": aid, "team": m.team, "name": m.name, "role": "worker",
        "persona": m.persona, "works": m.works or [], "autonomy": m.autonomy,
        "tools": sorted(set(m.tools)), "status": "active",
        "notes": f"작업 지시 #{order_id} 편성. 작업 종료 시 회수된다 (P7 DoD-4).",
    }
    if m.zone:
        doc["zone"] = m.zone
    doc["telemetry"] = {"emit": True, "service_name": f"dawn.agent.{aid}"}
    head = (f"# 작업 지시 #{order_id} 편성 — **자동 생성**. 손으로 고치지 마라.\n"
            f"# 작업이 끝나면 회수된다. 상시 에이전트는 접두어 없이 만든다.\n")
    return head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _gate_yaml(m: Member) -> str:
    return (
        "# 작업 게이트 — **좁히기만** 한다. 팀 게이트를 넓힐 수 없다 (단조 축소).\n"
        "# 여기 allow 에 팀 경계 밖 도구를 적으면 컴파일이 실패한다.\n"
        + yaml.safe_dump(
            {"tools": {"allow": sorted(set(m.tools))}, "autonomy": m.autonomy},
            allow_unicode=True, sort_keys=False)
    )


def _soul_md(m: Member, aid: str, order_id: int) -> str:
    tools = "\n".join(f"- `{t}`" for t in sorted(set(m.tools))) or "- (없음)"
    works = ", ".join(m.works) or "(미지정)"
    return f"""# SOUL.md — {aid}

> **통제 평면 L4.** 나 한 명에게만 적용된다.
> 작업 지시 **#{order_id}** 을 위해 편성됐고, 작업이 끝나면 회수된다.

## 나는 누구인가

{m.name}. {m.team} 소속으로 이 작업에만 투입됐다.

{m.mission or "이 작업의 산출물을 만드는 것이 내 일이다."}

## 이 작업에서 내가 하는 일

- 수행 업무(L3): {works}
- 위상: `{m.phase}`{" · 선행: " + ", ".join(m.depends_on) if m.depends_on else ""}

## 내가 쓸 수 있는 도구

{tools}

이 목록이 전부다. 여기 없는 도구가 필요하면 **작업 지시를 고쳐 다시 결재받는다** —
내가 스스로 늘리지 않는다. 늘릴 수도 없다(게이트가 기계적으로 막는다).

## 내가 하지 않는 것

- 이 작업 범위 밖의 일. 관련돼 보여도 하지 않는다
- 산출물을 "완료"라고 부르기 전에 검수를 건너뛰는 것
- 편성 기간이 끝난 뒤에도 남아 있으려는 시도
"""


__all__ = ["PREFIX", "CrewError", "Member", "disband", "form", "formed", "plan"]
