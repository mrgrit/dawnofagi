"""행동 게이트 엔진 — 이 도구를 지금 실행해도 되는가.

세 개의 입력이 하나의 판정으로 합쳐진다 (P2 지시문 §2):

    ① 통제 평면 게이트   gate.yaml 병합 결과 (도구 allow/deny · 자율화 · HITL 조건 · 예산)
    ② 스킬 위험도        skill_preview → risk(LOW/MED/HIGH) · destructive
    ③ EG 순회            Asset → SecurityLevel → APPLIES_TO → Policy.enforcement

판정: block > require_hitl > warn > log_only  (가장 센 것이 이긴다)

**어느 하나라도 막으면 막힌다.** 세 입력은 서로를 완화하지 못한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dawn_core.eg.traverse import (
    ENFORCEMENT_STRENGTH,
    IRREVERSIBILITY_WEIGHT,
    gate_for,
    severity_band,
    severity_of,
)
from dawn_core.gate import AUTONOMY_ORDER, Gate

from .policy import Facts, fired
from .policy import evaluate as evaluate_policies

# 행동의 비가역성은 **카탈로그가 선언한다** (`org/tools.yaml` 의 `action:`).
# 심각도는 행동 × 자산등급이지 자산의 선언된 비가역성 × 자산등급이 아니다 —
# 읽기는 읽기다. 폴백(위험도에서 추정)은 `skills.action_of` 에 있다.
from .skills import RISK_TO_ACTION as ACTION_IRREVERSIBILITY  # noqa: F401  (하위호환)

DECISIONS = ["log_only", "warn", "require_hitl", "block"]


def _strongest(decisions: list[str]) -> str:
    return max(decisions, key=lambda d: ENFORCEMENT_STRENGTH.get(d, 0)) if decisions else "log_only"


@dataclass
class GateDecision:
    """한 번의 도구 실행에 대한 판정."""

    skill: str
    decision: str  # log_only | warn | require_hitl | block
    reasons: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # 어느 입력이 뭘 말했나
    severity: int = 0
    severity_label: str = "낮음"
    assets: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    touches_l3: bool = False
    unclassified_assets: list[str] = field(default_factory=list)
    policy_verdicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.decision == "block"

    @property
    def needs_hitl(self) -> bool:
        return self.decision in ("block", "require_hitl")

    @property
    def allowed_without_human(self) -> bool:
        return self.decision in ("log_only", "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "decision": self.decision,
            "reasons": self.reasons,
            "sources": self.sources,
            "severity": self.severity,
            "severity_label": self.severity_label,
            "assets": self.assets,
            "policies": self.policies,
            "touches_l3": self.touches_l3,
            "unclassified_assets": self.unclassified_assets,
            "policy_verdicts": self.policy_verdicts,
        }

    def line(self) -> str:
        icon = {"block": "⛔", "require_hitl": "✋", "warn": "⚠", "log_only": "✔"}[self.decision]
        return (
            f"{icon} {self.decision:<12} {self.skill}  ({'; '.join(self.reasons) or '제약 없음'})"
        )


class ActionGate:
    """통제 평면 + 스킬 위험도 + EG 를 결합한 판정기."""

    def __init__(
        self,
        gate: Gate,
        eg_store=None,
        *,
        autonomy: str = "A1",
        org_id: str = "",
        model_cost_tier: str = "",
        catalog=None,
    ) -> None:
        self.gate = gate
        self.eg = eg_store
        self.autonomy = autonomy
        self.org_id = org_id
        self.model_cost_tier = model_cost_tier
        self.catalog = catalog          # dawn_core.gate.ToolCatalog — 루프 계측 선언원

    # ── 판정 ────────────────────────────────────────────────────────────
    def evaluate(self, preview, *, declared_tools: list[str] | None = None) -> GateDecision:
        """skill_preview 결과를 받아 실행 가능 여부를 판정한다."""
        decisions: list[str] = ["log_only"]
        reasons: list[str] = []
        sources: dict[str, str] = {}
        d = GateDecision(skill=preview.skill, decision="log_only")

        # ① 통제 평면 — 도구 경계
        if not self.gate.permits(preview.skill):
            decisions.append("block")
            reasons.append("통제 평면이 차단한 도구 (gate.yaml)")
            sources["control_plane"] = "block"
        elif declared_tools is not None and preview.skill not in declared_tools:
            decisions.append("block")
            reasons.append("에이전트 매니페스트에 없는 도구 — 최소권한 위반")
            sources["manifest"] = "block"
        else:
            sources["control_plane"] = "allow"

        # ② 스킬 위험도
        if preview.destructive:
            decisions.append("require_hitl")
            reasons.append("비가역 스킬 — 사전 인간 승인 필요")
            sources["skill_risk"] = "require_hitl"
            if self.gate.requires_hitl("destructive", "irreversible"):
                sources["skill_risk"] = "require_hitl(gate 조건 일치)"
        elif preview.risk == "HIGH":
            decisions.append("require_hitl")
            reasons.append("고위험 스킬")
            sources["skill_risk"] = "require_hitl"
        elif preview.risk == "MED":
            decisions.append("warn")
            sources["skill_risk"] = "warn"
        else:
            sources["skill_risk"] = "log_only"

        if not preview.implemented and preview.skill in (
            "sec.container_stop",
            "sec.firewall_change",
            "sec.credential_revoke",
            "sec.isolate",
            "sys.deploy",
            "fin.ledger_write",
            "pay.execute",
        ):
            decisions.append("block")
            reasons.append("실행부 미구현 — P2 에서 이 행동은 수행하지 않는다")
            sources["implementation"] = "block"

        # ③ EG 순회 — 자산 → 등급 → 정책 (rule 을 실제로 판정한다)
        d.assets = list(preview.touches_assets)
        action_irr = preview.action or "write"
        action_weight = IRREVERSIBILITY_WEIGHT[action_irr]

        if self.eg is not None and d.assets:
            worst = 0
            pol_ids: set[str] = set()
            eg_decisions: list[str] = []
            for aid in d.assets:
                node = self.eg.node(aid)
                if node is None:
                    continue
                sev = severity_of(self.eg, aid)
                # 심각도 = **행동의** 비가역성 + 자산 보안등급
                score = action_weight + sev.sec_rank
                worst = max(worst, score)
                if not sev.classified:
                    d.unclassified_assets.append(aid)
                    eg_decisions.append("require_hitl")
                    reasons.append(f"미분류 자산 {aid} — 최고 등급으로 취급")
                if sev.sec_rank >= 3:
                    d.touches_l3 = True

                facts = Facts(
                    action_destructive=preview.destructive,
                    action_high_risk=preview.risk == "HIGH",
                    action_financial=preview.skill.startswith(("fin.", "pay.")),
                    asset_id=aid,
                    asset_kind=node.prop("kind", ""),
                    asset_irreversibility=action_irr,
                    asset_sec_rank=sev.sec_rank,
                    asset_owner_org=node.prop("owner_org", ""),
                    asset_zone=sev.zone_id or "",
                    asset_classified=sev.classified,
                    org_id=self.org_id,
                    org_autonomy_level=AUTONOMY_ORDER.index(self.autonomy),
                    model_cost_tier=self.model_cost_tier,
                    amount_threshold_krw=self.gate.amount_threshold_krw,
                )
                g = gate_for(self.eg, aid)
                verdicts = evaluate_policies(g.policies, facts)
                for v in fired(verdicts):
                    pol_ids.add(v.policy_id)
                    eg_decisions.append(v.enforcement)
                    if v.enforcement in ("block", "require_hitl"):
                        reasons.append(f"{v.policy_id} 발동 ({v.reason[:60]})")
                    if v.unknown:
                        reasons.append(f"{v.policy_id}: 판정 불가 → 보수 적용")
                d.policy_verdicts.extend(v.to_dict() for v in verdicts)

            d.severity = worst
            d.severity_label = severity_band(worst)[0]
            d.policies = sorted(pol_ids)
            if eg_decisions:
                eg_strongest = _strongest(eg_decisions)
                decisions.append(eg_strongest)
                sources["eg"] = eg_strongest

        # L3 + 자율화 — 등급 위 행동만. 조회까지 막지 않는다.
        if d.touches_l3 and (preview.destructive or preview.risk == "HIGH"):
            if self.gate.requires_hitl("l3_data"):
                decisions.append("require_hitl")
                reasons.append("L3 자산에 대한 변경·고위험 행동")
                sources["l3"] = "require_hitl"

        d.decision = _strongest(decisions)

        # 루프 계측(①eg_search ④eg_record)은 **막을 수 없다.** 여기가 HITL 로 걸리면
        # 에이전트가 자기 작업을 기록하지 못해 "④ 를 마쳐야 완료"라는 불변식이 깨진다.
        # 자산·심각도·정책 판정은 **그대로 남긴다** — 무엇을 만졌는지는 기록돼야 한다.
        # 우회 대상은 카탈로그가 선언하고(`loop_instrumentation`), 비가역·고위험
        # 도구에는 붙일 수 없다(ToolCatalog.load 가 거부한다).
        if self.catalog is not None and self.catalog.loop_instrumentation(preview.skill):
            if d.decision != "log_only":
                reasons.append(
                    f"루프 계측 — 판정 {d.decision} 이지만 막지 않는다 "
                    "(막으면 에이전트가 자기 작업을 기록하지 못한다)"
                )
                sources["loop_instrumentation"] = "log_only(강제)"
            d.decision = "log_only"

        d.reasons = reasons
        d.sources = sources
        return d

    # ── 모델 라우팅 연동 ────────────────────────────────────────────────
    def requires_local_model(self, decision: GateDecision) -> bool:
        """이 행동이 L3 를 만지면 로컬 모델이 강제된다 (pol:l3-local-only)."""
        return decision.touches_l3 or self.gate.forces_local_model("l3_data")
