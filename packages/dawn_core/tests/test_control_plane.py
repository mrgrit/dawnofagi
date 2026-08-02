"""통제 평면 — 도구 카탈로그 · 작업별 에이전트 편성.

여기서 지키는 것:

* 도구는 **만지는 자산을 선언한다** — 빠뜨리면 심각도가 0 으로 계산돼
  가장 위험한 도구가 가장 안전해 보인다 (QUESTIONS Q7).
* 편성은 **권한을 만드는 행위**다 — 결재 없이는 만들지 않고, 팀 경계를
  넓히지 못하며, 끝나면 흔적 없이 회수된다 (P7 DoD-4).
"""

from __future__ import annotations

import pytest

# ── 도구 카탈로그 — 자산 선언 누락 방지 (QUESTIONS Q7) ────────────────────


def test_every_tool_declares_what_it_touches():
    """`touches` 를 빠뜨리면 심각도가 0(낮음)으로 계산돼 가장 위험한 도구가
    가장 안전해 보인다. 실측으로 `sys.rm_rf_root` 가 심각도 0 이었다.

    자산이 없는 게 맞는 메타 도구는 `touches: []` 로 **명시**한다 —
    빠뜨림과 "없음"은 다른 사실이고, 카탈로그는 그 둘을 구분해야 한다.
    """
    from dawn_core import Registry
    from dawn_core.paths import Paths

    cat = Registry.load(Paths().root).tool_catalog
    missing = [t for t, v in cat.tools.items() if "touches" not in v]
    assert not missing, f"touches 미선언: {sorted(missing)}"


def test_catalog_is_the_authority_for_touches():
    """등록부가 안 적어도 카탈로그에서 채워져야 한다 — 같은 실수가 반복되지 않게."""
    from dawn_agents.skills import build_default_registry
    from dawn_core import Registry
    from dawn_core.paths import Paths

    root = Paths().root
    reg = Registry.load(root)
    sk = build_default_registry(reg.tool_catalog, root=root)
    for name in sk.names():
        want = reg.tool_catalog.touches(name)
        if want:
            assert sk.get(name).touches, f"{name}: 카탈로그에 있는데 등록부가 비었다"


def test_dangerous_tools_are_not_scored_as_harmless():
    """자산을 선언하면 심각도가 실제 위험을 반영한다 (`ctl.*`·`sys.*` 가 0 이었다)."""
    import pytest
    from dawn_agents.actiongate import ActionGate
    from dawn_agents.skills import build_default_registry
    from dawn_core import Registry
    from dawn_core.control_plane import compile_agent
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore
    from dawn_core.paths import Paths

    root = Paths().root
    reg = Registry.load(root)
    db = db_path(reg.paths)
    if not db.is_file():
        pytest.skip("EG DB 없음")
    sk = build_default_registry(reg.tool_catalog, root=root)
    c = compile_agent(reg, sorted(reg.agents)[0])
    gate = ActionGate(c.gate, EGStore(db), autonomy=c.autonomy)
    for name in ("ctl.modify_gate", "ctl.modify_kill_switch", "ctl.cross_tenant",
                 "sys.rm_rf_root", "sys.mkfs"):
        d = gate.evaluate(sk.preview(name), declared_tools=c.declared_tools)
        assert d.severity >= 6, f"{name}: 심각도 {d.severity} — 자격증명 회수 문턱(6) 미달"


# ── 작업별 에이전트 편성 (P7 DoD-4) ──────────────────────────────────────


def _member(**kw):
    from dawn_core.crew import Member

    base = {"role_key": "builder", "name": "[테스트] 작업 에이전트", "team": "corp-cs",
            "persona": "corporate", "works": ["corporate/crm-inquiry"],
            "tools": ["eg.search", "eg.record", "doc.search"], "zone": "dmz"}
    base.update(kw)
    return Member(**base)


@pytest.fixture
def crew_root():
    """편성 테스트는 실물 `org/` 를 건드린다 — 끝나면 반드시 되돌린다."""
    from dawn_core import crew
    from dawn_core.paths import Paths

    root = Paths().root
    yield root
    crew.disband(root, order_id=9901)


