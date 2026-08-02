"""P7 DoD-3 — 인프라 할당.

여기서 지키는 것:

* **할당은 비가역 행동이다** — 결재 없이 집행하지 않는다.
* **풀이 비는 것은 실패가 아니라 대기다** — 장비를 넣으면 이어서 돈다.
* **거부는 규칙 위반일 때만** — 없는 존, 사업이 허용하지 않은 등급.
* **재고 파일(`infra/pool.yaml`)은 기계가 안 쓴다** — 사람 것이다.
"""

from __future__ import annotations

import shutil

import pytest
from dawn_core import infrapool
from dawn_core.infrapool import (
    PoolError,
    allocate,
    available,
    confirm,
    ledger,
    load_pool,
    release,
    summary,
)
from dawn_core.paths import Paths

POOL = """\
limits:
  container_max: 2
hosts:
  - id: node-01
    kind: server
    zone: dmz
    address: 192.168.0.201
    cpu: 16
    memory_gb: 64
    status: available
  - id: node-02
    kind: vm
    zone: dmz
    cpu: 4
    memory_gb: 8
    status: maintenance
"""


@pytest.fixture
def pool_root(tmp_path):
    """실물 저장소를 복제하지 않는다 — `infra/pool.yaml` 과 앵커만 있으면 된다."""
    real = Paths().root
    (tmp_path / "COMPANY.md").write_text("# 테스트 앵커\n", encoding="utf-8")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "pool.yaml").write_text(POOL, encoding="utf-8")
    # 사업·본부 규칙까지 보는 테스트가 있으므로 레지스트리가 읽는 것은 실물을 링크한다.
    # (`work/` 를 빼면 에이전트의 업무 참조가 깨져 무결성 검사에서 걸린다)
    for d in ("org", "work"):
        (tmp_path / d).symlink_to(real / d)
    return tmp_path


# ── 승인 게이트 ──────────────────────────────────────────────────────────


def test_allocation_requires_approval(pool_root):
    """할당은 자원을 점유하는 비가역 행동이다. 결재가 먼저다."""
    with pytest.raises(PoolError, match="결재"):
        allocate(pool_root, order_id=1, tier="server", zone="dmz", approved=False)
    assert ledger(pool_root) == [], "거부됐는데 원장에 남았다"


# ── 대기 ≠ 실패 ──────────────────────────────────────────────────────────


def test_empty_pool_waits_instead_of_failing(pool_root):
    """풀이 비면 예외가 아니라 대기다 — 장비를 넣거나 누가 반납하면 이어서 돈다."""
    a = allocate(pool_root, order_id=2, tier="vm", zone="dmz", approved=True)
    assert a.state == "waiting", "가용 vm 이 없는데 잡혔다"
    assert "pool.yaml" in a.reason, "무엇을 하면 풀리는지 안 알려준다"


def test_maintenance_host_is_not_allocatable(pool_root):
    """사람이 `maintenance` 로 내린 장비는 기계가 도로 꺼내 쓰지 않는다."""
    assert [h.id for h in available(pool_root, "vm")] == []
    assert [h.id for h in available(pool_root, "server")] == ["node-01"]


# ── 규칙 위반은 거부 ─────────────────────────────────────────────────────


def test_container_rejects_a_zone_that_is_not_a_container_zone(pool_root):
    with pytest.raises(PoolError, match="컨테이너 존이 아니다"):
        allocate(pool_root, order_id=3, tier="container", zone="없는존", approved=True)


def test_external_host_cannot_be_asked_for_a_container_zone(pool_root):
    """외부 장비는 물리 LAN(192.168.0.x), 컨테이너는 도커 브리지(10.20.x) 다.
    같은 존 이름을 써도 L3 경로가 다르다 — 라우팅이 정해지기 전에는 거부한다 (Q10)."""
    with pytest.raises(PoolError, match="Q10"):
        allocate(pool_root, order_id=4, tier="server", zone="int", approved=True)


def test_zone_is_required_when_a_resource_is_involved(pool_root):
    """존 없이는 게이트도 심각도도 계산이 안 된다."""
    with pytest.raises(PoolError, match="존"):
        allocate(pool_root, order_id=5, tier="server", zone="", approved=True)


