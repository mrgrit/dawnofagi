"""실제 저장소의 레지스트리·통제 평면이 정합한지 검증한다.

이 테스트는 픽스처가 아니라 **실물 org/ · work/ 를 읽는다.**
매니페스트를 잘못 고치면 여기서 잡힌다.
"""

from __future__ import annotations

import pytest
from dawn_core import Registry, compile_agent, compile_all
from dawn_core.lint import PASS_THRESHOLD
from dawn_core.lint import run as run_lint


@pytest.fixture(scope="module")
def reg() -> Registry:
    return Registry.load()


# ── 레지스트리 ──────────────────────────────────────────────────────────


def test_registry_loads_and_is_referentially_intact(reg):
    reg.check_integrity()  # 예외가 안 나면 통과
    s = reg.summary()
    assert s["divisions"] == 4, "헌장의 4본부 구조"
    assert s["businesses"] >= 2
    assert s["agents_active"] >= 1


def test_every_registered_business_has_a_real_division(reg):
    for b in reg.businesses.values():
        for did in b.data["owning_divisions"]:
            assert did in reg.divisions, f"{b.id} → 없는 본부 {did}"


def test_planned_business_is_registered_but_not_live(reg):
    """사업은 플러그인 — 시작 전에도 매니페스트로 조직 설계가 검증된다."""
    planned = [b for b in reg.businesses.values() if b.status == "planned"]
    assert planned, "planned 사업이 하나는 있어야 플러그인 구조가 실증된다"
    for b in planned:
        assert not b.is_live


def test_agents_only_declare_catalogued_tools(reg):
    cat = reg.tool_catalog
    for a in reg.agents.values():
        unknown = cat.unknown(a.data["tools"])
        assert not unknown, f"{a.id}: 카탈로그에 없는 도구 {unknown}"


def test_work_documents_have_required_frontmatter(reg):
    for w in reg.works.values():
        for key in ("id", "name", "domain", "owner_team", "risk"):
            assert key in w.meta, f"{w.source}: {key} 누락"


# ── 통제 평면 ────────────────────────────────────────────────────────────


def test_all_active_agents_compile(reg):
    ok, failed = compile_all(reg)
    assert not failed, "컴파일 실패한 에이전트가 있다:\n" + "\n".join(failed.values())
    assert ok, "컴파일된 에이전트가 하나도 없다"


def test_compiled_agent_has_all_four_layers(reg):
    for aid in reg.agents:
        c = compile_agent(reg, aid)
        levels = {lyr.level for lyr in c.layers}
        assert levels == {"L1", "L2", "L3", "L4"}, f"{aid}: 계층 누락 {levels}"


def test_system_prompt_puts_company_first(reg):
    c = compile_agent(reg, next(iter(reg.agents)))
    prompt = c.system_prompt()
    assert prompt.index("L1:") < prompt.index("L4:"), "상위 계층이 먼저 나와야 한다"
    assert "COMPANY.md" in prompt


def test_bundle_is_serialisable_and_stamped(reg):
    import json

    for aid in reg.agents:
        b = compile_agent(reg, aid).bundle()
        json.dumps(b, ensure_ascii=False)  # 직렬화 가능
        assert b["control_plane"]["prompt_sha256"]  # 감사용 해시
        assert len(b["control_plane"]["layers"]) >= 4


# ── 정책 실증 (헌장·컨벤션이 실제로 강제되는가) ──────────────────────────


def test_l3_team_is_forced_to_local_model(reg):
    """05_conventions #5 — 인사·재무 개인정보는 클라우드 모델 전송 금지."""
    c = compile_agent(reg, "corp-admin-clerk-01")
    assert c.gate.model_policy == "local_only"
    assert c.gate.forces_local_model()


def test_l3_team_has_no_external_egress(reg):
    """L3 데이터가 밖으로 나갈 경로 자체가 없어야 한다."""
    c = compile_agent(reg, "corp-admin-clerk-01")
    for tool in ("net.web_search", "net.fetch", "comm.external_send", "pay.execute"):
        assert not c.gate.permits(tool), f"L3 팀이 {tool} 을 쓸 수 있다"


def test_nobody_can_touch_the_control_plane(reg):
    """COMPANY.md §6.3 — 에이전트는 자기를 감시하는 층을 건드릴 수 없다."""
    for aid in reg.agents:
        c = compile_agent(reg, aid)
        for tool in ("ctl.modify_gate", "ctl.modify_kill_switch", "ctl.cross_tenant"):
            assert not c.gate.permits(tool), f"{aid} 가 {tool} 을 쓸 수 있다"


def test_nobody_can_execute_payment(reg):
    for aid in reg.agents:
        assert not compile_agent(reg, aid).gate.permits("pay.execute")


def test_destructive_actions_require_hitl_everywhere(reg):
    for aid in reg.agents:
        g = compile_agent(reg, aid).gate
        assert g.requires_hitl("destructive")
        assert g.requires_hitl("irreversible")


def test_every_agent_emits_telemetry(reg):
    """P3 관제가 못 보는 에이전트가 있으면 안 된다."""
    for aid in reg.agents:
        c = compile_agent(reg, aid)
        assert c.gate.telemetry.get("emit") is True, f"{aid}: 텔레메트리 꺼짐"


def test_security_team_cannot_read_hr_or_finance(reg):
    c = compile_agent(reg, "ccc-soc-triage-01")
    assert not c.gate.permits("hr.data_read")
    assert not c.gate.permits("fin.ledger_read")


def test_all_agents_start_at_or_below_a1(reg):
    """헌장 자율화 로드맵 — 초기 대부분 A1에서 시작."""
    from dawn_core.gate import AUTONOMY_ORDER

    for aid in reg.agents:
        c = compile_agent(reg, aid)
        assert AUTONOMY_ORDER.index(c.autonomy) <= AUTONOMY_ORDER.index("A1"), aid


# ── 성숙도 ──────────────────────────────────────────────────────────────


def test_control_readiness_passes(reg):
    rep = run_lint(reg)
    assert rep.passed, f"Control Readiness {rep.score} < {PASS_THRESHOLD}\n{rep.to_dict()}"
