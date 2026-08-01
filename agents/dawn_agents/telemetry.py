"""텔레메트리 — OTel GenAI semantic conventions 스팬 방출.

P3 관제의 수집 계층이 이걸 받는다. 05_conventions "모든 AI 지원 의사결정은
사후 재구성 가능(EU AI Act 12조)"의 실물.

스팬 트리 (04_tech_stack.md):
    invoke_agent
      ├─ chat                 (모델 호출)
      ├─ execute_tool         (도구 실행)
      └─ execute_tool …

왜 opentelemetry-sdk 를 안 쓰나:
  P2 DoD 는 "스팬 방출 확인"이다. OTel SDK + OTLP exporter 는 의존성 2개와
  콜렉터 1대를 요구하는데, fresh Linux 배포에서 그게 없어도 에이전트는 돌아야 한다.
  그래서 **semconv 는 그대로 따르되** 익스포터는 JSONL 트레이스 레이크로 시작한다.
  OTLP 로 바꿀 때 스팬 속성은 한 글자도 안 바뀐다 — P3 에서 exporter 만 갈아끼운다.

semconv 버전은 **핀한다** (experimental — 04_tech_stack.md 경고).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# OTel GenAI semantic conventions — experimental. 버전을 고정한다.
GEN_AI_SEMCONV_VERSION = "1.29.0"

# 스팬 이름 (semconv: gen_ai.operation.name)
OP_INVOKE_AGENT = "invoke_agent"
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"

_local = threading.local()


def _now_ns() -> int:
    return time.time_ns()


def _hexid(n: int) -> str:
    return uuid.uuid4().hex[: n * 2]


@dataclass
class Span:
    """OTel 호환 스팬. 속성 이름은 semconv 를 그대로 쓴다."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    end_ns: int | None = None
    status: str = "UNSET"  # UNSET | OK | ERROR
    status_message: str = ""

    @property
    def duration_ms(self) -> float:
        return ((self.end_ns or _now_ns()) - self.start_ns) / 1e6

    def set(self, **attrs: Any) -> Span:
        self.attributes.update({k: v for k, v in attrs.items() if v is not None})
        return self

    def event(self, name: str, **attrs: Any) -> Span:
        self.events.append({"name": name, "time_ns": _now_ns(), "attributes": attrs})
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "status_message": self.status_message,
            "attributes": self.attributes,
            "events": self.events,
            "resource": {
                "telemetry.sdk.name": "dawn",
                "gen_ai.semconv.version": GEN_AI_SEMCONV_VERSION,
            },
        }


class Tracer:
    """스팬 수집·방출. 기본 익스포터는 JSONL 트레이스 레이크."""

    def __init__(self, out_dir: Path | str | None = None, *, capture_content: str = "masked"):
        self.out_dir = Path(out_dir) if out_dir else None
        self.capture_content = capture_content  # none | masked | full
        self.spans: list[Span] = []
        self._lock = threading.Lock()

    # ── 스팬 생성 ───────────────────────────────────────────────────────
    @contextmanager
    def span(self, name: str, **attrs: Any):
        parent = getattr(_local, "current_span", None)
        trace_id = parent.trace_id if parent else _hexid(16)
        sp = Span(
            name=name,
            trace_id=trace_id,
            span_id=_hexid(8),
            parent_span_id=parent.span_id if parent else None,
            start_ns=_now_ns(),
        )
        sp.set(**attrs)
        prev = parent
        _local.current_span = sp
        try:
            yield sp
            if sp.status == "UNSET":
                sp.status = "OK"
        except Exception as exc:
            sp.status = "ERROR"
            sp.status_message = f"{type(exc).__name__}: {exc}"
            sp.event(
                "exception",
                **{
                    "exception.type": type(exc).__name__,
                    "exception.message": str(exc)[:500],
                },
            )
            raise
        finally:
            sp.end_ns = _now_ns()
            _local.current_span = prev
            self._emit(sp)

    def _emit(self, sp: Span) -> None:
        with self._lock:
            self.spans.append(sp)
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{sp.trace_id}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(sp.to_dict(), ensure_ascii=False) + "\n")

    # ── 내용 캡처 (PII 마스킹 전제 — 05_conventions) ────────────────────
    def content(self, text: str, *, max_chars: int = 2000) -> str | None:
        """프롬프트·응답 본문을 캡처할지 결정하고, 마스킹해서 돌려준다."""
        if self.capture_content == "none":
            return None
        text = text[:max_chars]
        if self.capture_content == "full":
            return text
        return mask_pii(text)

    def tree(self) -> str:
        """스팬 트리를 사람이 읽는 형태로."""
        by_parent: dict[str | None, list[Span]] = {}
        for s in self.spans:
            by_parent.setdefault(s.parent_span_id, []).append(s)

        lines: list[str] = []

        def walk(parent: str | None, depth: int) -> None:
            for s in by_parent.get(parent, []):
                mark = {"OK": "●", "ERROR": "✘", "UNSET": "○"}[s.status]
                detail = ""
                if s.name == OP_CHAT:
                    detail = (
                        f"  {s.attributes.get('gen_ai.request.model', '?')}"
                        f"  in={s.attributes.get('gen_ai.usage.input_tokens', '?')}"
                        f" out={s.attributes.get('gen_ai.usage.output_tokens', '?')}"
                    )
                elif s.name == OP_EXECUTE_TOOL:
                    detail = (
                        f"  {s.attributes.get('gen_ai.tool.name', '?')}"
                        f"  gate={s.attributes.get('dawn.gate.decision', '-')}"
                    )
                lines.append(f"{'  ' * depth}{mark} {s.name}{detail}  ({s.duration_ms:.0f}ms)")
                if s.status == "ERROR":
                    lines.append(f"{'  ' * (depth + 1)}  ↳ {s.status_message}")
                walk(s.span_id, depth + 1)

        walk(None, 0)
        return "\n".join(lines)


# ── PII 마스킹 (수집 계층에 내장 — 05_conventions "감사·로깅") ────────────


_PII_PATTERNS = [
    # 주민등록번호
    (re.compile(r"\b(\d{6})-?\d{7}\b"), r"\1-*******"),
    # 계좌·카드 (10자리 이상 숫자열)
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{2,4}\b"), "****-****-****-****"),
    # 이메일
    (re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)\b"), r"\1***@\2"),
    # 전화번호
    (re.compile(r"\b(01[016-9])-?\d{3,4}-?\d{4}\b"), r"\1-****-****"),
    # API 키류
    (re.compile(r"\b(sk-ant-|ghp_|gho_|sk-)[A-Za-z0-9_\-]{10,}"), r"\1***REDACTED***"),
]


def mask_pii(text: str) -> str:
    """로그 자체가 민감정보이므로 수집 시점에 마스킹한다."""
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ── 전역 트레이서 ────────────────────────────────────────────────────────

_default: Tracer | None = None


def get_tracer(root: Path | None = None) -> Tracer:
    global _default
    if _default is None:
        out = Path(os.getenv("DAWN_TRACE_DIR", "")) if os.getenv("DAWN_TRACE_DIR") else None
        if out is None and root is not None:
            out = root / "var" / "traces"
        _default = Tracer(out, capture_content=os.getenv("DAWN_CAPTURE_CONTENT", "masked"))
    return _default


def reset_tracer() -> None:
    global _default
    _default = None
