"""EG — Experience Graph. 회사의 뇌.

    store     bastion_graph.db 호환 그래프 저장소 (거버넌스 + 런타임 2계층)
    loader    eg/seed/*.json 주입 (경로 A)
    traverse  핵심 순회 — 심각도 · 게이트 · 개입지점 · 모델라우팅 · 자율화

사람의 개입은 여기 노드를 고치는 것으로 이루어진다. 코드 변경 0.
문서: docs/context/02_eg_schema.md · eg/BOOTSTRAP.md
"""

from __future__ import annotations

from .loader import LoadError, LoadResult, load, read_seeds, snapshot
from .store import Edge, EGError, EGStore, Node
from .traverse import (
    GateDecision,
    OrgProfile,
    Severity,
    all_severities,
    autonomy_violations,
    gate_for,
    model_for_org,
    org_profile,
    severity_of,
)

__all__ = [
    "EGError",
    "EGStore",
    "Edge",
    "GateDecision",
    "LoadError",
    "LoadResult",
    "Node",
    "OrgProfile",
    "Severity",
    "all_severities",
    "autonomy_violations",
    "gate_for",
    "load",
    "model_for_org",
    "org_profile",
    "read_seeds",
    "severity_of",
    "snapshot",
]
