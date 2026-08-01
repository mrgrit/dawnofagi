"""수집 계층 — P2 스팬을 공통 스키마로 정규화해 트레이스 레이크에 넣는다.

AOC 5계층의 [1]. 원본은 `var/traces/<trace_id>.jsonl` (P2 텔레메트리가 쓴 것).
여기서 하는 일은 **읽고·정규화하고·검증하는 것**이지 새로 만드는 게 아니다.

정규화가 필요한 이유: 스팬은 실행 순서대로 쌓이지만(자식이 부모보다 먼저 끝난다)
관제는 **에이전트 실행 단위(run)** 로 본다. `invoke_agent` 스팬 하나 = run 하나로
접고, 그 밑의 chat/execute_tool 을 붙인다.

PII 마스킹은 **수집 계층에 내장**한다 (05_conventions "로그 자체가 민감정보").
P2 가 이미 마스킹해서 쓰지만, 여기서 **다시 검증한다** — 마스킹은 한 곳만
믿으면 안 된다. 새는 게 발견되면 `masking_violations` 로 올라간다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dawn_agents.telemetry import GEN_AI_SEMCONV_VERSION, mask_pii

# 수집 시점에 "이건 마스킹됐어야 한다"를 다시 확인하는 패턴.
# P2 의 마스킹과 같은 대상이지만 독립적으로 판정한다 (이중 확인).
LEAK_PATTERNS = [
    ("주민등록번호", re.compile(r"\b\d{6}-[1-4]\d{6}\b")),
    ("카드·계좌", re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b")),
    ("이메일", re.compile(r"\b[A-Za-z0-9._%+-]{2,}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("API 키", re.compile(r"\b(sk-ant-|ghp_|sk-)[A-Za-z0-9_\-]{16,}")),
]


@dataclass
class Run:
    """정규화된 에이전트 실행 하나 — 관제의 기본 단위."""

    trace_id: str
    agent_id: str
    agent_name: str = ""
    team: str = ""
    division: str = ""
    eg_org: str = ""
    persona: str = ""
    autonomy: str = "A1"
    zone: str = ""
    started_ns: int = 0
    duration_ms: float = 0.0
    status: str = "OK"
    error: str = ""

    model: str = ""
    model_policy: str = ""
    provider: str = ""
    model_local: bool = False
    tokens_in: int = 0
    tokens_out: int = 0

    steps: int = 0
    tool_calls: int = 0
    chat_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    gate_decisions: dict[str, int] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    hitl_ids: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    max_severity: int = 0
    complete: bool = False

    spans: list[dict[str, Any]] = field(default_factory=list)
    masking_violations: list[dict[str, str]] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def is_orchestrator(self) -> bool:
        return self.agent_name.startswith("orchestrator:")

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "spans"}
        d["tokens"] = self.tokens
        d["span_count"] = len(self.spans)
        return d


def check_masking(text: str) -> list[dict[str, str]]:
    """이 문자열에 마스킹돼야 할 것이 남아 있나."""
    out = []
    for label, pat in LEAK_PATTERNS:
        m = pat.search(text)
        if m:
            out.append({"kind": label, "sample": mask_pii(m.group(0))})
    return out


def _span_text(span: dict[str, Any]) -> str:
    parts = [json.dumps(span.get("attributes", {}), ensure_ascii=False)]
    for ev in span.get("events", []):
        parts.append(json.dumps(ev.get("attributes", {}), ensure_ascii=False))
    return "\n".join(parts)


class TraceLake:
    """트레이스 레이크 — 스팬 원본과 정규화된 run 을 함께 보관한다."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.trace_dir = self.root / "var" / "traces"
        self.out_dir = self.root / "var" / "aoc"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ── 읽기 ────────────────────────────────────────────────────────────
    def trace_ids(self) -> list[str]:
        if not self.trace_dir.is_dir():
            return []
        return [
            p.stem
            for p in sorted(
                self.trace_dir.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True
            )
        ]

    def spans(self, trace_id: str) -> list[dict[str, Any]]:
        p = self.trace_dir / f"{trace_id}.jsonl"
        if not p.is_file():
            return []
        return [
            json.loads(ln)
            for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]

    # ── 정규화 ──────────────────────────────────────────────────────────
    def normalize(self, trace_id: str) -> list[Run]:
        """한 트레이스의 스팬을 run 단위로 접는다."""
        spans = self.spans(trace_id)
        if not spans:
            return []

        children: dict[str, list[dict]] = {}
        for s in spans:
            children.setdefault(s.get("parent_span_id"), []).append(s)

        runs: list[Run] = []
        for s in spans:
            if s["name"] != "invoke_agent":
                continue
            a = s["attributes"]
            r = Run(
                trace_id=trace_id,
                agent_id=a.get("gen_ai.agent.id", ""),
                agent_name=a.get("gen_ai.agent.name", ""),
                team=a.get("dawn.team", ""),
                division=a.get("dawn.division", ""),
                eg_org=a.get("dawn.eg_org", ""),
                persona=a.get("dawn.persona", ""),
                autonomy=a.get("dawn.autonomy", "A1"),
                zone=a.get("dawn.zone", ""),
                started_ns=s["start_ns"],
                duration_ms=s.get("duration_ms", 0.0),
                status=s.get("status", "OK"),
                error=s.get("status_message", ""),
                complete=bool(a.get("dawn.run.complete", False)),
            )
            r.spans.append(s)

            # 이 invoke_agent 아래의 자손을 모은다 (중첩 invoke_agent 는 별도 run)
            stack = list(children.get(s["span_id"], []))
            while stack:
                c = stack.pop()
                if c["name"] == "invoke_agent":
                    continue        # 자식 에이전트는 자기 run 을 갖는다
                r.spans.append(c)
                stack.extend(children.get(c["span_id"], []))

            for c in r.spans:
                ca = c.get("attributes", {})
                if c["name"] == "chat":
                    r.chat_calls += 1
                    r.model = ca.get("gen_ai.request.model", r.model)
                    r.model_policy = ca.get("dawn.model.policy", r.model_policy)
                    r.provider = ca.get("gen_ai.system", r.provider)
                    r.model_local = bool(ca.get("dawn.model.local", r.model_local))
                    r.tokens_in += int(ca.get("gen_ai.usage.input_tokens", 0) or 0)
                    r.tokens_out += int(ca.get("gen_ai.usage.output_tokens", 0) or 0)
                elif c["name"] == "execute_tool":
                    tool = ca.get("gen_ai.tool.name", "")
                    if tool:
                        r.tool_sequence.append(tool)
                        if tool not in r.tools_used:
                            r.tools_used.append(tool)
                    dec = ca.get("dawn.gate.decision")
                    if dec:
                        r.gate_decisions[dec] = r.gate_decisions.get(dec, 0) + 1
                    if dec in ("log_only", "warn"):
                        r.tool_calls += 1
                    if dec == "block":
                        r.blocked.append(tool)
                    if ca.get("dawn.hitl.id"):
                        r.hitl_ids.append(ca["dawn.hitl.id"])
                    for aid in str(ca.get("dawn.assets", "")).split(","):
                        if aid and aid not in r.assets:
                            r.assets.append(aid)
                    for pid in str(ca.get("dawn.policies", "")).split(","):
                        if pid and pid not in r.policies:
                            r.policies.append(pid)
                    r.max_severity = max(r.max_severity, int(ca.get("dawn.severity", 0) or 0))

                # PII 재검증 — 마스킹은 한 곳만 믿지 않는다
                r.masking_violations.extend(check_masking(_span_text(c)))

            r.steps = len(r.spans)
            runs.append(r)

        return sorted(runs, key=lambda x: x.started_ns)

    def all_runs(self, limit: int = 100) -> list[Run]:
        out: list[Run] = []
        for tid in self.trace_ids()[:limit]:
            out.extend(self.normalize(tid))
        return sorted(out, key=lambda r: r.started_ns, reverse=True)

    # ── 저장 ────────────────────────────────────────────────────────────
    def persist(self, runs: list[Run]) -> Path:
        """정규화 결과를 레이크에 저장 (P3 콘솔·픽셀오피스가 읽는다)."""
        p = self.out_dir / "runs.jsonl"
        p.write_text(
            "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in runs) + "\n",
            encoding="utf-8",
        )
        return p

    def stats(self, runs: list[Run]) -> dict[str, Any]:
        return {
            "semconv_version": GEN_AI_SEMCONV_VERSION,
            "traces": len({r.trace_id for r in runs}),
            "runs": len(runs),
            "spans": sum(len(r.spans) for r in runs),
            "agents": sorted({r.agent_id for r in runs if r.agent_id}),
            "tokens": sum(r.tokens for r in runs),
            "masking_violations": sum(len(r.masking_violations) for r in runs),
            "gate_decisions": _merge_counts([r.gate_decisions for r in runs]),
        }


def _merge_counts(dicts: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))
