"""P7 DoD-6 — 상시 작업.

여기서 지키는 것:

* **결재는 최초 1회.** 승인 전에는 안 돌고, 승인 뒤에는 매 회차 결재를 안 받는다.
* **돌면 흔적이 남는다.** 실패도 남는다 — 안 남으면 안 돈 것과 구별이 안 된다.
* **실행할 수 있는 것은 등록된 것뿐.** 매니페스트가 원격 실행 창구가 되면 안 된다.
* **한 건이 죽어도 나머지는 돈다.**
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dawn_biz import standing
from dawn_biz.store import BizStore
from dawn_core.paths import Paths

DECL = """\
- id: alpha
  title: "[테스트] 알파"
  action: test.ok
  division: corp
  every: 15
  why: 테스트
- id: beta
  title: "[테스트] 베타"
  action: test.boom
  division: corp
  every: 30
"""


@pytest.fixture
def sroot(tmp_path, monkeypatch):
    """선언과 동작을 테스트용으로 갈아 끼운다 — 실물 관제를 돌리지 않는다."""
    _wire(tmp_path, monkeypatch)
    return tmp_path


_CALLS: dict[str, list[str]] = {}


def _wire(tmp_path, monkeypatch) -> list[str]:
    (tmp_path / "COMPANY.md").write_text("# 테스트 앵커\n", encoding="utf-8")
    (tmp_path / "org").mkdir(exist_ok=True)
    (tmp_path / "org" / "standing.yaml").write_text(DECL, encoding="utf-8")

    calls: list[str] = []

    def ok(root, store):
        calls.append("alpha")
        return "했다"

    def boom(root, store):
        calls.append("beta")
        raise RuntimeError("터졌다")

    monkeypatch.setitem(standing.ACTIONS, "test.ok", ok)
    monkeypatch.setitem(standing.ACTIONS, "test.boom", boom)
    _CALLS[str(tmp_path)] = calls
    return calls


@pytest.fixture
def store(sroot):
    return BizStore(sroot, tenant=0)


def _approve_all(sroot, store):
    for r in store.work_orders(origin="standing"):
        store.set_work_order_status(r["id"], "approved")


# ── 결재는 최초 1회 ──────────────────────────────────────────────────────


def test_does_not_run_before_approval(sroot, store):
    """상시 작업도 작업 지시다 — 최초 1회는 사람이 승인한다."""
    standing.register(sroot, store)
    ticks = standing.tick(sroot, store)
    assert ticks and not any(t.ok for t in ticks)
    assert all("결재" in t.detail for t in ticks)
    assert _CALLS[str(sroot)] == [], "결재 전인데 동작이 실행됐다"


def test_registering_twice_does_not_pile_up_orders(sroot, store):
    """회차마다 지시가 쌓이면 결재함이 잠긴다."""
    a = standing.register(sroot, store)
    b = standing.register(sroot, store)
    assert a == b
    assert len(store.work_orders(origin="standing")) == 2


def test_after_approval_each_cycle_needs_no_new_approval(sroot, store):
    """승인 뒤에는 주기가 돌린다. 매 회차 결재를 받으면 상시가 아니다."""
    standing.register(sroot, store)
    _approve_all(sroot, store)

    standing.tick(sroot, store)
    n_after_first = len(store.work_orders(origin="standing"))

    # 주기가 지난 것처럼 시계를 앞으로 돌린다
    later = datetime.now(timezone.utc) + timedelta(minutes=60)
    standing.tick(sroot, store, now=later)

    assert len(store.work_orders(origin="standing")) == n_after_first, \
        "회차마다 새 작업 지시가 생겼다"
    assert _CALLS[str(sroot)].count("alpha") == 2, "두 번째 회차가 안 돌았다"


# ── 주기 ─────────────────────────────────────────────────────────────────


def test_never_run_means_due_now(sroot, store):
    assert {i.id for i in standing.due(sroot)} == {"alpha", "beta"}


def test_not_due_until_the_interval_passes(sroot, store):
    standing.register(sroot, store)
    _approve_all(sroot, store)
    standing.tick(sroot, store)
    assert standing.due(sroot) == [], "방금 돌았는데 또 차례다"

    soon = datetime.now(timezone.utc) + timedelta(minutes=20)
    assert {i.id for i in standing.due(sroot, now=soon)} == {"alpha"}, \
        "15분짜리만 차례여야 한다 (베타는 30분)"


# ── 기록 ─────────────────────────────────────────────────────────────────


def test_every_cycle_leaves_a_worklog(sroot, store):
    """돌기만 하고 흔적이 없으면 **안 돈 것과 구별이 안 된다.**"""
    standing.register(sroot, store)
    _approve_all(sroot, store)
    ticks = standing.tick(sroot, store)
    for t in ticks:
        assert t.worklog_id, f"{t.item_id}: 일지가 없다"
        doc = store.document(t.worklog_id)
        assert doc is not None, f"{t.item_id}: 일지 id 는 있는데 문서가 없다"
        assert "standing" in (doc["tags"] or ""), "태그가 없으면 나중에 못 찾는다"
        assert t.item_id in (doc["tags"] or "")


def test_failure_is_recorded_not_swallowed(sroot, store):
    """실패를 안 남기면 조용히 죽은 상시 작업을 아무도 못 찾는다."""
    standing.register(sroot, store)
    _approve_all(sroot, store)
    ticks = {t.item_id: t for t in standing.tick(sroot, store)}
    bad = ticks["beta"]
    assert not bad.ok and "터졌다" in bad.detail
    assert bad.worklog_id, "실패 회차에 일지가 없다"
    assert standing.state(sroot)["beta"]["ok"] is False


def test_one_failure_does_not_stop_the_others(sroot, store):
    standing.register(sroot, store)
    _approve_all(sroot, store)
    ticks = standing.tick(sroot, store)
    assert {t.item_id for t in ticks} == {"alpha", "beta"}
    assert sum(t.ok for t in ticks) == 1


# ── 실행할 수 있는 것은 등록된 것뿐 ──────────────────────────────────────


def test_unknown_action_is_refused_loudly(sroot):
    """매니페스트에 임의의 동작을 적게 두면 그 파일이 원격 실행 창구가 된다."""
    f = sroot / "org" / "standing.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("test.ok", "sh -c rm-rf"),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="등록되지 않은 action"):
        standing.load(sroot)


def test_zero_interval_is_refused(sroot):
    """주기 0 은 무한 루프다."""
    f = sroot / "org" / "standing.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("every: 15", "every: 0"),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="주기"):
        standing.load(sroot)


# ── 실물 선언 ────────────────────────────────────────────────────────────


def test_the_real_declaration_is_valid():
    """`org/standing.yaml` 이 깨지면 상시 운영이 통째로 멈춘다."""
    items = standing.load(Paths().root)
    assert items, "상시 작업이 하나도 선언돼 있지 않다"
    assert all(i.action in standing.ACTIONS for i in items)
    assert len({i.id for i in items}) == len(items), "id 가 겹친다"
    assert len({i.title for i in items}) == len(items), \
        "제목이 겹친다 — 작업 지시를 제목으로 찾으므로 섞인다"


def test_registered_actions_take_root_and_store():
    """동작의 모양이 갈라지면 tick 이 런타임에 죽는다."""
    import inspect

    for name, fn in standing.ACTIONS.items():
        p = list(inspect.signature(fn).parameters)
        assert p[:2] == ["root", "store"], f"{name}: 인자 모양이 다르다 — {p}"
