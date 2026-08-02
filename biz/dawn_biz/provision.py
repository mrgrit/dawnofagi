"""작업 지시 ↔ 인프라 풀 연결 (P7 DoD-3).

    결재 완료 → **할당** → 착수
                  ↑ 여기

규칙은 `dawn_core.infrapool` 에 있고 여기는 **작업 지시의 상태를 옮기는 일**만 한다.
풀은 `org/`·`infra/` 만 읽고 업무 DB 를 모른다 — 그래야 공개 홈페이지가 업무 DB 로
가는 경로를 갖지 않는다는 P4 격리가 유지된다.

## 상태 전이

| 결과 | 상태 | 뜻 |
|---|---|---|
| `none` | `approved` 유지 | 환경이 필요 없다. 바로 편성·착수 |
| `ready` | `provisioning` | 자원이 잡혔다 |
| `waiting` | `waiting_infra` | **실패가 아니다.** 장비를 넣거나 누가 반납하면 이어서 돈다 |

거부(예외)는 규칙 위반일 때만이다 — 미승인·없는 존·사업이 허용하지 않은 등급.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 할당해도 되는 상태. 결재 전(`pending_approval`)은 여기 없다 — 할당은 비가역이다.
ALLOCATABLE = ("approved", "provisioning", "waiting_infra")


def provision(store, root: Path, order_id: int) -> Any:
    """작업 지시 하나에 자원을 붙인다.

    Raises:
        ValueError: 없는 지시 · 결재가 안 끝난 지시
        dawn_core.infrapool.PoolError: 규칙 위반 (존·등급·미승인)
    """
    from dawn_core import workintake
    from dawn_core.infrapool import allocate

    r = store.work_order(order_id)
    if r is None:
        raise ValueError(f"작업 지시 없음: {order_id}")
    if r["status"] not in ALLOCATABLE:
        raise ValueError(
            f"결재가 끝나지 않았다 (지금 {r['status']}) — 할당은 비가역 행동이다")

    # 존은 담당 본부에서 파생한다. 접수 때 비어 있었으면 여기서 채운다.
    zone = r["zone"] or workintake.zone_for(root, r["division"])
    a = allocate(root, order_id=order_id, tier=r["infra_tier"], zone=zone,
                 business=r["business"], approved=True)

    if a.state == "ready":
        store.set_work_order_status(order_id, "provisioning")
    elif a.state == "waiting":
        store.set_work_order_status(order_id, "waiting_infra")
    return a


def confirm_executed(store, root: Path, order_id: int, *, by: str) -> Any:
    """사람이 집행했다고 선언받고 이어서 돈다 (컨테이너 전용 경로).

    시스템이 검증한 것이 아니다 — 도커 권한이 없으니 확인할 방법이 없다.
    누가 그렇게 말했는지만 남긴다.
    """
    from dawn_core.infrapool import confirm

    a = confirm(root, order_id, by=by)
    store.set_work_order_status(order_id, "provisioning")
    return a


def deprovision(store, root: Path, order_id: int) -> Any:
    """반납. **삭제가 아니다** — 장비는 그대로 있고 원장에서 놓아줄 뿐이다.

    끝나지 않은 작업을 반납하면 상태를 `approved` 로 되돌린다. 안 그러면 자원이
    없는데 `provisioning` 으로 남아 — 화면은 준비됐다고 하고 실제로는 비어 있다.
    끝난 작업(`done`/`rejected`)은 건드리지 않는다. 그건 사실의 기록이다.
    """
    from dawn_core.infrapool import release

    a = release(root, order_id)
    r = store.work_order(order_id)
    if a is not None and r is not None and r["status"] in ("provisioning",
                                                           "waiting_infra"):
        store.set_work_order_status(order_id, "approved")
    return a


def retry_waiting(store, root: Path) -> list[Any]:
    """`waiting_infra` 로 멈춘 지시들을 다시 시도한다.

    장비를 풀에 넣거나 다른 작업이 반납한 뒤에 부른다. **대기는 실패가 아니라
    상태**라서, 조건이 바뀌면 사람이 다시 접수하지 않고 이어서 돌아야 한다.
    """
    out = []
    for r in store.work_orders():
        if r["status"] != "waiting_infra":
            continue
        try:
            out.append(provision(store, root, r["id"]))
        except Exception as e:                    # 한 건이 막아도 나머지는 돈다
            out.append(e)
    return out


__all__ = ["ALLOCATABLE", "confirm_executed", "deprovision", "provision",
           "retry_waiting"]
