"""승인 큐 — **사람이 볼 것과 안 볼 것을 가른다.**

여기서 지키는 것은 큐의 기능이 아니라 **큐가 거짓말하지 않는가**다.
실측(2026-08-02)으로 두 가지가 거짓말하고 있었다:

1. **훈련이 사람의 큐를 막았다.** 대기 2,636건 중 실업무 0건이고 2,303건이
   인시던트 리허설이었다. 요청 내용은 `aoc.kill` 701 · `aoc.revoke_credentials`
   703 — 승인하면 진짜로 집행된다. KPI 에서 겪은 것과 같은 오염이다.
2. **승인해도 집행되지 않는데 화면이 그렇게 말하지 않았다.** 워커는 요청을 넣고
   그 자리에서 run 을 끝낸다. 실제로 `aoc.kill` 2건이 승인된 채 남아 있었다.
"""

from __future__ import annotations

import dataclasses

import pytest

# ── 훈련이 사람의 큐를 막지 않는다 ────────────────────────────────────────


@dataclasses.dataclass
class _Shim:
    """게이트 판정 자리 — 큐만 보는 테스트라 실물이 필요 없다."""

    decision: str = "require_hitl"
    reasons: list = dataclasses.field(default_factory=lambda: ["테스트"])
    severity: int = 6
    severity_label: str = "치명"
    assets: list = dataclasses.field(default_factory=list)
    policies: list = dataclasses.field(default_factory=list)


def _req(q, purpose, **kw):
    return q.request(agent_id="t", skill="pay.execute", gate_decision=_Shim(),
                     args={}, purpose=purpose, **kw)


def test_pending_defaults_to_everything_but_can_filter(tmp_path):
    """실측: 대기 2633건 중 2303건이 인시던트 리허설이라 사람이 볼 것을 못 찾았다.
    KPI 에서 겪은 것과 **같은 오염**이고, 같은 방식으로 목적을 태그해 가른다."""
    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(tmp_path)
    _req(q, "work")
    for _ in range(5):
        _req(q, "drill")
    _req(q, "redteam")

    assert len(q.pending()) == 7, "필터 없이는 전부 보여야 한다"
    assert len(q.pending(purpose="work")) == 1, "실업무만 골라야 한다"
    assert q.counts() == {"work": 1, "drill": 5, "redteam": 1}


def test_drills_are_kept_not_deleted(tmp_path):
    """지우면 안 된다 — 리허설이 게이트를 제대로 때렸다는 사실 자체가 증거다."""
    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(tmp_path)
    d = _req(q, "drill")
    assert q.get(d.id).status == "pending"
    assert not q.get(d.id).is_work
    assert "(drill)" in q.get(d.id).line(), "화면에서 훈련임이 안 보인다"


def test_untagged_requests_are_not_counted_as_work(tmp_path):
    """목적을 안 준 요청이 실업무로 새면 필터가 무의미해진다 (기본값은 unknown)."""
    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(tmp_path)
    ap = q.request(agent_id="t", skill="x", gate_decision=_Shim(), args={})
    assert ap.purpose == "unknown" and not ap.is_work
    assert q.pending(purpose="work") == []


def test_worker_tags_its_runs_purpose(tmp_path):
    """워커가 목적을 안 흘려보내면 큐가 다시 오염된다 — 호출부를 고정한다."""
    import inspect

    from dawn_agents import worker

    src = inspect.getsource(worker)
    assert src.count("purpose=run.purpose") >= 2, \
        "worker 의 두 승인 경로(block·require_hitl)가 목적을 안 넘긴다"


def test_a_request_whose_run_ended_says_so(tmp_path):
    """이 시스템의 HITL 은 "멈추고 기록"이지 "기다렸다 재개"가 아니다.

    화면이 그걸 안 말하면 사람이 눌러 놓고 **집행됐다고 믿는다.** 실측으로
    `aoc.kill` 2건이 승인돼 있었는데 아무 에이전트도 죽지 않았다 — 이번엔
    다행이었지만, 반대로 틀렸다면(집행됐는데 안 됐다고 믿음) 훨씬 나빴다.
    """
    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(tmp_path)
    ap = _req(q, "work")
    assert ap.decides_execution, "새 요청은 아직 집행으로 이어질 수 있다"

    q.mark_run_ended(ap.id)
    got = q.get(ap.id)
    assert got.run_ended and not got.decides_execution
    assert got.status == "pending", "만료시키면 안 된다 — 판단 기록으로는 유효하다"
    assert "집행 안 됨" in got.line()


def test_worker_marks_the_run_ended_at_both_exits(tmp_path):
    """워커의 두 출구(block · 승인대기)에서 모두 표시해야 한다 —
    한 쪽만 하면 나머지 경로가 조용히 거짓말한다."""
    import inspect

    from dawn_agents import worker

    assert inspect.getsource(worker).count("mark_run_ended") >= 2


def test_expire_needs_a_reason_and_is_not_denial(tmp_path):
    """거부(denied)로 쓰면 거짓말이 된다 — 사람이 본 적 없는 요청에 판단 기록을
    남기면 "이 사람은 kill 을 700번 거부했다"는 잘못된 이력이 만들어진다."""
    from dawn_agents.hitl import ApprovalQueue

    q = ApprovalQueue(tmp_path)
    _req(q, "drill")
    _req(q, "work")

    with pytest.raises(ValueError, match="사유"):
        q.expire(purpose="drill", note="")

    done = q.expire(purpose="drill", note="리허설 잔여 — 사람이 판단할 건이 아니다")
    assert len(done) == 1
    got = q.get(done[0])
    assert got.status == "expired" and got.decided_by == "system"
    assert len(q.pending()) == 1, "실업무까지 만료시켰다"