def test_business_that_forbids_a_tier_is_rejected(pool_root):
    """등급 제한은 접수에서 한 번 보지만 할당에서 **다시** 본다 — 경계는 겹쳐야 한다.

    접수와 할당 사이에 사업 매니페스트가 바뀔 수 있고, 접수를 거치지 않는 경로
    (CLI·상시 작업)도 있다. 한 곳만 보면 그 경로가 곧 구멍이다.
    """
    from dawn_core.workintake import INFRA_TIERS, choices

    cs = choices(pool_root, include_planned=True)
    pair = next(((c.id, t) for c in cs for t in INFRA_TIERS if t not in c.tiers), None)
    assert pair, "어떤 사업도 어떤 등급도 막지 않는다 — 제한이 사실상 없다"
    bid, tier = pair
    with pytest.raises(ValueError, match="허용하지 않는다"):
        allocate(pool_root, order_id=6, tier=tier, zone="dmz",
                 business=bid, approved=True)


# ── 할당 · 반납 ──────────────────────────────────────────────────────────


def test_allocate_then_release_returns_the_host_to_the_pool(pool_root):
    a = allocate(pool_root, order_id=7, tier="server", zone="dmz", approved=True)
    assert (a.state, a.host_id) == ("ready", "node-01")
    assert available(pool_root, "server") == [], "잡혔는데 여전히 가용으로 보인다"

    # 같은 장비를 다른 작업이 못 가져간다
    b = allocate(pool_root, order_id=8, tier="server", zone="dmz", approved=True)
    assert b.state == "waiting", "하나뿐인 장비가 두 작업에 나갔다"

    assert release(pool_root, 7).state == "released"
    assert [h.id for h in available(pool_root, "server")] == ["node-01"], "반납이 안 됐다"


def test_allocating_twice_does_not_take_two_resources(pool_root):
    """두 번 눌러도 자원이 두 개 나가면 안 된다 (화면 새로고침·재시도)."""
    a = allocate(pool_root, order_id=9, tier="server", zone="dmz", approved=True)
    b = allocate(pool_root, order_id=9, tier="server", zone="dmz", approved=True)
    assert (a.host_id, a.state) == (b.host_id, b.state)
    assert sum(1 for x in ledger(pool_root) if x.state == "ready") == 1


def test_container_cap_is_respected(pool_root, monkeypatch):
    """el34 랩이 이미 돌고 있다 — 한도를 넘으면 대기시킨다."""
    monkeypatch.setattr(infrapool, "docker_reachable", lambda: (True, ""))
    for i in (11, 12):
        assert allocate(pool_root, order_id=i, tier="container", zone="dmz",
                        approved=True).state == "ready"
    assert allocate(pool_root, order_id=13, tier="container", zone="dmz",
                    approved=True).state == "waiting", "한도(2)를 넘겨 잡혔다"


def test_no_tier_takes_nothing(pool_root):
    a = allocate(pool_root, order_id=14, tier="none", approved=True)
    assert a.state == "none" and not a.host_id and not a.container


# ── 권한이 없으면 명령서를 낸다 ──────────────────────────────────────────


def test_without_docker_it_hands_over_a_command_instead_of_pretending(pool_root,
                                                                     monkeypatch):
    """에이전트가 도커 소켓을 쥐면 호스트 루트와 다름없다 — 없는 것이 맞다.
    없으면 **못 한다고 말하고 명령서를 낸다.** 한 척하지 않는다."""
    monkeypatch.setattr(infrapool, "docker_reachable", lambda: (False, "접근 거부"))
    a = allocate(pool_root, order_id=15, tier="container", zone="dmz", approved=True)
    assert a.state == "waiting"
    assert "el34-dmz" in a.command, "어느 존 네트워크인지가 명령에 없다"
    assert a.network == "el34-dmz"


