"""상시 작업 — 사람이 시키지 않아도 돌아야 하는 일 (P7 DoD-6).

**상시 작업도 작업 지시다.** 기록·일지가 똑같이 남는다. 다른 점은 하나뿐 —
결재가 **최초 1회**이고 그 뒤로는 주기가 돌린다. 매 회차마다 결재를 받으면
관제가 사람의 응답 속도에 묶이고, 그러면 상시가 아니다.

## 왜 기록을 남기나

돌기만 하고 흔적이 없으면 **안 돈 것과 구별이 안 된다.** 관제가 15분마다 돈다고
믿고 있는데 3일 전부터 죽어 있었다는 것을 아무도 모르는 상태 — 그게 가장 나쁘다.
그래서 회차마다 일지(`document` kind=`worklog`)를 남기고, 실패도 남긴다.

## 무엇을 실행할 수 있나

`ACTIONS` 에 등록된 것만이다. `org/standing.yaml` 에 임의의 셸 명령을 적게 두면
그 파일이 곧 원격 실행 창구가 된다 — 매니페스트를 고칠 수 있는 사람이 곧 호스트를
가진 사람이 되면 안 된다.

## 스케줄러를 새로 만들지 않는다

cron 이든 systemd timer 든 바깥에서 `dawn-biz standing --tick` 을 부른다.
여기가 정하는 것은 **무엇이 지금 돌 차례인가**뿐이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 회차 기록. `var/` 라서 커밋되지 않는다 — 이건 운영 상태지 선언이 아니다.
STATE = Path("var") / "biz" / "standing.json"


@dataclass
class Item:
    """상시 작업 하나 (선언)."""

    id: str
    title: str
    action: str
    division: str = ""
    every: int = 60                 # 분
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Tick:
    """회차 하나 (결과)."""

    item_id: str
    ok: bool
    at: str = ""
    detail: str = ""
    order_id: int = 0
    worklog_id: int = 0

    def line(self) -> str:
        return (f"  {'✔' if self.ok else '✘'} {self.item_id:<14} "
                f"{self.detail[:70]}")


# ── 실행할 수 있는 것 (등록제) ───────────────────────────────────────────


def _act_aoc_cycle(root: Path, store) -> str:
    """관제 1회전 — 수집 → 탐지 → 트리아지.

    **관제 파이프라인을 새로 짜지 않는다.** `make aoc` 가 부르는 것과 같은 함수다.
    두 벌이 되면 상시로 도는 쪽과 사람이 돌리는 쪽의 판정이 갈라진다.
    """
    from dawn_aoc.collect import TraceLake
    from dawn_aoc.console import scan

    lake = TraceLake(root)
    runs = lake.all_runs()
    lake.persist(runs)
    st = lake.stats(runs)
    res = scan(root)
    return (f"스팬 {st['spans']} · run {st['runs']} · 스캔 {res['runs_scanned']} · "
            f"새 케이스 {len(res['new_cases'])}")


def _act_biz_intake(root: Path, store) -> str:
    """공개 홈페이지 접수함을 사내로 당겨 온다 (존 격리 — 미는 게 아니라 당긴다)."""
    from .events import ingest_inquiries, ingest_work_requests

    n, _ = ingest_inquiries(root, tenant=store.tenant)
    m, _ = ingest_work_requests(root, tenant=store.tenant)
    return f"문의 {n}건 · 작업 요청 {m}건"


def _act_infra_retry(root: Path, store) -> str:
    """준비대기로 멈춰 있던 작업 지시를 이어받는다."""
    from .provision import retry_waiting

    out = retry_waiting(store, root)
    ready = sum(1 for x in out if getattr(x, "state", "") == "ready")
    return f"재시도 {len(out)}건 · 준비됨 {ready}건"


def _act_kpi_review(root: Path, store) -> str:
    """자율화 등급을 올릴지 내릴지는 **측정된 수치**로 정한다. 인상으로 정하지 않는다.

    콘솔이 쓰는 것과 같은 상태를 읽는다 — 화면과 일지가 다른 숫자를 말하면 안 된다.
    """
    from dawn_aoc.console import build_state

    st = build_state(root)
    miss = [k["name"] for k in st["kpis"] if not k.get("sample")]
    hit = [f"{k['name']} {round(float(k['value']), 2):g}{k['unit']}"
           for k in st["kpis"] if k.get("sample")]
    note = f" · 표본 없음 {len(miss)}개" if miss else ""
    return ("; ".join(hit) or "수치 없음") + note


ACTIONS: dict[str, Callable[[Path, Any], str]] = {
    "aoc.cycle": _act_aoc_cycle,
    "biz.intake": _act_biz_intake,
    "infra.retry": _act_infra_retry,
    "kpi.review": _act_kpi_review,
}


# ── 선언 읽기 ────────────────────────────────────────────────────────────


def load(root: Path) -> list[Item]:
    """`org/standing.yaml`. 등록되지 않은 action 은 **거부한다** (조용히 넘기지 않는다)."""
    import yaml

    f = Path(root) / "org" / "standing.yaml"
    if not f.is_file():
        return []
    out = []
    for d in yaml.safe_load(f.read_text(encoding="utf-8")) or []:
        known = {k: v for k, v in d.items() if k in Item.__dataclass_fields__}
        it = Item(**known)
        if it.action not in ACTIONS:
            raise ValueError(
                f"{f}: 등록되지 않은 action — {it.action} "
                f"(가능: {', '.join(sorted(ACTIONS))})")
        if it.every <= 0:
            raise ValueError(f"{f}: {it.id} 의 주기가 0 이하다")
        out.append(it)
    return out


# ── 회차 상태 ────────────────────────────────────────────────────────────


def _state_path(root: Path) -> Path:
    return Path(root) / STATE


def state(root: Path) -> dict[str, Any]:
    f = _state_path(root)
    return json.loads(f.read_text(encoding="utf-8") or "{}") if f.is_file() else {}


def _save(root: Path, d: dict[str, Any]) -> None:
    f = _state_path(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(f)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def due(root: Path, *, now: datetime | None = None) -> list[Item]:
    """지금 돌 차례인 것. 한 번도 안 돌았으면 **바로 차례다.**"""
    now = now or _now()
    st, out = state(root), []
    for it in load(root):
        last = (st.get(it.id) or {}).get("at", "")
        if not last:
            out.append(it)
            continue
        gap = (now - datetime.fromisoformat(last)).total_seconds() / 60
        if gap >= it.every:
            out.append(it)
    return out


# ── 결재는 최초 1회 ──────────────────────────────────────────────────────


def register(root: Path, store) -> dict[str, int]:
    """상시 작업마다 작업 지시를 **하나씩** 만든다 (`origin: standing`).

    이미 있으면 만들지 않는다 — 회차마다 지시가 쌓이면 결재함이 잠긴다.
    최초 지시가 결재를 통과하면 그 뒤로는 주기가 돌린다.
    """
    have = {r["title"]: r["id"] for r in store.work_orders(origin="standing")}
    out = {}
    for it in load(root):
        if it.title in have:
            out[it.id] = have[it.title]
            continue
        wid = store.add_work_order(
            title=it.title, body=(it.why or "") + f"\n\n주기: {it.every}분 · "
            f"동작: {it.action}", origin="standing", requester="system",
            business="", division=it.division, infra_tier="none")
        store.set_work_order_status(wid, "pending_approval")
        out[it.id] = wid
    return out


def approved_orders(root: Path, store) -> dict[str, int]:
    """결재가 끝난 상시 작업만. 승인 전에는 **돌지 않는다.**"""
    by_title = {r["title"]: r for r in store.work_orders(origin="standing")}
    out = {}
    for it in load(root):
        r = by_title.get(it.title)
        if r is not None and r["status"] in ("approved", "provisioning",
                                             "in_progress", "done"):
            out[it.id] = r["id"]
    return out


# ── 회차 실행 ────────────────────────────────────────────────────────────


def run_one(root: Path, store, item: Item, *, order_id: int = 0) -> Tick:
    """한 회차. **실패해도 예외를 올리지 않는다** — 나머지 상시 작업이 멈춘다.

    대신 실패를 일지에 남긴다. 돌기만 하고 흔적이 없으면 안 돈 것과 구별이 안 된다.
    """
    at = _now().isoformat(timespec="seconds")
    try:
        detail = ACTIONS[item.action](root, store)
        t = Tick(item_id=item.id, ok=True, at=at, detail=detail, order_id=order_id)
    except Exception as e:                        # 한 건이 죽어도 나머지는 돈다
        t = Tick(item_id=item.id, ok=False, at=at,
                 detail=f"{type(e).__name__}: {e}", order_id=order_id)

    t.worklog_id = store.add_document(
        title=f"[상시] {item.title} — {at[:16].replace('T', ' ')}",
        body=(f"- 동작: `{item.action}`\n- 결과: {'성공' if t.ok else '**실패**'}\n"
              f"- 상세: {t.detail}\n- 작업 지시: #{order_id or '—'}\n"),
        author="system", org=item.division, tags=f"worklog,standing,{item.id}")

    st = state(root)
    st[item.id] = {"at": at, "ok": t.ok, "detail": t.detail[:200],
                   "worklog_id": t.worklog_id}
    _save(root, st)
    return t


def tick(root: Path, store, *, now: datetime | None = None,
         only: str = "") -> list[Tick]:
    """지금 차례인 것을 모두 돌린다. **결재를 통과한 것만.**"""
    ok_orders = approved_orders(root, store)
    out = []
    for it in due(root, now=now):
        if only and it.id != only:
            continue
        if it.id not in ok_orders:
            out.append(Tick(item_id=it.id, ok=False,
                            at=(now or _now()).isoformat(timespec="seconds"),
                            detail="결재 전이다 — 상시 작업도 최초 1회는 승인이 필요하다"))
            continue
        out.append(run_one(root, store, it, order_id=ok_orders[it.id]))
    return out


__all__ = [
    "ACTIONS",
    "STATE",
    "Item",
    "Tick",
    "approved_orders",
    "due",
    "load",
    "register",
    "run_one",
    "state",
    "tick",
]
