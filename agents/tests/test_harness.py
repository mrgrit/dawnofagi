"""P2 하네스 — 루프 무결성 · 게이트 · 라우팅 · 텔레메트리.

모델을 실제로 부르지 않는다 (테스트는 GPU/네트워크에 의존하면 안 된다).
LLM 은 가짜로 갈아끼우고, **게이트와 루프의 구조**를 검증한다.
"""

from __future__ import annotations

import pytest
from dawn_agents import (
    ApprovalQueue,
    Assignment,
    Event,
    TeamOrchestrator,
    Tracer,
    Worker,
    WorkQueue,
    default_dispatcher,
    mask_pii,
)
from dawn_agents import llm as llm_mod
from dawn_agents.policy import Facts, evaluate_rule
from dawn_agents.skills import SkillError, build_default_registry
from dawn_core import Registry
from dawn_core.eg.cli import db_path as eg_db_path
from dawn_core.eg.store import EGStore

CORP = "corp-admin-clerk-01"
CCC = "ccc-soc-triage-01"


class FakeClient(llm_mod.LLMClient):
    """모델을 부르지 않는다. 어떤 라우팅으로 불렸는지만 기록한다."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[llm_mod.Resolved] = []

    def complete(self, resolved, *, system, prompt, max_tokens=2000, effort="medium"):
        self.calls.append(resolved)
        return llm_mod.Completion(
            text="(가짜 응답)",
            model=resolved.model,
            provider=resolved.provider,
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        )

    def available(self, resolved):
        return True, ""


@pytest.fixture(scope="module")
def reg() -> Registry:
    return Registry.load()


@pytest.fixture(scope="module")
def eg(reg):
    db = eg_db_path(reg.paths)
    if not db.is_file():
        pytest.skip("EG DB 없음 — make eg-load")
    return EGStore(db)


@pytest.fixture
def worker(reg, eg, tmp_path):
    def make(agent_id: str) -> Worker:
        w = Worker(
            agent_id,
            registry=reg,
            eg_store=eg,
            tracer=Tracer(tmp_path / "traces"),
            queue=ApprovalQueue(tmp_path),
            client=FakeClient(),
        )
        return w

    return make


# ── 정책 규칙 평가기 ─────────────────────────────────────────────────────


def test_rule_always_fires():
    fired, unknown, _ = evaluate_rule("always => record(x)", Facts())
    assert fired and not unknown


def test_rule_boolean_predicate():
    r = "asset.irreversibility in ['irreversible','execute'] AND action.destructive => require_hitl"
    fired, _, _ = evaluate_rule(
        r, Facts(asset_irreversibility="irreversible", action_destructive=True)
    )
    assert fired
    fired, _, _ = evaluate_rule(
        r, Facts(asset_irreversibility="irreversible", action_destructive=False)
    )
    assert not fired, "action.destructive 가 거짓인데 발동했다"


def test_rule_l3_local_only():
    r = "asset.sec_rank == 3 AND model.cost_tier != 'local' => block"
    assert evaluate_rule(r, Facts(asset_sec_rank=3, model_cost_tier="mid"))[0]
    assert not evaluate_rule(r, Facts(asset_sec_rank=3, model_cost_tier="local"))[0]
    assert not evaluate_rule(r, Facts(asset_sec_rank=2, model_cost_tier="mid"))[0]


def test_rule_single_tenant_never_fires():
    r = "task.tenant != asset.tenant => block"
    assert not evaluate_rule(r, Facts())[0], "테넌트 #0 단독인데 크로스테넌트가 발동했다"


def test_unknown_predicate_fails_safe():
    fired, unknown, _ = evaluate_rule("some.unknown.thing == 1 => block", Facts())
    assert fired and unknown, "모르는 술어는 보수적으로 발동해야 한다"


# ── 루프 무결성 ──────────────────────────────────────────────────────────


def test_four_step_loop(worker):
    w = worker(CORP)
    run = w.run("테스트 업무")
    kinds = [s.kind for s in run.steps]
    assert kinds[0] == "eg_search", "① eg_search 로 시작하지 않았다"
    assert "chat" in kinds
    assert kinds[-1] == "record", "④ eg_record 로 끝나지 않았다"
    assert run.complete


def test_preview_always_precedes_run(worker):
    """②를 건너뛴 ③은 구조적으로 불가능해야 한다."""
    w = worker(CORP)
    run = w.run("테스트", extra_skills=[("fin.expense_read", {"request_id": "X"})])
    seq = [s.kind for s in run.steps if s.kind in ("preview", "gate", "run")]
    for i, k in enumerate(seq):
        if k == "run":
            assert seq[i - 2] == "preview" and seq[i - 1] == "gate", (
                f"run 앞에 preview→gate 가 없다: {seq}"
            )


def test_record_missing_means_incomplete(worker):
    w = worker(CORP)
    run = w.run("테스트")
    run.recorded = False
    assert not run.complete, "eg_record 없이 완료로 보고했다"


# ── 행동 게이트 ──────────────────────────────────────────────────────────


def test_destructive_l3_routes_to_hitl(worker):
    """자기검증 #1 — 결제 실행(비가역·L3)은 HITL 로 간다."""
    w = worker(CORP)
    pv = w.skills.preview("pay.execute", amount=1000000)
    d = w.gate.evaluate(pv, declared_tools=w.compiled.declared_tools)
    assert d.needs_hitl
    assert d.blocked, "pay.execute 는 전사 deny 다 — block 이어야 한다"


