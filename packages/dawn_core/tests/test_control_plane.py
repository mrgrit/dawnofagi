

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