def test_human_confirmation_is_recorded_as_an_assertion(pool_root, monkeypatch):
    """도커를 못 만지니 컨테이너가 진짜 떴는지 **확인할 수 없다.**
    확인할 수 없는 것을 확인한 척하지 않고, 누가 그렇게 말했는지 남긴다."""
    monkeypatch.setattr(infrapool, "docker_reachable", lambda: (False, "접근 거부"))
    allocate(pool_root, order_id=16, tier="container", zone="dmz", approved=True)
    a = confirm(pool_root, 16, by="ccc")
    assert a.state == "ready"
    assert "ccc" in a.reason and "검증한 것은 아니다" in a.reason
    with pytest.raises(PoolError):
        confirm(pool_root, 16, by="ccc")            # 이미 ready — 두 번 확인 못 한다


# ── 사람의 파일을 기계가 안 쓴다 ─────────────────────────────────────────


def test_the_inventory_file_is_never_written(pool_root):
    """`infra/pool.yaml` 은 사람이 쓰는 재고다. 할당이 이걸 덮어쓰면 주석·포맷이
    날아간다 (`crew.py` 의 팀 명부에서 이미 겪었다). 할당은 원장에만 쌓인다."""
    f = pool_root / "infra" / "pool.yaml"
    before = f.read_text(encoding="utf-8")
    allocate(pool_root, order_id=17, tier="server", zone="dmz", approved=True)
    release(pool_root, 17)
    assert f.read_text(encoding="utf-8") == before, "재고 파일이 바뀌었다"
    assert (pool_root / "var" / "infra" / "allocations.json").is_file()


def test_summary_counts_what_is_actually_free(pool_root):
    allocate(pool_root, order_id=18, tier="server", zone="dmz", approved=True)
    d = summary(pool_root)
    assert d["hosts_total"] == 2
    assert d["hosts_free"]["server"] == 0 and d["hosts_by_kind"]["server"] == 1
    assert d["hosts_free"]["vm"] == 0, "maintenance 장비를 가용으로 셌다"


def test_pool_loads_when_the_file_is_missing(tmp_path):
    """풀 파일이 없어도 죽지 않는다 — 아직 장비를 안 넣은 상태가 정상이다."""
    (tmp_path / "COMPANY.md").write_text("#\n", encoding="utf-8")
    limits, hosts = load_pool(tmp_path)
    assert (limits, hosts) == ({}, [])


# ── 작업 지시와의 연결 ───────────────────────────────────────────────────


def test_provision_moves_the_work_order_and_refuses_before_approval(pool_root):
    """상태 전이: ready → provisioning · waiting → waiting_infra · 결재 전 → 거부."""
    from dawn_biz.provision import provision
    from dawn_biz.store import BizStore

    s = BizStore(pool_root, tenant=0)
    wid = s.add_work_order(title="[테스트] 서버 필요", body="", origin="internal",
                           business="", division="aoc", infra_tier="server")
    s.set_work_order_status(wid, "pending_approval")
    with pytest.raises(ValueError, match="결재"):
        provision(s, pool_root, wid)

    s.set_work_order_status(wid, "approved")
    a = provision(s, pool_root, wid)
    assert a.state == "ready" and a.host_id == "node-01"
    assert s.work_order(wid)["status"] == "provisioning"
    assert a.zone == "dmz", "존이 담당 본부(aoc)에서 파생되지 않았다"


def test_waiting_orders_resume_when_a_host_appears(pool_root):
    """대기는 상태지 실패가 아니다 — 장비가 들어오면 사람이 다시 접수하지 않는다."""
    from dawn_biz.provision import provision, retry_waiting
    from dawn_biz.store import BizStore

    s = BizStore(pool_root, tenant=0)
    wid = s.add_work_order(title="[테스트] vm 필요", body="", origin="internal",
                           business="", division="aoc", infra_tier="vm")
    s.set_work_order_status(wid, "approved")
    assert provision(s, pool_root, wid).state == "waiting"
    assert s.work_order(wid)["status"] == "waiting_infra"

    f = pool_root / "infra" / "pool.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("status: maintenance",
                                                       "status: available"),
                 encoding="utf-8")
    out = retry_waiting(s, pool_root)
    assert [x.state for x in out] == ["ready"]
    assert s.work_order(wid)["status"] == "provisioning"


