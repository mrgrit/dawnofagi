"""통제 평면 ↔ EG 브리지.

두 개의 손잡이가 **서로 어긋나지 않는지** 기계적으로 확인한다.

    통제 평면(P0)   문서 4계층 + gate.yaml     "이 에이전트는 이렇게 행동한다"
    EG(P1)          그래프 거버넌스 8종        "이 조직은 이런 규정·페르소나·모델이다"

둘은 경쟁이 아니라 두 개의 손잡이다. 하지만 어긋나면 조용히 틀린다:
  · 팀 gate 는 A1 인데 EG 는 그 조직을 A0 로 운영한다면 — 어느 쪽이 참인가?
  · gate 가 `model.policy: from_eg` 인데 EG 에 USES_MODEL 이 없다면 — 무엇을 쓰나?
  · gate 는 클라우드 허용인데 EG 는 그 조직에 로컬 모델만 배정했다면 — L3 가 샌다.

그래서 브리지가 대조한다. **어긋남은 경고가 아니라 오류다** — CI 가 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..control_plane import compile_all
from ..gate import AUTONOMY_ORDER
from ..registry import Registry
from .store import EGStore
from .traverse import model_for_org, org_profile


@dataclass
class BridgeIssue:
    severity: str  # error | warning
    where: str
    message: str
    fix: str = ""


@dataclass
class BridgeReport:
    issues: list[BridgeIssue] = field(default_factory=list)
    mapped: dict[str, str] = field(default_factory=dict)  # team_id → eg_org
    checked: int = 0

    @property
    def errors(self) -> list[BridgeIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[BridgeIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "mapped": self.mapped,
            "errors": [{"where": i.where, "message": i.message, "fix": i.fix} for i in self.errors],
            "warnings": [
                {"where": i.where, "message": i.message, "fix": i.fix} for i in self.warnings
            ],
        }


def check(registry: Registry, store: EGStore) -> BridgeReport:
    """레지스트리(통제 평면)와 EG 를 대조한다."""
    rep = BridgeReport()
    compiled, failed = compile_all(registry)

    for aid, msg in failed.items():
        rep.issues.append(
            BridgeIssue(
                "error",
                f"agent:{aid}",
                "통제 평면 컴파일 실패 — EG 대조 이전 문제",
                msg.splitlines()[0] if msg else "",
            )
        )

    # ── 1. 모든 팀·본부가 EG OrgUnit 에 매핑되는가 ──────────────────────
    for holder, kind in [(registry.teams, "team"), (registry.divisions, "division")]:
        for oid, unit in holder.items():
            eg_org = unit.data.get("eg_org")
            if not eg_org:
                rep.issues.append(
                    BridgeIssue(
                        "error",
                        f"{kind}:{oid}",
                        "eg_org 매핑이 없다 — 이 조직은 EG 의 규정·페르소나를 못 받는다",
                        f"{unit.source.name} 에 `eg_org: org:<id>` 를 추가하라",
                    )
                )
                continue
            if store.node(eg_org) is None:
                rep.issues.append(
                    BridgeIssue(
                        "error",
                        f"{kind}:{oid}",
                        f"eg_org={eg_org} 노드가 EG 에 없다",
                        "eg/seed/01_foundation.json 에 OrgUnit 을 추가하고 make eg-load",
                    )
                )
                continue
            if kind == "team":
                rep.mapped[oid] = eg_org

    # ── 2. 에이전트별 대조 ──────────────────────────────────────────────
    for aid, c in compiled.items():
        team = registry.teams[c.team_id]
        eg_org = team.data.get("eg_org")
        if not eg_org or store.node(eg_org) is None:
            continue
        rep.checked += 1
        prof = org_profile(store, eg_org)
        where = f"agent:{aid}"

        # 2-1. 자율화 — gate 가 EG 보다 높으면 안 된다
        if prof.autonomy is not None:
            eg_level = prof.autonomy.id.split(":")[-1]
            if AUTONOMY_ORDER.index(c.gate.autonomy) > AUTONOMY_ORDER.index(eg_level):
                rep.issues.append(
                    BridgeIssue(
                        "error",
                        where,
                        f"자율화 불일치 — gate={c.gate.autonomy} 인데 "
                        f"EG({eg_org})는 {eg_level} 로 운영한다. EG 가 더 보수적이다",
                        f"팀 gate.yaml 의 autonomy 를 {eg_level} 로 낮추거나, "
                        f"EG 의 OPERATES_AT 엣지를 승급하라(KPI 충족 시)",
                    )
                )

        # 2-2. 페르소나 — EG 페르소나가 있는데 매니페스트가 다른 걸 가리키면
        eg_personas = {p.id.split(":", 1)[-1] for p in prof.personas}
        declared = registry.agents[aid].data.get("persona")
        if eg_personas and declared not in eg_personas:
            rep.issues.append(
                BridgeIssue(
                    "warning",
                    where,
                    f"페르소나 불일치 — 매니페스트 '{declared}' vs "
                    f"EG({eg_org}) {sorted(eg_personas)}",
                    "agent.yaml 의 persona 를 EG 값에 맞추거나 EG 의 HAS_PERSONA 를 조정하라",
                )
            )

        # 2-3. 모델 정책 — gate 가 from_eg 면 EG 에 배정이 있어야 한다
        if c.gate.model_policy == "from_eg" and not prof.models:
            rep.issues.append(
                BridgeIssue(
                    "error",
                    where,
                    f"gate 는 model.policy=from_eg 인데 EG({eg_org})에 USES_MODEL 이 없다 "
                    f"— 무슨 모델을 쓸지 결정할 수 없다",
                    "eg/seed/01_foundation.json 에 USES_MODEL 엣지를 추가하라",
                )
            )

        # 2-4. L3 로컬 강제 — EG 가 로컬 전용인데 gate 가 클라우드를 허용하면 유출
        if prof.is_local_only and c.gate.model_policy not in ("local_only", "pinned"):
            rep.issues.append(
                BridgeIssue(
                    "error",
                    where,
                    f"L3 유출 위험 — EG({eg_org})는 로컬 모델만 배정했는데 "
                    f"gate 는 model.policy={c.gate.model_policy} 로 클라우드를 허용한다",
                    "팀 gate.yaml 을 model.policy: local_only 로 바꿔라 (pol:l3-local-only)",
                )
            )

        # 2-4b. 반대 방향 — gate 는 로컬 전용인데 EG 가 클라우드 모델만 배정했다.
        # 두 손잡이가 정반대를 가리키는 상태다. 어느 쪽을 믿을지 알 수 없으므로 오류다.
        if c.gate.model_policy == "local_only" and prof.models and not prof.is_local_only:
            cloud = [m for m in prof.models if m.prop("cost_tier") != "local"]
            has_local = any(m.prop("cost_tier") == "local" for m in prof.models)
            if not has_local:
                rep.issues.append(
                    BridgeIssue(
                        "error",
                        where,
                        f"모델 정책 정면 충돌 — gate 는 local_only 인데 "
                        f"EG({eg_org})는 클라우드 모델만 배정했다 "
                        f"({', '.join(m.prop('model') for m in cloud)}). "
                        f"이 에이전트는 쓸 수 있는 모델이 없다",
                        f"eg/seed/01_foundation.json 에 "
                        f"`USES_MODEL {eg_org} → model:openlocal` 을 추가하거나, "
                        f"팀 gate.yaml 의 model.policy 를 완화하라",
                    )
                )

        # 2-5. L3 자산을 소유한 조직인데 gate 가 로컬 강제를 안 하면
        l3_assets = [
            a
            for a in prof.assets
            if any(s.prop("rank") == 3 for s in store.out(a.id, "CLASSIFIED_AS"))
        ]
        if l3_assets and not c.gate.forces_local_model("l3_data"):
            rep.issues.append(
                BridgeIssue(
                    "warning",
                    where,
                    f"소유 자산에 L3 가 있는데({', '.join(a.id for a in l3_assets[:3])}) "
                    f"gate 가 l3_data 로컬 강제를 선언하지 않았다",
                    "gate.yaml 의 model.force_local_when 에 l3_data 를 추가하라",
                )
            )

        # 2-6. HITL — EG 정책이 require_hitl/block 인데 gate 조건이 비면
        needs = {p.prop("enforcement") for p in prof.policies}
        if ("require_hitl" in needs or "block" in needs) and not c.gate.hitl_require_on:
            rep.issues.append(
                BridgeIssue(
                    "error",
                    where,
                    f"EG 정책이 HITL 을 요구하는데({', '.join(sorted(needs))}) "
                    f"gate 의 hitl.require_on 이 비어 있다",
                    "팀 gate.yaml 에 hitl.require_on 을 선언하라",
                )
            )

    # ── 3. EG 쪽 고아 — 에이전트가 붙지 않은 조직 (정보) ────────────────
    mapped_orgs = set(rep.mapped.values())
    for org in store.nodes(type="OrgUnit"):
        prof = org_profile(store, org.id)
        if org.id not in mapped_orgs and prof.autonomy is not None:
            rep.issues.append(
                BridgeIssue(
                    "warning",
                    f"eg:{org.id}",
                    f"EG 조직 '{org.name}' 이 자율화 등급을 갖지만 "
                    f"레지스트리에 대응 팀·에이전트가 없다 (휴면)",
                    "org/divisions/ 에 팀을 만들거나, 의도된 휴면이면 무시하라",
                )
            )

    return rep


def format_report(rep: BridgeReport) -> str:
    lines = ["", "  통제 평면 ↔ EG 브리지", "  " + "─" * 58]
    lines.append(f"  매핑된 팀      {len(rep.mapped)}")
    lines.append(f"  대조한 에이전트 {rep.checked}")
    lines.append("  " + "─" * 58)
    if rep.errors:
        lines.append(f"\n  오류 {len(rep.errors)}건 — 두 손잡이가 어긋난다")
        for i in rep.errors:
            lines.append(f"    ✘ [{i.where}] {i.message}")
            if i.fix:
                lines.append(f"        → {i.fix}")
    if rep.warnings:
        lines.append(f"\n  경고 {len(rep.warnings)}건")
        for i in rep.warnings:
            lines.append(f"    ! [{i.where}] {i.message}")
    lines.append("")
    lines.append("  ✔ 정합" if rep.ok else "  ✘ 불일치 — 고치기 전에는 에이전트를 기동하지 마라")
    lines.append("")
    return "\n".join(lines)


def routing_table(registry: Registry, store: EGStore) -> list[dict[str, Any]]:
    """에이전트별 실효 모델 라우팅 — P2 가 그대로 쓴다."""
    rows: list[dict[str, Any]] = []
    compiled, _ = compile_all(registry)
    for aid, c in sorted(compiled.items()):
        eg_org = registry.teams[c.team_id].data.get("eg_org")
        row: dict[str, Any] = {
            "agent": aid,
            "team": c.team_id,
            "eg_org": eg_org,
            "gate_policy": c.gate.model_policy,
            "model_hint": registry.agents[aid].data.get("model_hint"),
        }
        if eg_org and store.node(eg_org) is not None:
            normal = model_for_org(store, eg_org, touches_l3=False)
            l3 = model_for_org(store, eg_org, touches_l3=True)
            row["model_normal"] = normal["model"]
            row["model_on_l3"] = l3["model"]
            row["l3_blocked"] = l3["blocked"]
        rows.append(row)
    return rows
