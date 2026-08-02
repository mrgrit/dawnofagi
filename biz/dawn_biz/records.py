"""작업 지시가 끝나면 남는 것 — 일지·보고서·정산 (P7 DoD-7).

    착수 → 검수 → **일지 · 보고서 · 정산 · 반납** → 완료

기록을 자동으로 남기는 이유는 감사 때문만이 아니다. **사람이 나중에 "이 작업이
뭘 했지"를 물었을 때 트레이스를 뒤지게 하면 아무도 안 뒤진다.** 읽을 수 있는
문서로 남아야 실제로 읽힌다.

## 사용량과 금액을 갈라놓는다

`usage()` 는 **잰다** — 토큰·시간·인프라 점유. 이건 사실이라 판단이 안 들어간다.
`settle()` 은 **값을 매긴다** — `org/ratecard.yaml` 의 단가를 곱한다. 단가가
`미정` 이면 **금액을 0 으로 만들지 않고 미정 항목으로 센다.** 0 과 미정을 섞으면
로컬 모델이 공짜로 보이고 원가가 실제보다 싸게 잡힌다.

**원가만 낸다. 청구액이 아니다** (Q9-③ 미정). 고객에게 얼마를 받을지는 계약
판단이고, 여기서 나온 숫자가 그대로 견적이 되면 안 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UNSET = "미정"                       # 0(판단함)과 구별한다


@dataclass
class Usage:
    """이 작업 지시가 **실제로 쓴 것.** 사실만 — 값은 안 매긴다."""

    order_id: int
    runs: int = 0
    completed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    wall_ms: int = 0
    local_gpu_ms: int = 0           # 사내 GPU 점유 — 로컬 모델은 시간으로 잡는다
    tool_calls: int = 0
    blocked: int = 0
    infra_tier: str = "none"
    infra_hours: float = 0.0
    agents: list[str] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Settlement:
    """원가. **청구액이 아니다.**"""

    order_id: int
    currency: str = "KRW"
    model_cost: int = 0
    infra_cost: int = 0
    unpriced: list[str] = field(default_factory=list)   # 쟀지만 값을 못 매긴 것
    lines: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.model_cost + self.infra_cost

    @property
    def complete(self) -> bool:
        """값을 다 매겼나. 아니면 이 숫자는 **하한**이다."""
        return not self.unpriced

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "total": self.total, "complete": self.complete}


# ── 잰다 ─────────────────────────────────────────────────────────────────


def usage(root: Path, order_id: int) -> Usage:
    """작업 지시 하나가 쓴 것을 모은다.

    작업용 에이전트는 `wo<id>-<role>` 로 편성되므로 **agent_id 가 곧 연결 고리다.**
    별도 매핑 테이블을 두지 않는다 — 두면 편성과 정산이 어긋날 수 있다.
    """
    from dawn_aoc.collect import TraceLake
    from dawn_core.infrapool import allocation_of

    u = Usage(order_id=order_id)
    prefix = f"wo{order_id}-"
    for r in TraceLake(root).all_runs():
        if not r.agent_id.startswith(prefix):
            continue
        u.runs += 1
        u.completed += int(bool(r.complete))
        u.tokens_in += r.tokens_in
        u.tokens_out += r.tokens_out
        u.wall_ms += int(r.duration_ms or 0)
        if r.model_local:
            u.local_gpu_ms += int(r.duration_ms or 0)
        u.tool_calls += int(r.tool_calls or 0)
        u.blocked += len(r.blocked or [])
        m = u.by_model.setdefault(r.model or "(미상)",
                                  {"in": 0, "out": 0, "runs": 0, "local": 0})
        m["in"] += r.tokens_in
        m["out"] += r.tokens_out
        m["runs"] += 1
        m["local"] = int(bool(r.model_local))
        if r.agent_id not in u.agents:
            u.agents.append(r.agent_id)
        if r.trace_id not in u.traces:
            u.traces.append(r.trace_id)

    a = allocation_of(root, order_id)
    if a is not None:
        u.infra_tier = a.tier
        # 점유 시간은 원장에 시작 시각이 없다 — 실제 시간을 못 재면 재지 않는다.
        # 잘못된 숫자보다 없는 숫자가 낫다 (정산서에 미정으로 뜬다).
    return u


# ── 값을 매긴다 ──────────────────────────────────────────────────────────


def rates(root: Path) -> dict[str, Any]:
    import yaml

    f = Path(root) / "org" / "ratecard.yaml"
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {} if f.is_file() else {}


def hourly(rc: dict[str, Any], name: str) -> float | str:
    """장비 하나의 **시간당 원가.** 채워진 값에서 계산한다 — 따로 적지 않는다.

        시간당 = 구입가 / (수명연수 × 8760) + 소비전력W / 1000 × 전기요금

    시간당 원가를 사람이 직접 적게 두면 구입가를 바꿔도 안 따라온다. 입력은
    **견적서와 고지서에서 읽을 수 있는 것**만 받고 나머지는 여기서 낸다.
    하나라도 미정이면 결과가 `미정` 이다 — 빠진 항을 0 으로 치면 원가가 싸게 잡힌다.
    """
    hw = ((rc.get("hardware") or {}).get(name)) or {}
    if not hw:
        return UNSET
    price, life = hw.get("구입가"), hw.get("수명연수")
    watt, kwh = hw.get("소비전력w"), rc.get("electricity_krw_kwh")
    if any(not isinstance(v, (int, float)) for v in (price, life, watt, kwh)):
        return UNSET
    if not life:
        return UNSET
    return price / (life * 8760) + (watt / 1000) * kwh


def _infra_hourly(rc: dict[str, Any], tier: str) -> float | str:
    """등급 → 시간당 원가. 등급은 **장비를 가리키고**, 값은 그 장비에서 나온다."""
    v = (rc.get("infra_hour") or {}).get(tier, UNSET)
    if isinstance(v, (int, float)):
        return v                                # 직접 적힌 값 (예: none: 0)
    if not isinstance(v, str) or v == UNSET:
        return UNSET
    h = hourly(rc, v)                           # 장비 이름을 가리킨다
    if h == UNSET or tier != "container":
        return h
    # 컨테이너는 호스트 하나를 여럿이 나눠 쓴다 — 정원으로 나눈다.
    from dawn_core.infrapool import load_pool
    from dawn_core.paths import Paths

    cap = int((load_pool(Paths().root)[0] or {}).get("container_max", 0) or 0)
    return h / cap if cap else h


def settle(root: Path, u: Usage) -> Settlement:
    """원가를 낸다. **단가가 `미정` 이면 0 으로 만들지 않고 미정으로 센다.**"""
    rc = rates(root)
    krw = float(rc.get("usd_krw", 0) or 0)
    s = Settlement(order_id=u.order_id, currency=rc.get("currency", "KRW"))
    mrates = rc.get("model") or {}

    for model, m in sorted(u.by_model.items()):
        if m.get("local"):
            continue                            # 로컬은 토큰이 아니라 시간으로 잡는다
        r = mrates.get(model)
        if r is None or not krw:
            s.unpriced.append(f"{model} — 단가가 없는 모델 · "
                              f"in {m['in']:,} / out {m['out']:,} 토큰")
            continue
        cost = round((m["in"] / 1e6) * float(r["in_usd_1m"]) * krw
                     + (m["out"] / 1e6) * float(r["out_usd_1m"]) * krw)
        s.model_cost += cost
        s.lines.append({"what": model, "in": m["in"], "out": m["out"],
                        "krw": cost})

    # 로컬 모델 — **시간**으로 잡는다. 전용 GPU 는 쓰든 안 쓰든 같은 값이 들고,
    # 토큰당으로 매기면 같은 일의 원가가 처리량 편차(실측 3.5배)만큼 흔들린다.
    local_tok = sum(m["in"] + m["out"] for m in u.by_model.values()
                    if m.get("local"))
    if local_tok and not u.local_gpu_ms:
        # 로컬을 썼는데 점유 시간이 없다. **조용히 0 원이 되면 안 된다** —
        # 시간으로 잡는 방식으로 바꾼 뒤 생긴 구멍이라 명시적으로 막는다.
        s.unpriced.append(f"로컬 모델 — GPU 점유 시간 미측정 · {local_tok:,} 토큰")
    elif u.local_gpu_ms:
        hrs = u.local_gpu_ms / 3_600_000
        gh = hourly(rc, rc.get("gpu_source", ""))
        if gh == UNSET:
            s.unpriced.append(
                f"로컬 모델 — GPU 시간당 원가 미정 (구입가·소비전력·전기요금) · "
                f"점유 {hrs:.2f}h · {local_tok:,} 토큰")
        else:
            cost = round(float(gh) * hrs)
            s.model_cost += cost
            s.lines.append({"what": "로컬 모델 (GPU 점유)", "hours": round(hrs, 2),
                            "krw": cost})

    ih = _infra_hourly(rc, u.infra_tier)
    if u.infra_tier == "none":
        pass                                    # 아무것도 안 잡았다 — 진짜 0
    elif ih == UNSET:
        s.unpriced.append(f"인프라 {u.infra_tier} — 시간당 원가 미정 "
                          "(장비 구입가·소비전력·전기요금)")
    elif u.infra_hours:
        cost = round(float(ih) * u.infra_hours)
        s.infra_cost += cost
        s.lines.append({"what": f"인프라 {u.infra_tier}",
                        "hours": u.infra_hours, "krw": cost})
    else:
        s.unpriced.append(f"인프라 {u.infra_tier} — 점유 시간 미측정")
    return s


# ── 남긴다 ───────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verdicts(root: Path, u: Usage) -> list[dict[str, Any]]:
    """이 작업의 품질 판정. **없으면 없다고 쓴다** — 통과로 읽히면 안 된다."""
    d = Path(root) / "var" / "aoc" / "judge"
    out = []
    for t in u.traces:
        f = d / f"{t}.json"
        if f.is_file():
            out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def worklog(store, root: Path, order_id: int) -> int:
    """작업 일지 — **무엇을 했나.** 트레이스가 아니라 읽을 수 있는 문서로."""
    r = store.work_order(order_id)
    if r is None:
        raise ValueError(f"작업 지시 없음: {order_id}")
    u = usage(root, order_id)
    body = [
        f"- 작업 지시: #{order_id} {r['title']}",
        f"- 상태: {r['status']} · 출처 {r['origin']} · "
        f"{r['business'] or '내부 지원'} / {r['division']}",
        f"- 편성: {', '.join(u.agents) or '없음'}",
        f"- 실행: run {u.runs}건 (완료 {u.completed}) · 도구 호출 {u.tool_calls} · "
        f"게이트 차단 {u.blocked}",
        f"- 토큰: in {u.tokens_in:,} / out {u.tokens_out:,}",
        f"- 소요: {u.wall_ms / 1000:.1f}s",
        f"- 트레이스: {', '.join(u.traces[:6]) or '없음'}",
    ]
    return store.add_document(
        title=f"[작업일지] #{order_id} {r['title'][:40]}",
        body="\n".join(body) + "\n", author="system", org=r["division"],
        tags=f"worklog,order,wo{order_id}")


def report(store, root: Path, order_id: int) -> int:
    """완료 보고서 — **무엇이 나왔고 믿을 만한가.** 산출물 + 판정 + 원가."""
    r = store.work_order(order_id)
    if r is None:
        raise ValueError(f"작업 지시 없음: {order_id}")
    u = usage(root, order_id)
    s = settle(root, u)
    vs = _verdicts(root, u)

    lines = [f"# #{order_id} {r['title']}", "",
             f"{r['business'] or '내부 지원'} / {r['division']} · "
             f"{r['origin']} · 환경 {r['infra_tier']}", "",
             "## 실행", "",
             f"- 편성: {', '.join(u.agents) or '없음'}",
             f"- run {u.runs}건 (완료 {u.completed}) · 도구 {u.tool_calls} · "
             f"차단 {u.blocked}",
             f"- 토큰 in {u.tokens_in:,} / out {u.tokens_out:,} · "
             f"소요 {u.wall_ms / 1000:.1f}s", "",
             "## 품질 판정", ""]
    if not vs:
        lines.append("**판정 없음.** 통과한 것이 아니라 **판정하지 않은 것**이다.")
    for v in vs:
        lines.append(f"- {v.get('verdict', '?')} — 근거 {v.get('groundedness')} "
                     f"완결 {v.get('completeness')} 경로 {v.get('trajectory')} "
                     f"(judge: {v.get('judge_model', '?')})")
        for i in (v.get("issues") or [])[:3]:
            lines.append(f"  - {i}")

    lines += ["", "## 원가", "",
              "> **원가다. 청구액이 아니다** (단가는 `org/ratecard.yaml`)."]
    for ln in s.lines:
        lines.append(f"- {ln['what']}: {ln['krw']:,} {s.currency}")
    lines.append(f"- **합계 {s.total:,} {s.currency}**"
                 + ("" if s.complete else "  ← 값을 다 못 매겼다. **하한이다**"))
    for x in s.unpriced:
        lines.append(f"- 미정: {x}")

    return store.add_document(
        title=f"[완료보고] #{order_id} {r['title'][:40]}",
        body="\n".join(lines) + "\n", author="system", org=r["division"],
        tags=f"report,order,wo{order_id}")


def record_cost(store, root: Path, order_id: int) -> int:
    """원가를 경비로 남긴다. **미정이 있으면 남기지 않는다.**

    반쪽 금액을 장부에 올리면 그게 사실로 굳는다. 못 매긴 것은 보고서에
    미정으로 남고, 단가가 정해진 뒤에 다시 부르면 된다.
    """
    u = usage(root, order_id)
    s = settle(root, u)
    if not s.complete or s.total <= 0:
        return 0
    r = store.work_order(order_id)
    return store.add_expense(
        request_id=f"wo{order_id}", requester="system",
        requester_org=r["division"] if r else "", amount_krw=s.total,
        category="작업 원가", receipt_id="")


def close(store, root: Path, order_id: int, *, release: bool = True) -> dict[str, Any]:
    """작업 지시를 닫는다 — 일지 · 보고서 · 원가 · 반납 · 편성 회수.

    **순서가 있다.** 기록을 먼저 남기고 자원을 놓는다. 반대로 하면 반납 뒤에
    무엇을 썼는지 알 수 없다.
    """
    from dawn_core.crew import disband

    out: dict[str, Any] = {"order_id": order_id, "at": _now()}
    out["worklog_id"] = worklog(store, root, order_id)
    out["report_id"] = report(store, root, order_id)
    out["expense_id"] = record_cost(store, root, order_id)

    if release:
        from .provision import deprovision

        a = deprovision(store, root, order_id)
        out["released"] = a.to_dict() if a is not None else None
    out["disbanded"] = disband(root, order_id=order_id)
    store.set_work_order_status(order_id, "done")
    return out


__all__ = [
    "UNSET",
    "Settlement",
    "Usage",
    "close",
    "rates",
    "record_cost",
    "report",
    "settle",
    "usage",
    "worklog",
]