def test_forming_requires_completed_approval(crew_root):
    """편성은 **권한을 만드는 행위**다. 결재 전에는 만들지 않는다."""
    from dawn_core.crew import CrewError, form, formed

    with pytest.raises(CrewError, match="결재"):
        form(crew_root, order_id=9901, members=[_member()], approved=False)
    assert formed(crew_root, order_id=9901) == []


def test_work_gate_cannot_widen_the_team_gate(crew_root):
    """단조 축소 — 작업 게이트는 좁힐 수만 있다."""
    from dawn_core.crew import CrewError, form

    with pytest.raises(CrewError, match="경계 밖"):
        form(crew_root, order_id=9901,
             members=[_member(tools=["eg.search", "pay.execute"])], approved=True)


def test_forming_rejects_unknown_work_sop(crew_root):
    from dawn_core.crew import CrewError, form

    with pytest.raises(CrewError, match="없는 업무 SOP"):
        form(crew_root, order_id=9901, members=[_member(works=["nope/none"])],
             approved=True)


def test_forming_refuses_team_without_l2(crew_root):
    """팀에 사람을 넣으려면 그 팀의 행동 규칙(L2)이 먼저 있어야 한다.

    자동 생성하지 않는다 — 규칙 없이 일하는 팀을 만들지 않기 위해서다.
    """
    from dawn_core import Registry
    from dawn_core.crew import CrewError, form

    reg = Registry.load(crew_root)
    dormant = next((t for t in reg.teams.values()
                    if not (t.dir / "AGENT_TEAM.md").is_file()), None)
    if dormant is None:
        pytest.skip("L2 없는 팀이 없다")
    with pytest.raises(CrewError, match=r"AGENT_TEAM\.md"):
        form(crew_root, order_id=9901,
             members=[_member(team=dormant.id, works=[])], approved=True)


def test_formed_agent_compiles_through_the_control_plane(crew_root):
    """편성된 에이전트가 기존 컴파일러를 그대로 통과해야 한다 —
    실행기를 새로 만들면 게이트가 두 벌이 되고 그게 통제 평면의 구멍이다."""
    from dawn_core import Registry
    from dawn_core.control_plane import compile_agent
    from dawn_core.crew import form

    made = form(crew_root, order_id=9901, members=[_member()], approved=True)
    assert made == ["wo9901-builder"]
    c = compile_agent(Registry.load(crew_root), made[0])
    g = c.gate.to_dict(c.declared_tools)
    assert [ly.level for ly in c.layers][:2] == ["L1", "L2"]
    assert [ly.level for ly in c.layers][-1] == "L4"
    assert set(g["tools"]["effective"]) == {"eg.search", "eg.record", "doc.search"}
    assert f"agent:{made[0]}" in g["sources"], "작업 게이트가 경계에 반영되지 않았다"


def test_disband_leaves_no_trace(crew_root):
    """회수가 안 되면 레지스트리가 끝난 작업의 에이전트로 계속 부푼다."""
    from dawn_core import Registry
    from dawn_core.crew import disband, form, formed

    # 편성이 건드리는 파일을 **직접** 떠 둔다. 예전엔 `git diff org/divisions/` 로
    # 봤는데, 그건 이 테스트와 무관한 커밋 안 된 변경까지 실패로 읽는다(실측).
    team_yaml = Registry.load(crew_root).teams["corp-cs"].dir / "team.yaml"
    before = team_yaml.read_text(encoding="utf-8")

    form(crew_root, order_id=9901, members=[_member()], approved=True)
    assert formed(crew_root, order_id=9901)
    assert team_yaml.read_text(encoding="utf-8") != before, "편성이 명부에 안 올랐다"
    assert disband(crew_root, order_id=9901) == ["wo9901-builder"]
    assert formed(crew_root, order_id=9901) == []
    Registry.load(crew_root).check_integrity()      # 양방향 참조가 깨지지 않았다
    # 사람이 쓴 매니페스트를 원본 그대로 돌려놔야 한다 (주석·포맷 포함)
    assert team_yaml.read_text(encoding="utf-8") == before, \
        "팀 매니페스트가 바뀐 채로 남았다 (주석·포맷 포함해 원본이어야 한다)"
