"""게이트 병합 — 단조 축소(monotonic narrowing) 강제.

`gate.yaml` 은 전사(org/gate.yaml) → 본부 → 팀 → 에이전트 순으로 겹쳐진다.
병합 규칙은 **하위가 권한을 늘릴 수 없도록** 설계됐다:

    allow    : 교집합 (패턴 포함관계)  — 하위는 상위 허용 범위 밖으로 못 나간다
    deny     : 합집합                  — 한 번 금지되면 아래에서 못 푼다
    autonomy : 더 낮은 쪽              — A0 < A1 < A2 < A3
    budget   : 최솟값                  — 서킷 브레이커는 가장 조인 값
    hitl     : 합집합 + 임계는 최솟값   — 게이트 조건은 늘기만 한다
    model    : 더 엄격한 정책          — local_only > pinned > from_eg > cloud_ok

도구 이름은 `<namespace>.<action>` 규칙(`org/tools.yaml`)을 따르고,
allow/deny 는 정확한 이름 또는 글롭(`sec.*`)을 쓸 수 있다.
덕분에 전사 게이트는 **네임스페이스 단위로 우주를 정의**하고,
팀 게이트는 **자기 도메인으로 좁히기만** 하면 된다.

이 규칙 자체가 통제 평면의 안전 불변식이다. 여기를 고치면 회사 전체의 경계가 바뀐다.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

AUTONOMY_ORDER = ["A0", "A1", "A2", "A3"]

# 엄격한 순 → 느슨한 순. 병합 시 더 앞(엄격)이 이긴다.
MODEL_POLICY_STRICTNESS = ["local_only", "pinned", "from_eg", "cloud_ok"]

_BUDGET_KEYS = ("max_steps", "max_tokens", "max_tool_calls", "max_wallclock_sec")

# allow 가 아예 선언되지 않은 계층은 "제한 없음"이 아니라 "상속"으로 취급한다.
_UNSET = object()


class GateError(Exception):
    """게이트 병합·검증 실패."""


def matches(tool: str, patterns: Iterable[str]) -> bool:
    """도구 이름이 패턴 목록 중 하나에 맞는가."""
    return any(fnmatch.fnmatchcase(tool, p) for p in patterns)


def subsumes(parent_patterns: Iterable[str], child_pattern: str) -> bool:
    """부모 패턴 집합이 자식 패턴을 포함하는가.

    - 자식이 정확한 이름이면: 부모 중 하나가 그것을 매치하면 된다.
    - 자식이 글롭이면: 부모 패턴 자체가 자식 패턴을 매치해야 한다 (보수적).
      예) 부모 `sec.*` ⊇ 자식 `sec.siem_query` ✔ / ⊇ 자식 `sec.*` ✔ / ⊉ 자식 `*` ✘
    """
    parents = list(parent_patterns)
    if matches(child_pattern, parents):
        return True
    # 자식이 글롭인 경우 — 부모 글롭이 자식 글롭 문자열 자체를 덮는지 본다.
    return any(fnmatch.fnmatchcase(child_pattern, p) for p in parents)


@dataclass
class Gate:
    """병합된 실효 경계."""

    allow: set[str] = field(default_factory=set)  # 패턴
    deny: set[str] = field(default_factory=set)  # 패턴
    autonomy: str = "A1"
    hitl_require_on: set[str] = field(default_factory=set)
    amount_threshold_krw: float | None = None
    budget: dict[str, int] = field(default_factory=dict)
    model_policy: str = "from_eg"
    model_pinned: str | None = None
    force_local_when: set[str] = field(default_factory=set)
    telemetry: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    # ── 조회 ────────────────────────────────────────────────────────────
    def permits(self, tool: str) -> bool:
        """deny 가 allow 를 이긴다."""
        if matches(tool, self.deny):
            return False
        return matches(tool, self.allow)

    def filter(self, tools: Iterable[str]) -> set[str]:
        """선언된 도구 중 실제로 허용되는 것만."""
        return {t for t in tools if self.permits(t)}

    def rejected(self, tools: Iterable[str]) -> set[str]:
        """선언됐으나 게이트가 막는 도구."""
        return {t for t in tools if not self.permits(t)}

    def requires_hitl(self, *conditions: str) -> bool:
        return bool(self.hitl_require_on & set(conditions))

    def forces_local_model(self, *contexts: str) -> bool:
        if self.model_policy == "local_only":
            return True
        if "always" in self.force_local_when:
            return True
        return bool(self.force_local_when & set(contexts))

    def to_dict(self, declared: Iterable[str] | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tools": {
                "allow_patterns": sorted(self.allow),
                "deny_patterns": sorted(self.deny),
            },
            "autonomy": self.autonomy,
            "hitl": {
                "require_on": sorted(self.hitl_require_on),
                **(
                    {"amount_threshold_krw": self.amount_threshold_krw}
                    if self.amount_threshold_krw is not None
                    else {}
                ),
            },
            "budget": dict(sorted(self.budget.items())),
            "model": {
                "policy": self.model_policy,
                **({"pinned": self.model_pinned} if self.model_pinned else {}),
                "force_local_when": sorted(self.force_local_when),
            },
            "telemetry": self.telemetry,
            "sources": self.sources,
        }
        if declared is not None:
            d["tools"]["effective"] = sorted(self.filter(declared))
        return d

    def __str__(self) -> str:  # pragma: no cover
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def load_gate_file(path: Path) -> dict[str, Any]:
    """gate.yaml 하나를 읽고 스키마 검증한다."""
    from .registry import _load_yaml, _validate  # 순환 회피용 지연 임포트

    doc = _load_yaml(path)
    _validate(doc, "gate", path)
    return doc


def merge(layers: list[tuple[str, dict[str, Any]]]) -> Gate:
    """상위→하위 순서의 게이트 레이어들을 단조 축소 규칙으로 병합한다.

    Args:
        layers: [(출처 라벨, gate 문서), ...] — **상위가 먼저**.
    """
    allow: Any = _UNSET
    deny: set[str] = set()
    autonomy_idx: int | None = None
    hitl: set[str] = set()
    threshold: float | None = None
    budget: dict[str, int] = {}
    model_idx: int | None = None
    pinned: str | None = None
    force_local: set[str] = set()
    telemetry: dict[str, Any] = {}
    sources: list[str] = []

    for label, doc in layers:
        if not doc:
            continue
        sources.append(label)

        tools = doc.get("tools") or {}
        if "allow" in tools:
            incoming = set(tools["allow"] or [])
            if allow is _UNSET:
                allow = incoming
            else:
                # 하위 패턴 중 상위가 덮는 것만 남긴다 (교집합의 패턴 버전).
                allow = {p for p in incoming if subsumes(allow, p)}
        deny |= set(tools.get("deny") or [])

        if "autonomy" in doc:
            idx = AUTONOMY_ORDER.index(doc["autonomy"])
            autonomy_idx = idx if autonomy_idx is None else min(autonomy_idx, idx)

        h = doc.get("hitl") or {}
        hitl |= set(h.get("require_on") or [])
        if "amount_threshold_krw" in h:
            v = float(h["amount_threshold_krw"])
            threshold = v if threshold is None else min(threshold, v)

        b = doc.get("budget") or {}
        for k in _BUDGET_KEYS:
            if k in b:
                budget[k] = min(budget[k], int(b[k])) if k in budget else int(b[k])

        m = doc.get("model") or {}
        if "policy" in m:
            idx = MODEL_POLICY_STRICTNESS.index(m["policy"])
            model_idx = idx if model_idx is None else min(model_idx, idx)
        if m.get("pinned"):
            pinned = m["pinned"]
        force_local |= set(m.get("force_local_when") or [])

        t = doc.get("telemetry") or {}
        if t:
            merged = {**telemetry, **t}
            if telemetry.get("emit") is True:
                merged["emit"] = True  # 상위가 켰으면 하위가 못 끈다
            telemetry = merged

    return Gate(
        allow=set() if allow is _UNSET else set(allow),
        deny=deny,
        autonomy=AUTONOMY_ORDER[autonomy_idx if autonomy_idx is not None else 1],
        hitl_require_on=hitl,
        amount_threshold_krw=threshold,
        budget=budget,
        model_policy=MODEL_POLICY_STRICTNESS[model_idx] if model_idx is not None else "from_eg",
        model_pinned=pinned,
        force_local_when=force_local,
        telemetry=telemetry,
        sources=sources,
    )


def check_narrowing(parent: Gate, child_doc: dict[str, Any], label: str) -> list[str]:
    """하위 계층이 권한을 넓히려 했는지 검사한다. 위반 목록을 반환."""
    violations: list[str] = []
    tools = child_doc.get("tools") or {}
    child_allow = list(tools.get("allow") or [])

    if child_allow and parent.allow:
        escaped = [p for p in child_allow if not subsumes(parent.allow, p)]
        if escaped:
            violations.append(
                f"{label}: 상위 허용 범위 밖의 도구를 allow 하려 함 — {', '.join(sorted(escaped))} "
                f"(상위 허용: {', '.join(sorted(parent.allow))})"
            )

    unblocked = [p for p in child_allow if matches(p, parent.deny)]
    if unblocked:
        violations.append(
            f"{label}: 상위에서 deny 된 도구를 allow 하려 함 — {', '.join(sorted(unblocked))}"
        )

    if "autonomy" in child_doc and AUTONOMY_ORDER.index(
        child_doc["autonomy"]
    ) > AUTONOMY_ORDER.index(parent.autonomy):
        violations.append(
            f"{label}: 자율화 등급을 올리려 함 — {parent.autonomy} → {child_doc['autonomy']}"
        )

    b = child_doc.get("budget") or {}
    for k in _BUDGET_KEYS:
        if k in b and k in parent.budget and int(b[k]) > parent.budget[k]:
            violations.append(f"{label}: 예산을 늘리려 함 — {k} {parent.budget[k]} → {b[k]}")

    m = child_doc.get("model") or {}
    if "policy" in m and MODEL_POLICY_STRICTNESS.index(m["policy"]) > MODEL_POLICY_STRICTNESS.index(
        parent.model_policy
    ):
        violations.append(
            f"{label}: 모델 정책을 느슨하게 하려 함 — {parent.model_policy} → {m['policy']}"
        )

    t = child_doc.get("telemetry") or {}
    if t.get("emit") is False and parent.telemetry.get("emit") is True:
        violations.append(f"{label}: 상위가 켠 텔레메트리를 끄려 함")

    return violations


def gate_chain_for_agent(registry, agent_id: str) -> list[tuple[str, dict[str, Any], Path]]:
    """전사 → 본부 → 팀 → 에이전트 순의 gate.yaml 체인을 모은다 (존재하는 것만)."""
    agent = registry.agent(agent_id)
    team = registry.team_of(agent_id)
    division = registry.division_of(agent_id)

    candidates = [
        ("company", registry.paths.root_gate),
        (f"division:{division.id}", division.dir / "gate.yaml"),
        (f"team:{team.id}", team.dir / "gate.yaml"),
        (f"agent:{agent.id}", agent.dir / "gate.yaml"),
    ]
    chain: list[tuple[str, dict[str, Any], Path]] = []
    for label, path in candidates:
        if path.is_file():
            chain.append((label, load_gate_file(path), path))
    if not chain:
        raise GateError("게이트가 하나도 없다 — 최소한 org/gate.yaml 은 있어야 한다")
    return chain


# ── 도구 카탈로그 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCatalog:
    """org/tools.yaml — 회사가 아는 모든 도구."""

    namespaces: dict[str, dict[str, Any]]
    tools: dict[str, dict[str, Any]]
    source: Path

    @classmethod
    def load(cls, path: Path) -> ToolCatalog:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tools = doc.get("tools") or {}
        namespaces = doc.get("namespaces") or {}
        bad = [t for t in tools if "." not in t or t.split(".", 1)[0] not in namespaces]
        if bad:
            raise GateError(f"{path}: 알 수 없는 네임스페이스의 도구 — {', '.join(sorted(bad))}")
        return cls(namespaces=namespaces, tools=tools, source=path)

    def known(self, tool: str) -> bool:
        return tool in self.tools

    def unknown(self, tools: Iterable[str]) -> list[str]:
        return sorted(t for t in tools if t not in self.tools)

    def pattern_covers_nothing(self, pattern: str) -> bool:
        """카탈로그의 어떤 도구도 매치하지 않는 죽은 패턴인가."""
        return not any(fnmatch.fnmatchcase(t, pattern) for t in self.tools)

    def destructive(self, tool: str) -> bool:
        return bool(self.tools.get(tool, {}).get("destructive"))

    def risk(self, tool: str) -> str:
        return str(self.tools.get(tool, {}).get("risk", "MED"))
