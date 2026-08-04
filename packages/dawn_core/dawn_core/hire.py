"""작업 지시를 읽고 **에이전트 편성안을 만든다** (P7 — 자동 고용).

지금까지 편성은 사람이 `--role`·`--team`·`--tools` 를 손으로 다 넣어야 했고
한 번에 한 명만 만들어졌다. 지시문을 읽지 않았다.

여기서 하는 일은 **초안 작성뿐이다.** 만들지 않는다:

    작업 지시 → [모델] → 편성 초안(YAML)  →  본부장이 고치고 승인  →  crew.form()
                          ^^^^^^^^^^^^^                ^^^^^^^^^^^^
                          이 모듈                       사람

초안과 집행을 가르는 이유는 하나다. **편성은 권한을 만드는 행위다** — 에이전트가
생기면 그 순간 도구·자산·존에 대한 접근이 생긴다. 모델이 지시문을 잘못 읽어
과한 역할을 제안할 수 있고, 그건 사람이 파일로 보고 고칠 수 있어야 한다.

## 게이트가 경계다, 모델의 의견이 아니다

모델이 제안한 도구는 **팀 게이트가 허용하는 것과 교집합**만 남긴다. 빼앗은 것은
`dropped_tools` 로 초안에 적는다 — 조용히 좁히면 모델이 무엇을 하려 했는지
사람이 못 본다. 그게 보여야 "이 작업에는 원래 더 센 권한이 필요한 것 아닌가"를
사람이 판단한다.

## 팀장

`lead: true` 인 에이전트는 **자기 게이트 안의 결정을 스스로 내린다.** 게이트
밖(비가역·L3·경계 밖)은 팀장이 있어도 사람에게 올라간다 — 팀장을 두는 것은
결재를 없애는 게 아니라 **게이트 안쪽의 잔결정을 사람이 안 봐도 되게** 하는 것이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DRAFT_DIR = Path("var") / "biz" / "crew"
MAX_MEMBERS = 6          # 한 지시에 이보다 많이 뽑지 않는다
MAX_BODY = 6000          # 지시문이 길면 앞부분만 — 컨텍스트를 다 먹지 않게

# 초안에서도 절대 못 주는 도구. 게이트가 이미 막지만, 모델이 제안하는 것 자체를
# 보고 싶지 않다 — 목록에 떠 있으면 사람이 실수로 켤 수 있다.
NEVER = {
    "sys.rm_rf_root", "sys.mkfs", "ctl.modify_gate", "ctl.modify_kill_switch",
    "ctl.cross_tenant", "pay.execute", "crm.contract_sign", "sec.firewall_change",
}


class HireError(Exception):
    """편성안을 만들 수 없다."""


@dataclass
class Draft:
    """편성 초안 하나. 파일이 곧 이 객체다."""

    order_id: int
    title: str = ""
    division: str = ""
    team: str = ""
    status: str = "draft"           # draft | approved
    proposed_by: str = ""
    proposed_at: str = ""
    approved_by: str = ""
    approved_at: str = ""
    members: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def lead(self) -> dict[str, Any] | None:
        """팀장. 없으면 None — 그러면 게이트 안의 결정도 사람이 내려야 한다."""
        return next((m for m in self.members if m.get("lead")), None)


def draft_path(root: Path, order_id: int) -> Path:
    return Path(root) / DRAFT_DIR / f"wo{order_id}.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 팀·도구 ─────────────────────────────────────────────────────────────
def pick_team(root: Path, division: str, *, hint: str = "") -> str:
    """이 본부에서 편성할 팀. `AGENT_TEAM.md`(L2) 가 있는 팀만 고른다.

    규칙 없이 일하는 팀에는 사람을 넣지 않는다 — 팀 규칙이 없으면 그 에이전트의
    행동 경계가 회사 기본값밖에 없고, 그건 경계라고 부를 수 없다.
    """
    from . import Registry

    reg = Registry.load(root)
    div = reg.divisions.get(division)
    if div is None:
        raise HireError(f"없는 본부: {division}")
    usable = [t for t in div.data.get("teams", [])
              if (reg.teams[t].dir / "AGENT_TEAM.md").is_file()]
    if not usable:
        raise HireError(
            f"{division} 본부에 편성할 팀이 없다 — AGENT_TEAM.md(L2) 가 있는 팀이 없다")
    if hint and hint in usable:
        return hint
    return usable[0]


def allowed_tools(root: Path, team: str) -> set[str]:
    """이 팀이 실제로 쓸 수 있는 도구 — 게이트가 정한다."""
    from .crew import _team_tools

    return _team_tools(root, team) - NEVER


# ── 모델에게 묻기 ───────────────────────────────────────────────────────
_SCHEMA = """{
  "notes": "이 지시문을 어떻게 읽었는지 1~2문장",
  "members": [
    {
      "role_key": "소문자-하이픈 (파일명이 된다)",
      "name": "역할 이름 (한국어)",
      "mission": "이 에이전트가 책임지는 것 한 문장",
      "lead": true 또는 false,
      "tools": ["도구 id", ...],
      "phase": "P1" | "P2" | "P3",
      "depends_on": ["먼저 끝나야 하는 role_key", ...]
    }
  ]
}"""


def _prompt(order: dict[str, Any], tools: list[str], team: str) -> tuple[str, str]:
    system = (
        "너는 이 회사의 편성 담당이다. 작업 지시를 읽고 **누가 필요한지**를 정한다.\n"
        "\n"
        "규칙:\n"
        f"- 최대 {MAX_MEMBERS}명. 적을수록 좋다 — 사람이 늘면 조율 비용이 산출물보다 커진다.\n"
        "- 도구는 **주어진 목록에서만** 고른다. 목록에 없는 것을 적으면 무시된다.\n"
        "- 각자에게 **자기 일에 필요한 것만** 준다. 넉넉히 주지 않는다.\n"
        "- 팀장(lead)은 **0명 또는 1명.** 결정이 자주 필요한 작업에만 둔다.\n"
        "  팀장은 자기 게이트 안의 결정만 스스로 내린다 — 비가역·L3 는 사람에게 간다.\n"
        "- phase 는 순서다. 같은 phase 는 동시에 돈다.\n"
        "- JSON 만 출력한다. 설명을 붙이지 않는다.\n"
        "\n"
        f"출력 형식:\n{_SCHEMA}\n"
    )
    body = str(order.get("body") or "")[:MAX_BODY]
    prompt = (
        f"## 작업 지시 #{order.get('id')}\n"
        f"제목: {order.get('title')}\n"
        f"사업: {order.get('business')} · 본부: {order.get('division')} · 팀: {team}\n"
        f"환경: {order.get('infra_tier')} · 보안등급: {order.get('security_level')}\n\n"
        f"## 지시문\n{body}\n\n"
        f"## 이 팀이 쓸 수 있는 도구 ({len(tools)}개)\n{', '.join(tools)}\n"
    )
    return system, prompt


def _model_for(root: Path, team: str) -> tuple[str, str]:
    """편성될 **팀**에 배정된 모델.

    본부(`org:ax`)가 아니라 팀(`org:ax-univ`)이다. 모델 배정이 팀 단위로
    걸려 있기도 하지만, 그보다 **초안을 쓰는 모델은 그 초안대로 일할 팀의
    것이어야** 한다. 더 좋은 모델로 편성안만 그럴듯하게 뽑아 놓으면, 실제로
    도는 에이전트가 감당 못 하는 계획이 나온다.
    """
    from .eg.cli import db_path
    from .eg.store import EGStore
    from .eg.traverse import model_for_org
    from .paths import Paths
    from . import Registry

    reg = Registry.load(root)
    t = reg.teams.get(team)
    eg_org = (t.data.get("eg_org") if t else "") or ""
    if not eg_org:
        raise HireError(f"{team} 팀에 eg_org 가 없다")

    db = db_path(Paths(root))
    if not db.is_file():
        raise HireError(f"EG DB 가 없다: {db} — make eg-load 먼저")
    m = model_for_org(EGStore(db), eg_org, touches_l3=False)
    if m.get("blocked") or not m.get("model_id"):
        raise HireError(m.get("reason") or f"{eg_org} 에 모델 배정이 없다")
    return m["model_id"], eg_org


def _parse(text: str) -> dict[str, Any]:
    """모델 출력에서 JSON 을 꺼낸다. 코드펜스로 감싸 오는 일이 흔하다."""
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for chunk in parts:
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                t = c
                break
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise HireError(f"모델이 JSON 을 주지 않았다: {text[:200]}")
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise HireError(f"JSON 파싱 실패: {e}") from e


def propose(root: Path, order: dict[str, Any], *, team: str = "",
            timeout: int = 300) -> Draft:
    """작업 지시 → 편성 초안. **파일로 쓰지 않는다** (호출자가 `save`)."""
    from dawn_agents import llm

    root = Path(root)
    division = str(order.get("division") or "")
    team = team or pick_team(root, division)
    allowed = allowed_tools(root, team)
    if not allowed:
        raise HireError(f"{team} 팀이 쓸 수 있는 도구가 없다 — 게이트를 확인하라")

    model_id, _eg_org = _model_for(root, team)
    system, prompt = _prompt(order, sorted(allowed), team)
    comp = llm.LLMClient(timeout=timeout).complete(
        llm.resolve(model_id, touches_l3=False),
        system=system, prompt=prompt, max_tokens=2500)
    raw = _parse(comp.text)

    members: list[dict[str, Any]] = []
    seen_lead = False
    for m in (raw.get("members") or [])[:MAX_MEMBERS]:
        if not isinstance(m, dict):
            continue
        key = str(m.get("role_key") or "").strip().lower().replace(" ", "-")
        if not key:
            continue
        asked = {str(t).strip() for t in (m.get("tools") or []) if str(t).strip()}
        # **게이트가 경계다.** 뺏은 것은 적어 둔다 — 조용히 좁히면 모델이 무엇을
        # 하려 했는지 사람이 못 보고, 그게 보여야 권한 재검토가 일어난다.
        keep = sorted(asked & allowed)
        dropped = sorted(asked - allowed)
        lead = bool(m.get("lead")) and not seen_lead
        seen_lead = seen_lead or lead
        members.append({
            "role_key": key,
            "name": str(m.get("name") or key)[:60],
            "mission": str(m.get("mission") or "")[:200],
            "lead": lead,
            "tools": keep or ["eg.search", "eg.record"],
            "dropped_tools": dropped,
            "phase": str(m.get("phase") or "P1"),
            "depends_on": [str(x) for x in (m.get("depends_on") or [])],
            "works": [],
        })

    if not members:
        raise HireError("모델이 편성안을 내지 못했다 — 지시문이 너무 짧거나 형식이 깨졌다")

    return Draft(
        order_id=int(order["id"]), title=str(order.get("title") or ""),
        division=division, team=team, status="draft",
        proposed_by=f"{comp.provider}/{comp.model}", proposed_at=_now(),
        members=members, notes=str(raw.get("notes") or "")[:300],
    )


# ── 파일 ────────────────────────────────────────────────────────────────
_HEADER = """\
# 작업 지시 #{oid} 편성안 — **초안이다. 이대로 만들어지지 않는다.**
#
# 본부장이 이 파일을 고치고 승인해야 에이전트가 생긴다:
#     dawn-biz hire {oid} --approve --by <본부장 계정>
#
# 고칠 것:
#   name/mission  이 에이전트가 무엇을 책임지나
#   tools         **줄이는 쪽으로.** 필요 없는 도구는 지운다
#   lead          팀장 1명 또는 0명. 팀장은 자기 게이트 안의 결정을 스스로 내리고,
#                 게이트 밖(비가역·L3)은 팀장이 있어도 사람에게 올라간다
#   phase         순서. 같은 phase 는 동시에 돈다
#
# dropped_tools 는 **모델이 요청했지만 팀 게이트 밖이라 뺀 것**이다. 참고용이고
# 여기 적어도 주어지지 않는다. 정말 필요하면 팀 게이트를 고치는 게 맞다 —
# 그건 통제 평면의 일이고 사유가 남는다.
"""


def save(root: Path, d: Draft) -> Path:
    import yaml

    p = draft_path(root, d.order_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(d.to_dict(), allow_unicode=True, sort_keys=False,
                          default_flow_style=False)
    p.write_text(_HEADER.format(oid=d.order_id) + body, encoding="utf-8")
    return p


def load(root: Path, order_id: int) -> Draft:
    import yaml

    p = draft_path(root, order_id)
    if not p.is_file():
        raise HireError(f"편성 초안이 없다: {p} — dawn-biz hire {order_id} 먼저")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    known = {k: v for k, v in raw.items() if k in Draft.__annotations__}
    return Draft(**known)


# ── 승인 → 집행 ─────────────────────────────────────────────────────────
def to_members(root: Path, d: Draft) -> list:
    """초안 → `crew.Member`. 도구는 **여기서 다시 한 번** 게이트로 자른다.

    사람이 파일을 고치므로, 초안이 만들어진 뒤 손으로 도구를 더 적어 넣을 수 있다.
    파일을 믿지 않는다 — 경계는 게이트지 파일이 아니다.
    """
    from . import Registry
    from .crew import Member

    reg = Registry.load(root)
    allowed = allowed_tools(root, d.team)
    persona = reg.teams[d.team].data.get("persona_default", "corporate")
    out = []
    for m in d.members:
        asked = set(m.get("tools") or [])
        out.append(Member(
            role_key=str(m["role_key"]),
            name=str(m.get("name") or m["role_key"]),
            team=d.team,
            persona=persona,
            works=[str(w) for w in (m.get("works") or [])],
            tools=sorted(asked & allowed) or ["eg.search", "eg.record"],
            zone="",
            phase=str(m.get("phase") or "P1"),
            depends_on=[str(x) for x in (m.get("depends_on") or [])],
            mission=str(m.get("mission") or ""),
        ))
    return out


def approve(root: Path, order_id: int, *, by: str, approved: bool) -> tuple[Draft, list[str]]:
    """본부장 승인 → 실제 편성. 만들어진 에이전트 id 를 함께 돌려준다.

    Args:
        approved: 이 **작업 지시**가 결재를 끝냈는가. 편성 승인과 별개다 —
            지시가 결재 전이면 편성안이 아무리 좋아도 만들지 않는다.
    """
    from .crew import form

    d = load(root, order_id)
    if d.status == "approved":
        raise HireError(f"이미 승인된 편성안이다 ({d.approved_by} · {d.approved_at})")
    if not by:
        raise HireError("승인자가 필요하다 — 누가 승인했는지 없이 권한을 만들지 않는다")

    made = form(root, order_id=order_id, members=to_members(root, d), approved=approved)
    d.status, d.approved_by, d.approved_at = "approved", by, _now()
    save(root, d)
    return d, made


__all__ = [
    "DRAFT_DIR", "Draft", "HireError", "NEVER",
    "allowed_tools", "approve", "draft_path", "load", "pick_team",
    "propose", "save", "to_members",
]