def test_ledger_write_blocked(worker):
    w = worker(CORP)
    d = w.gate.evaluate(
        w.skills.preview("fin.ledger_write"), declared_tools=w.compiled.declared_tools
    )
    assert d.blocked, "원장 기입(비가역)이 막히지 않았다"


def test_loop_instrumentation_is_not_gated(worker):
    """①④ 단계가 HITL 로 막히면 루프가 아예 돌지 않는다."""
    w = worker(CORP)
    for s in ("eg.search", "eg.record"):
        d = w.gate.evaluate(w.skills.preview(s), declared_tools=w.compiled.declared_tools)
        assert d.allowed_without_human, f"{s} 가 HITL 로 막혔다 — 루프가 멈춘다"


def test_undeclared_tool_blocked(worker):
    """매니페스트에 없는 도구는 최소권한 위반."""
    w = worker(CCC)
    d = w.gate.evaluate(
        w.skills.preview("fin.ledger_read"), declared_tools=w.compiled.declared_tools
    )
    assert d.blocked


def test_hitl_request_lands_in_queue(worker, tmp_path):
    w = worker(CORP)
    run = w.run("경비 조회", extra_skills=[("fin.expense_read", {"request_id": "X"})])
    q = ApprovalQueue(tmp_path)
    assert run.hitl_requests, "HITL 요청이 만들어지지 않았다"
    ap = q.get(run.hitl_requests[0])
    assert ap.status == "pending" and ap.agent_id == CORP


def test_hitl_blocks_execution_until_approved(worker, monkeypatch, tmp_path):
    monkeypatch.delenv("DAWN_AUTO_APPROVE", raising=False)
    w = worker(CORP)
    run = w.run("경비 조회", extra_skills=[("fin.expense_read", {"request_id": "X"})])
    assert run.tool_calls == 0, "승인 전에 실행됐다"
    assert run.hitl_requests


def test_approval_is_append_only(tmp_path):
    q = ApprovalQueue(tmp_path)

    class _D:
        decision, reasons, severity, severity_label = "require_hitl", [], 3, "높음"
        assets, policies = [], []

    ap = q.request(agent_id="a", skill="s", gate_decision=_D(), args={})
    q.decide(ap.id, approve=True)
    with pytest.raises(ValueError):
        q.decide(ap.id, approve=False)  # 재판정 불가 — 감사 추적


# ── 모델 라우팅 ──────────────────────────────────────────────────────────


def test_routing_differs_per_org(worker):
    """자기검증 #2 — 조직마다 다른 모델을 EG 로 고른다."""
    a = worker(CORP).resolve_model(touches_l3=False)
    b = worker(CCC).resolve_model(touches_l3=False)
    assert a.model_policy_id != b.model_policy_id
    assert a.is_local and not b.is_local


def test_l3_forces_local_everywhere(worker):
    for aid in (CORP, CCC):
        r = worker(aid).resolve_model(touches_l3=True)
        assert r.is_local, f"{aid}: L3 인데 클라우드로 라우팅됐다"


def test_cloud_never_receives_l3():
    with pytest.raises(llm_mod.PolicyViolation):
        llm_mod.resolve("model:sonnet", touches_l3=True)


def test_missing_model_policy_is_an_error():
    with pytest.raises(llm_mod.PolicyViolation):
        llm_mod.resolve(None, touches_l3=False)


def test_l3_run_uses_local_model(worker):
    w = worker(CORP)
    w.run("L3 업무", touches_l3=True)
    assert w.client.calls and w.client.calls[-1].is_local


# ── 서킷 브레이커 ────────────────────────────────────────────────────────


def test_circuit_breaker_on_steps(worker):

    w = worker(CORP)
    w.budget = {"max_steps": 2}
    run = w.run("테스트")
    assert "CircuitBreaker" in run.error
    assert not run.complete


# ── 텔레메트리 ───────────────────────────────────────────────────────────


