"""통제 평면 컴파일러 — 4계층 문서 + 게이트 → 에이전트 1명의 실효 지침.

    L1 COMPANY.md          전사 헌법
    L2 AGENT_TEAM.md       팀 행동양식
    L3 *_WORK.md           업무 SOP (에이전트가 수행 가능한 업무마다)
    L4 SOUL.md             개인 페르소나
    ⛔ gate.yaml           도구·자율화·예산 경계 (전사→본부→팀→개인 병합)

핵심 불변식 — **단조 축소**: 하위 계층은 상위를 좁힐 수만 있고 넓힐 수 없다.
위반 시 컴파일이 실패하고, 실패한 에이전트는 기동하지 않는다.

문서: docs/governance/CONTROL_PLANE.md
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gate import Gate, check_narrowing, gate_chain_for_agent, merge
from .registry import Registry, RegistryError

LAYER_TITLES = {
    "L1": "회사 헌법 (COMPANY.md) — 전사 · 아무도 넘을 수 없음",
    "L2": "팀 행동양식 (AGENT_TEAM.md)",
    "L3": "업무 지침 (*_WORK.md)",
    "L4": "개인 페르소나 (SOUL.md)",
}


class CompileError(Exception):
    """통제 평면 컴파일 실패. 이 에이전트는 기동할 수 없다."""


@dataclass
class Layer:
    level: str  # L1 | L2 | L3 | L4
    label: str
    path: Path
    text: str

    @property
    def rel(self) -> str:
        return self.label


@dataclass
class CompiledAgent:
    agent_id: str
    team_id: str
    division_id: str
    businesses: list[str]
    persona: str
    role: str
    autonomy: str
    zone: str | None
    layers: list[Layer]
    gate: Gate
    works: list[str]
    declared_tools: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ── 산출 ────────────────────────────────────────────────────────────
    def system_prompt(self) -> str:
        """4계층을 하나의 시스템 프롬프트로 조립한다. 상위가 먼저 나온다."""
        parts: list[str] = [
            "# 통제 평면 — 아래 문서는 우선순위 순이다.",
            "# 충돌 시 항상 **먼저 나온 문서**가 이긴다. 하위 문서는 상위를 좁힐 수만 있다.",
            "",
        ]
        for layer in self.layers:
            parts.append(f"<!-- ═══ {layer.level}: {LAYER_TITLES[layer.level]} ═══ -->")
            parts.append(f"<!-- source: {layer.label} -->")
            parts.append(layer.text.strip())
            parts.append("")

        parts.append("<!-- ═══ ⛔ 실효 경계 (기계 강제 — 문장으로 넘을 수 없음) ═══ -->")
        parts.append("```json")
        parts.append(
            json.dumps(self.gate.to_dict(self.declared_tools), ensure_ascii=False, indent=2)
        )
        parts.append("```")
        return "\n".join(parts)

    def bundle(self) -> dict[str, Any]:
        """P2 하네스가 먹고, P3 관제가 기준선으로 쓰는 정책 번들."""
        prompt = self.system_prompt()
        return {
            "schema_version": 1,
            "agent": {
                "id": self.agent_id,
                "team": self.team_id,
                "division": self.division_id,
                "businesses": self.businesses,
                "persona": self.persona,
                "role": self.role,
                "autonomy": self.autonomy,
                "zone": self.zone,
                "works": self.works,
            },
            "control_plane": {
                "layers": [
                    {"level": lyr.level, "source": lyr.label, "sha256": _sha(lyr.text)}
                    for lyr in self.layers
                ],
                "prompt_sha256": _sha(prompt),
                "prompt_chars": len(prompt),
            },
            "gate": self.gate.to_dict(self.declared_tools),
            "warnings": self.warnings,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── 컴파일 ──────────────────────────────────────────────────────────────


def compile_agent(registry: Registry, agent_id: str) -> CompiledAgent:
    """에이전트 1명의 통제 평면을 컴파일한다.

    Raises:
        CompileError: 문서 누락 또는 단조 축소 불변식 위반.
    """
    agent = registry.agent(agent_id)
    team = registry.team_of(agent_id)
    division = registry.division_of(agent_id)
    paths = registry.paths

    errors: list[str] = []
    warnings: list[str] = []
    layers: list[Layer] = []

    # ── L1 회사 ─────────────────────────────────────────────────────────
    if paths.company_md.is_file():
        layers.append(
            Layer(
                "L1", "COMPANY.md", paths.company_md, paths.company_md.read_text(encoding="utf-8")
            )
        )
    else:
        errors.append("L1 누락: COMPANY.md 가 저장소 루트에 없다")

    # ── L2 팀 ──────────────────────────────────────────────────────────
    team_md = team.dir / "AGENT_TEAM.md"
    if team_md.is_file():
        layers.append(
            Layer(
                "L2",
                str(team_md.relative_to(paths.root)),
                team_md,
                team_md.read_text(encoding="utf-8"),
            )
        )
    else:
        errors.append(
            f"L2 누락: {team_md.relative_to(paths.root)} 가 없다 "
            f"(팀 {team.id} 에 에이전트가 있으면 AGENT_TEAM.md 는 필수)"
        )

    # ── L3 업무 ────────────────────────────────────────────────────────
    if not agent.work_ids:
        errors.append(f"L3 누락: 에이전트 {agent_id} 에 works 가 비어 있다")
    for wid in agent.work_ids:
        work = registry.works.get(wid)
        if work is None:
            errors.append(f"L3 누락: 업무 {wid} 문서를 찾을 수 없다")
            continue
        layers.append(Layer("L3", str(work.source.relative_to(paths.root)), work.source, work.body))
        owner = work.meta.get("owner_team")
        if owner and owner != team.id:
            warnings.append(
                f"업무 {wid} 의 소유 팀은 {owner} 인데 {team.id} 의 에이전트가 수행한다"
            )
        amax = work.meta.get("autonomy_max")
        if amax:
            from .gate import AUTONOMY_ORDER

            if AUTONOMY_ORDER.index(agent.data["autonomy"]) > AUTONOMY_ORDER.index(amax):
                errors.append(
                    f"자율화 위반: 에이전트 {agent_id}({agent.data['autonomy']}) 가 "
                    f"업무 {wid} 의 상한({amax})을 넘는다"
                )

    # ── L4 개인 ────────────────────────────────────────────────────────
    soul = agent.dir / "SOUL.md"
    if soul.is_file():
        layers.append(
            Layer("L4", str(soul.relative_to(paths.root)), soul, soul.read_text(encoding="utf-8"))
        )
    else:
        errors.append(f"L4 누락: {soul.relative_to(paths.root)} 가 없다")

    # ── 게이트 병합 + 단조 축소 검증 ────────────────────────────────────
    chain = gate_chain_for_agent(registry, agent_id)
    gate = merge([(label, doc) for label, doc, _ in chain])

    for i in range(1, len(chain)):
        accumulated = merge([(lbl, d) for lbl, d, _ in chain[:i]])
        _, doc, path = chain[i]
        errors.extend(check_narrowing(accumulated, doc, str(path.relative_to(paths.root))))

    # ── 매니페스트 vs 게이트 정합성 ────────────────────────────────────
    declared = set(agent.data.get("tools") or [])
    rejected = gate.rejected(declared)
    if rejected:
        errors.append(
            f"도구 위반: 에이전트 매니페스트가 게이트 밖 도구를 선언했다 — "
            f"{', '.join(sorted(rejected))}"
        )

    # 게이트가 허용하는데 매니페스트가 안 쓰는 도구 → 최소권한 관점의 잉여
    cat = registry.tool_catalog
    if cat is not None:
        granted = {t for t in cat.tools if gate.permits(t)}
        unused = granted - declared
        if unused:
            warnings.append(
                f"게이트는 허용하나 매니페스트가 선언하지 않은 도구 {len(unused)}개 "
                f"(최소권한 관점에서 게이트를 좁힐 여지) — {', '.join(sorted(unused))}"
            )
        destructive = sorted(t for t in declared if cat.destructive(t))
        if destructive:
            warnings.append(
                f"비가역 도구 선언 — {', '.join(destructive)} (HITL 필수 대상인지 확인하라)"
            )

    from .gate import AUTONOMY_ORDER

    if AUTONOMY_ORDER.index(agent.data["autonomy"]) > AUTONOMY_ORDER.index(gate.autonomy):
        errors.append(
            f"자율화 위반: 매니페스트 {agent.data['autonomy']} 가 게이트 상한 {gate.autonomy} 초과"
        )

    if not gate.telemetry.get("emit", False):
        warnings.append("텔레메트리 emit 이 꺼져 있다 — P3 관제가 이 에이전트를 못 본다")

    if errors:
        raise CompileError(
            f"[{agent_id}] 통제 평면 컴파일 실패 — 이 에이전트는 기동할 수 없다:\n"
            + "\n".join(f"  ✗ {e}" for e in errors)
        )

    return CompiledAgent(
        agent_id=agent_id,
        team_id=team.id,
        division_id=division.id,
        businesses=[b.id for b in registry.businesses_of(agent_id)],
        persona=agent.data["persona"],
        role=agent.data["role"],
        autonomy=agent.data["autonomy"],
        zone=agent.data.get("zone") or team.data.get("zone"),
        layers=layers,
        gate=gate,
        works=agent.work_ids,
        declared_tools=sorted(declared),
        warnings=warnings,
    )


def compile_all(registry: Registry) -> tuple[dict[str, CompiledAgent], dict[str, str]]:
    """등록된 모든 에이전트를 컴파일한다. (성공 맵, 실패 맵)."""
    ok: dict[str, CompiledAgent] = {}
    failed: dict[str, str] = {}
    for aid in sorted(registry.agents):
        try:
            ok[aid] = compile_agent(registry, aid)
        except (CompileError, RegistryError) as exc:
            failed[aid] = str(exc)
    return ok, failed


def write_bundles(registry: Registry, out_dir: Path | None = None) -> list[Path]:
    """컴파일 결과를 var/control-plane/ 에 떨군다 (P2 하네스가 읽는다)."""
    out = out_dir or registry.paths.build_dir
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    ok, failed = compile_all(registry)
    if failed:
        raise CompileError(
            "컴파일 실패한 에이전트가 있어 번들을 쓰지 않는다:\n"
            + "\n".join(f"--- {k} ---\n{v}" for k, v in failed.items())
        )
    for aid, compiled in ok.items():
        (out / f"{aid}.bundle.json").write_text(
            json.dumps(compiled.bundle(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out / f"{aid}.prompt.md").write_text(compiled.system_prompt() + "\n", encoding="utf-8")
        written += [out / f"{aid}.bundle.json", out / f"{aid}.prompt.md"]
    return written
