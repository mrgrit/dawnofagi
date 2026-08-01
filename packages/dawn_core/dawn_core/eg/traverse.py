"""핵심 순회 — EG 가 실제로 답하는 질문들.

schema.json 의 `key_traversals` 를 실행 가능하게 만든 것. 이 세 가지가 P1 DoD 다.

    심각도   Asset.irreversibility × (Asset -CLASSIFIED_AS-> SecurityLevel.rank)
    게이트   Asset -CLASSIFIED_AS-> SecurityLevel <-APPLIES_TO- Policy.enforcement
    개입지점 OrgUnit -HAS_PERSONA-> Persona   ← 사람이 고치는 곳

여기에 P2 가 바로 쓰는 두 가지를 더한다:
    모델라우팅 OrgUnit -USES_MODEL-> ModelPolicy   (L3 관여 시 로컬 강제)
    자율화     OrgUnit -OPERATES_AT-> AutonomyLevel
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .store import EGStore, Node

# 비가역성 축 — 심각도 매트릭스의 한 축 (schema.json Asset.irreversibility)
IRREVERSIBILITY_WEIGHT = {"read": 0, "write": 1, "execute": 2, "irreversible": 3}

# 미분류 자산의 기본 등급. fail-safe — 등급이 없으면 최고로 본다.
MAX_SEC_RANK = 3

# enforcement 강도 — 여러 정책이 걸릴 때 가장 센 것이 이긴다
ENFORCEMENT_STRENGTH = {"log_only": 0, "warn": 1, "require_hitl": 2, "block": 3}

SEVERITY_BANDS = [
    (0, 1, "낮음", "🟢"),
    (2, 2, "보통", "🟡"),
    (3, 4, "높음", "🟠"),
    (5, 6, "최고", "🔴"),
]


def severity_band(score: int) -> tuple[str, str]:
    for lo, hi, label, icon in SEVERITY_BANDS:
        if lo <= score <= hi:
            return label, icon
    return ("최고", "🔴") if score > 6 else ("낮음", "🟢")


# ── 심각도 ──────────────────────────────────────────────────────────────


@dataclass
class Severity:
    asset_id: str
    asset_name: str
    irreversibility: str
    irr_weight: int
    sec_id: str | None
    sec_rank: int
    score: int
    label: str
    icon: str
    zone_id: str | None
    zone_room: str | None
    classified: bool = True

    @property
    def unclassified(self) -> bool:
        """보안등급이 없는 자산. **낮음이 아니라 미상이다.**"""
        return not self.classified

    def line(self) -> str:
        if self.unclassified:
            return (
                f"⚫미분류(?)  {self.asset_name:<24} "
                f"@ {self.zone_room or '존 미상'}  [{self.irreversibility} × 등급없음] "
                f"— 보수적으로 최고 등급 취급"
            )
        return (
            f"{self.icon}{self.label}({self.score})  {self.asset_name:<24} "
            f"@ {self.zone_room or '?'}  [{self.irreversibility} × {self.sec_id or '?'}]"
        )


def severity_of(store: EGStore, asset_id: str) -> Severity:
    """자산 하나의 심각도. 추측 없이 그래프에서만 나온다."""
    asset = store.node(asset_id)
    if asset is None or asset.type != "Asset":
        raise KeyError(f"Asset 아님: {asset_id}")

    irr = asset.prop("irreversibility", "read")
    irr_w = IRREVERSIBILITY_WEIGHT.get(irr, 0)

    secs = [n for n in store.out(asset_id, "CLASSIFIED_AS") if n.type == "SecurityLevel"]
    sec = secs[0] if secs else None

    zones = [n for n in store.out(asset_id, "LOCATED_IN") if n.type == "Zone"]
    zone = zones[0] if zones else None

    if sec is None:
        # **미분류를 rank 0 으로 읽으면 안 된다.** 등급이 없다는 것은 안전하다는 뜻이 아니라
        # 아직 아무도 판단하지 않았다는 뜻이다. fail-safe: 최고 등급으로 취급한다.
        # (bastion 런타임이 자동 생성한 asset-vm-* 등이 여기 해당 — 거버넌스 미편입 자산)
        rank = MAX_SEC_RANK
        score = irr_w + rank
        label, icon = severity_band(score)
        return Severity(
            asset_id=asset_id,
            asset_name=asset.name,
            irreversibility=irr,
            irr_weight=irr_w,
            sec_id=None,
            sec_rank=rank,
            score=score,
            label=label,
            icon=icon,
            zone_id=zone.id if zone else None,
            zone_room=zone.prop("pixel_room") if zone else None,
            classified=False,
        )

    rank = int(sec.prop("rank", 0))
    score = irr_w + rank
    label, icon = severity_band(score)
    return Severity(
        asset_id=asset_id,
        asset_name=asset.name,
        irreversibility=irr,
        irr_weight=irr_w,
        sec_id=sec.id,
        sec_rank=rank,
        score=score,
        label=label,
        icon=icon,
        zone_id=zone.id if zone else None,
        zone_room=zone.prop("pixel_room") if zone else None,
        classified=True,
    )


def all_severities(store: EGStore) -> list[Severity]:
    return sorted(
        (severity_of(store, a.id) for a in store.nodes(type="Asset")),
        key=lambda s: (-s.score, s.asset_id),
    )


# ── 게이트 ──────────────────────────────────────────────────────────────


@dataclass
class GateDecision:
    asset_id: str
    asset_name: str
    sec_id: str | None
    policies: list[Node] = field(default_factory=list)
    enforcements: set[str] = field(default_factory=set)
    classified: bool = True

    @property
    def strongest(self) -> str:
        if not self.enforcements:
            return "log_only"
        return max(self.enforcements, key=lambda e: ENFORCEMENT_STRENGTH.get(e, 0))

    @property
    def blocks(self) -> bool:
        return "block" in self.enforcements

    @property
    def requires_hitl(self) -> bool:
        return self.blocks or "require_hitl" in self.enforcements

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset_id,
            "security_level": self.sec_id,
            "policies": [p.id for p in self.policies],
            "enforcements": sorted(self.enforcements),
            "strongest": self.strongest,
            "requires_hitl": self.requires_hitl,
            "classified": self.classified,
        }


def gate_for(store: EGStore, asset_id: str) -> GateDecision:
    """이 자산을 건드릴 때 어떤 게이트가 걸리는가."""
    asset = store.node(asset_id)
    if asset is None or asset.type != "Asset":
        raise KeyError(f"Asset 아님: {asset_id}")

    secs = [n for n in store.out(asset_id, "CLASSIFIED_AS") if n.type == "SecurityLevel"]
    sec = secs[0] if secs else None

    if sec is None:
        # fail-safe: 미분류 자산은 최고 등급(rank 3)의 정책을 그대로 적용한다.
        top = max(
            (n for n in store.nodes(type="SecurityLevel")),
            key=lambda n: int(n.prop("rank", 0)),
            default=None,
        )
        policies = [n for n in store.inc(top.id, "APPLIES_TO") if n.type == "Policy"] if top else []
        return GateDecision(
            asset_id=asset_id,
            asset_name=asset.name,
            sec_id=None,
            policies=sorted(policies, key=lambda p: p.id),
            enforcements={p.prop("enforcement", "log_only") for p in policies} | {"require_hitl"},
            classified=False,
        )

    policies = [n for n in store.inc(sec.id, "APPLIES_TO") if n.type == "Policy"]
    return GateDecision(
        asset_id=asset_id,
        asset_name=asset.name,
        sec_id=sec.id,
        policies=sorted(policies, key=lambda p: p.id),
        enforcements={p.prop("enforcement", "log_only") for p in policies},
    )


# ── 개입 지점 ────────────────────────────────────────────────────────────


@dataclass
class OrgProfile:
    """조직 하나의 EG 프로파일 — P2 워커가 착수 시 조회하는 것 전부."""

    org_id: str
    org_name: str
    mission: str
    personas: list[Node] = field(default_factory=list)
    policies: list[Node] = field(default_factory=list)  # 페르소나 경유
    models: list[Node] = field(default_factory=list)
    autonomy: Node | None = None
    parent: Node | None = None
    assets: list[Node] = field(default_factory=list)

    @property
    def autonomy_level(self) -> int:
        return int(self.autonomy.prop("level", 1)) if self.autonomy else 1

    @property
    def is_local_only(self) -> bool:
        """이 조직의 모델이 전부 로컬인가 (pol:l3-local-only 대응)."""
        return bool(self.models) and all(m.prop("cost_tier") == "local" for m in self.models)

    def to_dict(self) -> dict[str, Any]:
        """P2 워커가 착수 시 시스템 프롬프트에 주입할 것 **전부**.

        페르소나·정책의 id 만 담으면 안 된다 — 사람이 principles 를 고쳐도
        출력이 그대로여서 "개입이 반영됐는지" 확인할 수 없다.
        에이전트가 실제로 읽는 본문을 그대로 싣는다.
        """
        return {
            "org": self.org_id,
            "name": self.org_name,
            "mission": self.mission,
            "parent": self.parent.id if self.parent else None,
            "personas": [
                {
                    "id": p.id,
                    "role": p.prop("role"),
                    "tone": p.prop("tone"),
                    "principles": p.prop("principles") or [],
                    "prohibited": p.prop("prohibited") or [],
                    "escalation_rule": p.prop("escalation_rule"),
                }
                for p in self.personas
            ],
            "policies": [
                {
                    "id": p.id,
                    "statement": p.prop("statement"),
                    "rule": p.prop("rule"),
                    "enforcement": p.prop("enforcement"),
                    "severity": p.prop("severity"),
                }
                for p in self.policies
            ],
            "models": [
                {"id": m.id, "model": m.prop("model"), "tier": m.prop("cost_tier")}
                for m in self.models
            ],
            "autonomy": (
                {
                    "id": self.autonomy.id,
                    "level": self.autonomy_level,
                    "label": self.autonomy.prop("label"),
                    "gate_rule": self.autonomy.prop("gate_rule"),
                }
                if self.autonomy
                else None
            ),
            "assets_owned": [
                {"id": a.id, "name": a.name, "irreversibility": a.prop("irreversibility")}
                for a in self.assets
            ],
            "local_only": self.is_local_only,
        }


def org_profile(store: EGStore, org_id: str) -> OrgProfile:
    """조직 → 페르소나 → 정책 체인. **사람의 개입 지점을 찾는 순회.**

    페르소나가 직접 안 걸린 조직은 상위 조직(PART_OF)을 타고 올라가 상속한다 —
    전사 org:dawn 의 company-default 가 최종 폴백이다.
    """
    org = store.node(org_id)
    if org is None or org.type != "OrgUnit":
        raise KeyError(f"OrgUnit 아님: {org_id}")

    personas = [n for n in store.out(org_id, "HAS_PERSONA") if n.type == "Persona"]

    # 상속: 자기 페르소나가 없으면 조직 계통을 타고 올라간다
    if not personas:
        cur, hops = org_id, 0
        while hops < 6:
            parents = [n for n in store.out(cur, "PART_OF") if n.type == "OrgUnit"]
            if not parents:
                break
            cur = parents[0].id
            hops += 1
            inherited = [n for n in store.out(cur, "HAS_PERSONA") if n.type == "Persona"]
            if inherited:
                personas = inherited
                break

    policies: dict[str, Node] = {}
    for p in personas:
        for pol in store.out(p.id, "GOVERNED_BY"):
            if pol.type == "Policy":
                policies[pol.id] = pol

    parents = [n for n in store.out(org_id, "PART_OF") if n.type == "OrgUnit"]
    autos = [n for n in store.out(org_id, "OPERATES_AT") if n.type == "AutonomyLevel"]

    return OrgProfile(
        org_id=org_id,
        org_name=org.name,
        mission=org.prop("mission", ""),
        personas=sorted(personas, key=lambda n: n.id),
        policies=sorted(policies.values(), key=lambda n: n.id),
        models=[n for n in store.out(org_id, "USES_MODEL") if n.type == "ModelPolicy"],
        autonomy=autos[0] if autos else None,
        parent=parents[0] if parents else None,
        assets=[n for n in store.inc(org_id, "OWNED_BY") if n.type == "Asset"],
    )


# ── 모델 라우팅 (P2 가 쓴다) ─────────────────────────────────────────────


def model_for_org(store: EGStore, org_id: str, *, touches_l3: bool = False) -> dict[str, Any]:
    """이 조직 에이전트가 어떤 모델을 쓰는가. L3 관여 시 로컬 강제(pol:l3-local-only)."""
    prof = org_profile(store, org_id)
    models = prof.models
    local = [m for m in models if m.prop("cost_tier") == "local"]

    if touches_l3:
        chosen = local[0] if local else None
        return {
            "org": org_id,
            "touches_l3": True,
            "forced_local": True,
            "model": chosen.prop("model") if chosen else None,
            "model_id": chosen.id if chosen else None,
            "policy": "pol:l3-local-only",
            "blocked": chosen is None,
            "reason": (
                "L3 자산 관여 — 로컬 모델 전용"
                if chosen
                else "L3 자산 관여인데 이 조직에 로컬 모델이 배정돼 있지 않다 → 차단"
            ),
        }

    chosen = models[0] if models else None
    return {
        "org": org_id,
        "touches_l3": False,
        "forced_local": False,
        "model": chosen.prop("model") if chosen else None,
        "model_id": chosen.id if chosen else None,
        "blocked": False,
        "reason": "EG USES_MODEL 배정" if chosen else "배정된 ModelPolicy 없음",
    }


# ── 자율화 위반 (P3 탐지가 쓴다) ─────────────────────────────────────────


def autonomy_violations(store: EGStore) -> list[dict[str, Any]]:
    """조직의 자율화 등급이 자기가 소유한 자산의 민감도에 못 미치는 경우.

    pol:autonomy-gate — `org.autonomy_level < asset.sec_rank => require_hitl`
    위반이 아니라 **HITL 이 필요한 조합**을 찾아내는 것이다.
    """
    out: list[dict[str, Any]] = []
    for org in store.nodes(type="OrgUnit"):
        prof = org_profile(store, org.id)
        if prof.autonomy is None:
            continue
        for asset in prof.assets:
            sev = severity_of(store, asset.id)
            if prof.autonomy_level < sev.sec_rank:
                out.append(
                    {
                        "org": org.id,
                        "org_name": org.name,
                        "autonomy": prof.autonomy.id,
                        "autonomy_level": prof.autonomy_level,
                        "asset": asset.id,
                        "asset_name": asset.name,
                        "sec_rank": sev.sec_rank,
                        "requires": "require_hitl",
                        "policy": "pol:autonomy-gate",
                    }
                )
    return sorted(out, key=lambda d: (d["org"], d["asset"]))
