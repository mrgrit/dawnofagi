"""dawn_core — the dawn of AGI 공용 라이브러리.

registry       조직·사업·에이전트·업무 레지스트리 (org/, work/ 매니페스트)
gate           gate.yaml 병합 — 단조 축소 불변식
control_plane  COMPANY/AGENT_TEAM/WORK/SOUL 4계층 컴파일러
lint           Control Readiness Score
"""

from __future__ import annotations

__version__ = "0.1.0"

from .control_plane import CompiledAgent, CompileError, compile_agent, compile_all
from .gate import Gate, GateError, merge
from .paths import Paths, find_root
from .registry import Registry, RegistryError

__all__ = [
    "CompileError",
    "CompiledAgent",
    "Gate",
    "GateError",
    "Paths",
    "Registry",
    "RegistryError",
    "__version__",
    "compile_agent",
    "compile_all",
    "find_root",
    "merge",
]