def test_docker_probe_says_no_rather_than_raising():
    """탐지가 예외를 던지면 할당 전체가 죽는다. 못 만지면 '못 만진다'고 답한다."""
    ok, why = infrapool.docker_reachable()
    assert isinstance(ok, bool)
    if not ok:
        assert why, "왜 못 만지는지가 비었다"
    if not shutil.which("docker"):
        assert ok is False


def test_release_does_not_leave_a_ghost_provisioning_state(pool_root):
    """반납했는데 상태가 `provisioning` 으로 남으면 화면은 준비됐다고 하고
    실제로는 비어 있다. 끝나지 않은 작업은 `approved` 로 되돌린다."""
    from dawn_biz.provision import deprovision, provision
    from dawn_biz.store import BizStore

    s = BizStore(pool_root, tenant=0)
    wid = s.add_work_order(title="[테스트] 반납", body="", origin="internal",
                           business="", division="aoc", infra_tier="server")
    s.set_work_order_status(wid, "approved")
    assert provision(s, pool_root, wid).state == "ready"
    assert s.work_order(wid)["status"] == "provisioning"

    deprovision(s, pool_root, wid)
    assert s.work_order(wid)["status"] == "approved", "자원 없이 provisioning 으로 남았다"


def test_release_does_not_rewrite_a_finished_order(pool_root):
    """끝난 작업의 상태는 사실의 기록이다 — 반납이 그걸 고쳐 쓰지 않는다."""
    from dawn_biz.provision import deprovision, provision
    from dawn_biz.store import BizStore

    s = BizStore(pool_root, tenant=0)
    wid = s.add_work_order(title="[테스트] 완료 후 반납", body="", origin="internal",
                           business="", division="aoc", infra_tier="server")
    s.set_work_order_status(wid, "approved")
    provision(s, pool_root, wid)
    for st in ("in_progress", "reviewing_output", "done"):
        s.set_work_order_status(wid, st)
    deprovision(s, pool_root, wid)
    assert s.work_order(wid)["status"] == "done"


# ── 존 경유 (Q10) ────────────────────────────────────────────────────────


def _open_transit(pool_root, zones: str):
    """`routing` 은 최상위 키다 — 실측으로 `load_pool(root)[0]` (limits) 를 뒤지는
    버그가 있어서 경로를 열어도 인정되지 않았다."""
    f = pool_root / "infra" / "pool.yaml"
    f.write_text(f.read_text(encoding="utf-8")
                 + f"\nrouting:\n  enabled: true\n  zones: [{zones}]\n  via: pipe\n",
                 encoding="utf-8")


def test_transit_is_closed_unless_declared(pool_root):
    """기본은 닫힘. 전부 여는 기본값을 두지 않는다."""
    from dawn_core.infrapool import transit_open

    assert transit_open(pool_root, "dmz") is False


def test_only_declared_zones_are_reachable(pool_root):
    from dawn_core.infrapool import transit_open

    _open_transit(pool_root, "dmz")
    assert transit_open(pool_root, "dmz") is True
    assert transit_open(pool_root, "int") is False, "선언 안 한 존이 열렸다"


def test_declared_transit_makes_a_lan_host_allocatable(pool_root):
    """경로를 열었다고 적으면 존이 달라도 쓸 수 있어야 한다 — 안 그러면
    `transit_open` 이 검사만 통과시키고 **실제로는 아무것도 못 잡는다.**"""
    from dawn_core.infrapool import PoolError, allocate

    f = pool_root / "infra" / "pool.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("zone: dmz\n    address",
                                                       "zone: lan\n    address"),
                 encoding="utf-8")
    with pytest.raises(PoolError, match="Q10"):          # 열기 전
        allocate(pool_root, order_id=21, tier="server", zone="dmz", approved=True)

    _open_transit(pool_root, "dmz")
    a = allocate(pool_root, order_id=21, tier="server", zone="dmz", approved=True)
    assert a.state == "ready" and a.host_id == "node-01"
    assert "pipe 경유" in a.reason, "경유로 잡혔다는 사실이 안 남았다"