def test_span_tree_shape(worker):
    w = worker(CORP)
    w.run("테스트", extra_skills=[("fs.read", {"path": "COMPANY.md"})])
    names = [s.name for s in w.tracer.spans]
    assert "invoke_agent" in names and "chat" in names and "execute_tool" in names
    root = next(s for s in w.tracer.spans if s.name == "invoke_agent")
    assert root.parent_span_id is None
    assert all(s.trace_id == root.trace_id for s in w.tracer.spans), "trace_id 불일치"
    chat = next(s for s in w.tracer.spans if s.name == "chat")
    for key in (
        "gen_ai.operation.name",
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    ):
        assert key in chat.attributes, f"OTel GenAI 속성 누락: {key}"


def test_gate_decision_is_on_the_span(worker):
    w = worker(CORP)
    w.run("테스트", extra_skills=[("fin.expense_read", {"request_id": "X"})])
    tools = [s for s in w.tracer.spans if s.name == "execute_tool"]
    assert any(s.attributes.get("dawn.gate.decision") == "require_hitl" for s in tools)


def test_pii_is_masked():
    # 키 리터럴을 소스에 두지 않는다 — gitleaks 훅이 (옳게) 잡는다.
    # 규칙을 완화하는 대신 픽스처를 런타임에 조립한다.
    fake_key = "sk-" + "ant-" + "api03" + "a" * 20
    t = f"홍길동 900101-1234567 hong@corp.com 010-1234-5678 {fake_key}"
    m = mask_pii(t)
    assert "900101-1234567" not in m
    assert "hong@corp.com" not in m
    assert "010-1234-5678" not in m
    assert fake_key not in m, "API 키 패턴이 마스킹되지 않았다"


# ── 오케스트레이션 ───────────────────────────────────────────────────────


def test_verifier_must_differ_from_producer():
    a = [Assignment("x", "만들어라"), Assignment("x", "검증하라", role="verifier")]
    assert TeamOrchestrator.check_separation(a) == ["x"]


def test_dependency_ordering():
    a = [
        Assignment("b", "2", depends_on=["a"]),
        Assignment("a", "1"),
        Assignment("c", "3", depends_on=["b"]),
    ]
    assert [x.agent_id for x in TeamOrchestrator._order(a)] == ["a", "b", "c"]


def test_dependency_cycle_detected():
    from dawn_agents import OrchestratorError

    a = [Assignment("a", "1", depends_on=["b"]), Assignment("b", "2", depends_on=["a"])]
    with pytest.raises(OrchestratorError):
        TeamOrchestrator._order(a)


def test_orchestrator_rejects_outsider(reg):
    from dawn_agents import OrchestratorError

    o = TeamOrchestrator("itops-ccc", registry=reg)
    with pytest.raises(OrchestratorError):
        o.worker(CORP)


# ── 이벤트 구동 ──────────────────────────────────────────────────────────


def test_triggers_registered_for_work_docs():
    d = default_dispatcher()
    reg = d.registered()
    assert "security/alert-triage" in reg.get("siem.alert", [])
    assert "corporate/expense-processing" in reg.get("expense.submitted", [])


def test_unknown_event_does_nothing():
    d = default_dispatcher()
    assert d.emit(Event(type="nothing.here", source="test")) == []


def test_queue_roundtrip(tmp_path):
    q = WorkQueue(tmp_path)
    q.push(Event(type="expense.submitted", source="groupware", payload={"request_id": "X"}))
    assert q.depth() == 1
    assert q.pop().payload["request_id"] == "X"
    assert q.depth() == 0


def test_no_polling_loop_in_events_module():
    """이벤트 구동이어야 한다 — 상시 폴링 루프가 있으면 결함이다."""
    import inspect

    from dawn_agents import events

    src = inspect.getsource(events)
    assert "while True" not in src, "events 모듈에 폴링 루프가 있다"
    assert "time.sleep" not in src


# ── 스킬 안전 ────────────────────────────────────────────────────────────


def test_skill_must_be_in_catalog(reg):
    r = build_default_registry(reg.tool_catalog, root=reg.paths.root)
    with pytest.raises(SkillError):
        r.register("made.up_tool", lambda: None)


def test_path_traversal_blocked(reg):
    r = build_default_registry(reg.tool_catalog, root=reg.paths.root)
    with pytest.raises(SkillError):
        r.run("fs.read", path="../../etc/passwd")


def test_state_changing_command_refused(reg):
    r = build_default_registry(reg.tool_catalog, root=reg.paths.root)
    assert not r.run("sys.run_command", command="rm -rf /tmp/x").ok


def test_irreversible_skills_have_no_implementation(reg):
    r = build_default_registry(reg.tool_catalog, root=reg.paths.root)
    for n in ("pay.execute", "fin.ledger_write", "sec.firewall_change", "sys.deploy"):
        assert not r.preview(n).implemented, f"{n} 에 실행부가 있다 — P2 에서는 없어야 한다"
