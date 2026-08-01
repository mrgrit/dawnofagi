"""정책 규칙 평가기 — EG Policy.rule 을 실제로 판정한다.

`02_policies.json` 의 각 정책은 `rule` 에 **판정 가능한 조건식**을 갖고 있다:

    pol:l3-local-only    asset.sec_rank == 3 AND model.cost_tier != 'local' => block
    pol:pii-hr-fin       asset.owner_org in ['org:hr','org:fin'] AND asset.kind=='data' => require_hitl
    pol:autonomy-gate    org.autonomy_level < asset.sec_rank => require_hitl

`enforcement` 를 조건 무시하고 그대로 적용하면 **모든 행동이 HITL 로 막힌다** —
자산을 스치기만 해도 그 등급에 걸린 정책 전부가 발동하기 때문이다.
그러면 에이전트는 아무것도 못 하고, 사람은 승인 피로로 다 눌러 버린다.
게이트가 무의미해지는 전형적인 실패다.

그래서 여기서 조건을 실제로 판정한다. 판정에 필요한 사실이 없으면
**보수적으로 발동시킨다** (모르면 막는 쪽).

지원하는 술어는 시드의 11개 정책이 실제로 쓰는 것에 한정한다.
새 정책이 새 술어를 쓰면 `UNKNOWN_PREDICATE` 로 표시되고 보수 판정된다 —
조용히 무시되지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ALWAYS = "always"


@dataclass
class Facts:
    """정책 판정에 쓰이는 사실. 없는 값은 None — 모르면 보수적으로 간다."""

    # 행동
    action_destructive: bool = False
    action_high_risk: bool = False
    action_offensive: bool = False
    action_financial: bool = False
    action_amount_krw: float | None = None
    output_customer_facing: bool = False

    # 자산
    asset_id: str = ""
    asset_kind: str = ""
    asset_irreversibility: str = "read"
    asset_sec_rank: int = 0
    asset_owner_org: str = ""
    asset_zone: str = ""
    asset_classified: bool = True

    # 주체
    org_id: str = ""
    org_autonomy_level: int = 1
    task_tenant: str = "0"
    asset_tenant: str = "0"

    # 모델
    model_cost_tier: str = ""

    # 임계
    amount_threshold_krw: float | None = None

    def get(self, path: str) -> Any:
        return {
            "action.destructive": self.action_destructive,
            "action.high_risk": self.action_high_risk,
            "action.offensive": self.action_offensive,
            "action.financial": self.action_financial,
            "amount": self.action_amount_krw,
            "output.customer_facing": self.output_customer_facing,
            "asset.kind": self.asset_kind,
            "asset.irreversibility": self.asset_irreversibility,
            "asset.sec_rank": self.asset_sec_rank,
            "asset.owner_org": self.asset_owner_org,
            "asset.zone": self.asset_zone,
            "org.autonomy_level": self.org_autonomy_level,
            "task.org": self.org_id,
            "task.tenant": self.task_tenant,
            "asset.tenant": self.asset_tenant,
            "model.cost_tier": self.model_cost_tier,
            "threshold": self.amount_threshold_krw,
        }.get(path, _MISSING)


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"


_MISSING = _Missing()


@dataclass
class Verdict:
    policy_id: str
    fired: bool
    enforcement: str  # 발동 시 적용될 enforcement
    reason: str
    unknown: bool = False  # 술어를 못 읽어 보수 판정했나

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ── 조건식 파서 ──────────────────────────────────────────────────────────
# 시드의 rule 문법: "<조건> => <효과>", 조건은 AND 로 이어진 비교식.

_COMPARE = re.compile(r"^\s*([\w.]+)\s*(==|!=|>=|<=|>|<|\bin\b|\bnot in\b)\s*(.+?)\s*$")
_LIST = re.compile(r"^\[(.*)\]$")


def _literal(raw: str) -> Any:
    raw = raw.strip()
    m = _LIST.match(raw)
    if m:
        inner = m.group(1)
        return [_literal(x) for x in inner.split(",") if x.strip()]
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')):
        return raw[1:-1]
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw  # 심볼 (threshold 등) — Facts.get 으로 다시 푼다


def _resolve(token: Any, facts: Facts) -> Any:
    """사실 경로면 값으로, 아니면 리터럴 그대로.

    점이 들어간 토큰(`asset.foo`)은 **사실 경로로 의도된 것**이다.
    Facts 에 없으면 리터럴 문자열로 넘기지 않고 _MISSING 을 돌려
    '판정 불가 → 보수 적용' 으로 가게 한다. 그러지 않으면 오타 난 술어가
    조용히 거짓이 되어 정책이 죽는다.
    """
    if isinstance(token, str):
        v = facts.get(token)
        if v is not _MISSING:
            return v
        if "." in token and not token.replace(".", "").isdigit():
            return _MISSING
    return token


def _compare(lhs: Any, op: str, rhs: Any) -> bool | None:
    """비교. 판정 불가면 None."""
    if lhs is _MISSING or rhs is _MISSING or lhs is None or rhs is None:
        return None
    try:
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op == "in":
            return lhs in rhs if isinstance(rhs, (list, tuple, set, str)) else None
        if op == "not in":
            return lhs not in rhs if isinstance(rhs, (list, tuple, set, str)) else None
        if op == ">":
            return float(lhs) > float(rhs)
        if op == "<":
            return float(lhs) < float(rhs)
        if op == ">=":
            return float(lhs) >= float(rhs)
        if op == "<=":
            return float(lhs) <= float(rhs)
    except (TypeError, ValueError):
        return None
    return None


def evaluate_rule(rule: str, facts: Facts) -> tuple[bool, bool, str]:
    """조건식을 판정한다. → (발동?, 판정불가?, 설명)"""
    if not rule:
        return True, True, "rule 이 비어 있음 — 보수적으로 발동"

    cond = rule.split("=>", 1)[0].strip()
    if cond.lower() == ALWAYS:
        return True, False, "always"

    clauses = re.split(r"\s+AND\s+", cond)
    unknown = False
    details: list[str] = []
    for clause in clauses:
        m = _COMPARE.match(clause)
        if not m:
            # 비교 연산자가 없는 **불리언 단독 술어** (예: action.destructive)
            bare = facts.get(clause.strip())
            if bare is not _MISSING:
                if not bare:
                    return False, False, f"{clause.strip()} → 거짓"
                details.append(f"{clause.strip()} → 참")
                continue
            unknown = True
            details.append(f"미지원 술어: {clause.strip()}")
            continue
        lhs_raw, op, rhs_raw = m.group(1), m.group(2).strip(), m.group(3)
        lhs = _resolve(lhs_raw, facts)
        rhs = _resolve(_literal(rhs_raw), facts)
        got = _compare(lhs, op, rhs)
        if got is None:
            unknown = True
            details.append(f"판정 불가: {clause.strip()}")
            continue
        if not got:
            return False, False, f"{clause.strip()} → 거짓"
        details.append(f"{clause.strip()} → 참")

    if unknown:
        # 모르면 발동시킨다 (fail-safe)
        return True, True, "; ".join(details)
    return True, False, "; ".join(details)


def evaluate(policies, facts: Facts) -> list[Verdict]:
    """정책 노드 목록을 판정한다."""
    out: list[Verdict] = []
    for p in policies:
        rule = p.prop("rule", "") or ""
        fired, unknown, why = evaluate_rule(rule, facts)
        out.append(
            Verdict(
                policy_id=p.id,
                fired=fired,
                enforcement=p.prop("enforcement", "log_only"),
                reason=why,
                unknown=unknown,
            )
        )
    return out


def fired(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.fired]
