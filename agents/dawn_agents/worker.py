"""워커 에이전트 — 모든 에이전트가 공통으로 도는 4단계 루프.

    ① eg_search      착수 전 — 내 조직의 페르소나·정책·과거 경험 조회
    ② skill_preview  실행 전 — 위험도·비가역 확인
    ③ skill_run      HIGH/destructive 면 HITL 승인 게이트 통과 후에만
    ④ eg_record      완료 후 — 결과·finding 축적

②를 건너뛴 ③은 **구조적으로 불가능하다** — `_use_skill()` 이 preview 없이는
게이트를 만들지 않고, 게이트 없이는 run 을 호출하지 않는다.
④ 없이 끝난 작업은 `WorkerRun.complete=False` 로 남는다.

시스템 프롬프트는 통제 평면 컴파일 결과(4계층 + 게이트)에 EG 프로파일을 얹어 만든다.
**행동 규칙을 코드에 박지 않는다** — 전부 조회 결과다 (COMPANY.md §6.2-6).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from dawn_core import Registry, compile_agent
from dawn_core.eg.cli import db_path as eg_db_path
from dawn_core.eg.store import EGStore
from dawn_core.eg.traverse import org_profile
from dawn_core.paths import Paths

from . import llm as llm_mod
from .actiongate import ActionGate, GateDecision
from .hitl import ApprovalQueue, auto_approve_enabled
from .skills import Preview, SkillRegistry, SkillResult, build_default_registry
from .telemetry import OP_CHAT, OP_EXECUTE_TOOL, OP_INVOKE_AGENT, Tracer, get_tracer

# run 의 목적. **KPI 는 `work` 만 센다** — 드릴·레드팀은 일부러 차단되어 ④ 에
# 도달하지 않으므로 같이 세면 성공률이 거짓이 된다.
RUN_PURPOSES = {"work", "drill", "redteam", "demo"}


class WorkerError(Exception):
    """워커 실행 실패."""


class CircuitBreaker(WorkerError):
    """서킷 브레이커 발동 — 예산 초과 (gate.yaml budget)."""


@dataclass
class Step:
    """루프 한 스텝의 기록."""

    n: int
    kind: str  # eg_search | preview | gate | run | chat | record | hitl
    detail: str
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        mark = "●" if self.ok else "✘"
        return f"  {mark} [{self.n:02d}] {self.kind:<12} {self.detail}"


@dataclass
class WorkerRun:
    agent_id: str
    task: str
    steps: list[Step] = field(default_factory=list)
    trace_id: str = ""
    # 이 실행이 실업무인가 훈련인가. **run 의 속성이다** — 승인 요청·케이스가
    # 이걸 그대로 물려받아야 훈련이 사람의 큐를 막지 않는다.
    purpose: str = "work"
    model: str = ""
    model_policy: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    hitl_requests: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    output: str = ""
    recorded: bool = False
    error: str = ""

    @property
    def complete(self) -> bool:
        """④ eg_record 를 마쳐야 완료다."""
        return self.recorded and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent_id,
            "task": self.task,
            "trace_id": self.trace_id,
            "model": self.model,
            "model_policy": self.model_policy,
            "provider": self.provider,
            "tokens": {"in": self.tokens_in, "out": self.tokens_out},
            "tool_calls": self.tool_calls,
            "hitl_requests": self.hitl_requests,
            "blocked": self.blocked,
            "recorded": self.recorded,
            "complete": self.complete,
            "error": self.error,
            "steps": [
                {"n": s.n, "kind": s.kind, "detail": s.detail, "ok": s.ok} for s in self.steps
            ],
        }


class Worker:
    """통제 평면 + EG 위에서 도는 에이전트 하나."""

    def __init__(
        self,
        agent_id: str,
        *,
        registry: Registry | None = None,
        eg_store: EGStore | None = None,
        skills: SkillRegistry | None = None,
        tracer: Tracer | None = None,
        queue: ApprovalQueue | None = None,
        client: llm_mod.LLMClient | None = None,
    ) -> None:
        self.registry = registry or Registry.load()
        self.paths: Paths = self.registry.paths
        self.agent_id = agent_id

        # 통제 평면 — 컴파일 실패 시 기동하지 않는다
        self.compiled = compile_agent(self.registry, agent_id)
        self.team = self.registry.teams[self.compiled.team_id]
        self.eg_org = self.team.data.get("eg_org")

        db = eg_db_path(self.paths)
        self.eg = eg_store if eg_store is not None else (EGStore(db) if db.is_file() else None)
        self.profile = (
            org_profile(self.eg, self.eg_org)
            if self.eg is not None and self.eg_org and self.eg.node(self.eg_org)
            else None
        )

        self.skills = skills or build_default_registry(
            self.registry.tool_catalog, root=self.paths.root, eg_store=self.eg,
            agent_id=self.agent_id
        )
        tier = ""
        if self.profile is not None and self.profile.models:
            tier = self.profile.models[0].prop("cost_tier", "")
        self.gate = ActionGate(
            self.compiled.gate,
            self.eg,
            autonomy=self.compiled.autonomy,
            org_id=self.eg_org or "",
            model_cost_tier=tier,
            catalog=self.skills.catalog,
        )
        self.tracer = tracer or get_tracer(self.paths.root)
        self.queue = queue or ApprovalQueue(self.paths.root)
        self.client = client or llm_mod.LLMClient()

        self.budget = self.compiled.gate.budget
        self._step_n = 0

    # ── 시스템 프롬프트 = 통제 평면 4계층 + EG 프로파일 ─────────────────
    def system_prompt(self) -> str:
        parts = [self.compiled.system_prompt()]
        if self.profile is not None:
            parts.append("\n<!-- ═══ EG: 내 조직의 페르소나·정책·모델·자율화 ═══ -->")
            parts.append("```json")
            parts.append(json.dumps(self.profile.to_dict(), ensure_ascii=False, indent=2))
            parts.append("```")
        parts.append(
            "\n<!-- ═══ 사용 가능한 스킬 (이 밖의 도구는 게이트가 막는다) ═══ -->\n"
            + "\n".join(f"- {s}" for s in self.compiled.declared_tools)
        )
        return "\n".join(parts)

    # ── 스텝 기록 ───────────────────────────────────────────────────────
    def _step(
        self,
        run: WorkerRun,
        kind: str,
        detail: str,
        ok: bool = True,
        *,
        enforce: bool = True,
        **data,
    ) -> Step:
        self._step_n += 1
        max_steps = self.budget.get("max_steps")
        # 종료 기록(error/중단 observation)은 예산에 걸지 않는다 —
        # 브레이커가 감사 추적까지 끊으면 사후 재구성이 불가능해진다.
        if enforce and max_steps and self._step_n > max_steps:
            raise CircuitBreaker(
                f"서킷 브레이커 — 스텝 {self._step_n} > 예산 {max_steps} (gate.yaml budget)"
            )
        s = Step(self._step_n, kind, detail, ok, data)
        run.steps.append(s)
        return s

    # ── ① eg_search ────────────────────────────────────────────────────
    def eg_search(self, run: WorkerRun, query: str, limit: int = 5) -> str:
        with self.tracer.span(
            OP_EXECUTE_TOOL,
            **{
                "gen_ai.tool.name": "eg.search",
                "gen_ai.operation.name": OP_EXECUTE_TOOL,
                # 만지는 자산은 **선언한다** (카탈로그가 권위). 다만 이 단계는 루프의
                # 필수 단계라 액션 게이트를 지나지 않는다 — 그 사실도 같이 싣는다.
                # 관제가 "존을 넘었다"와 "자기 자리에서 조회했다"를 구분해야 한다.
                "dawn.assets": ",".join(self.skills.get("eg.search").touches),
                "dawn.gate.evaluated": False,
            },
        ) as sp:
            res = self.skills.run("eg.search", query=query, limit=limit)
            sp.set(**{
                "dawn.eg.hits": res.meta.get("hits", 0),
                # 무엇을 읽었는지 — 사후에 "이 판단의 근거"를 재구성하려면 필요하다
                "dawn.eg.hit_ids": ",".join(res.meta.get("hit_ids", [])),
                "dawn.gate.decision": "log_only",
            })
        self._step(run, "eg_search", f'"{query}" → {res.meta.get("hits", 0)}건', res.ok)
        return res.output

    # ── ②③ preview → gate → run ────────────────────────────────────────
    def use_skill(
        self, run: WorkerRun, name: str, **kwargs
    ) -> tuple[GateDecision, SkillResult | None]:
        """스킬 하나를 쓴다. **preview 없이는 절대 run 하지 않는다.**"""
        max_calls = self.budget.get("max_tool_calls")
        if max_calls and run.tool_calls >= max_calls:
            raise CircuitBreaker(f"서킷 브레이커 — 도구 호출 {run.tool_calls} ≥ 예산 {max_calls}")

        # ② preview (건너뛸 수 없다)
        preview: Preview = self.skills.preview(name, **kwargs)
        self._step(run, "preview", preview.line())

        # 게이트 판정 (통제평면 × 스킬위험도 × EG)
        decision = self.gate.evaluate(preview, declared_tools=self.compiled.declared_tools)
        self._step(run, "gate", decision.line(), decision.allowed_without_human)

        with self.tracer.span(
            OP_EXECUTE_TOOL,
            **{
                "gen_ai.tool.name": name,
                "gen_ai.operation.name": OP_EXECUTE_TOOL,
                "dawn.skill.risk": preview.risk,
                "dawn.skill.destructive": preview.destructive,
                "dawn.gate.decision": decision.decision,
                "dawn.gate.evaluated": True,
                "dawn.gate.reasons": "; ".join(decision.reasons),
                "dawn.severity": decision.severity,
                "dawn.assets": ",".join(decision.assets),
                "dawn.policies": ",".join(decision.policies),
            },
        ) as sp:
            # block — 실행하지 않는다
            if decision.blocked:
                run.blocked.append(name)
                ap = self.queue.request(
                    agent_id=self.agent_id,
                    skill=name,
                    gate_decision=decision,
                    args=kwargs,
                    trace_id=run.trace_id,
                    purpose=run.purpose,     # 훈련이 사람의 큐를 막지 않게
                )
                run.hitl_requests.append(ap.id)
                sp.set(**{"dawn.hitl.id": ap.id})
                sp.event("blocked", reason="; ".join(decision.reasons))
                # 여기서 run 이 끝난다 — 나중에 승인해도 이 행동은 집행되지 않는다.
                # 화면이 그 사실을 말할 수 있게 남긴다.
                self.queue.mark_run_ended(ap.id)
                self._step(run, "hitl", f"차단 → 승인 큐 {ap.id}", False)
                return decision, None

            # require_hitl — 승인 전에는 실행하지 않는다
            if decision.needs_hitl:
                ap = self.queue.request(
                    agent_id=self.agent_id,
                    skill=name,
                    gate_decision=decision,
                    args=kwargs,
                    trace_id=run.trace_id,
                    purpose=run.purpose,     # 훈련이 사람의 큐를 막지 않게
                )
                run.hitl_requests.append(ap.id)
                sp.set(**{"dawn.hitl.id": ap.id})
                if not auto_approve_enabled():
                    sp.event("awaiting_approval", approval_id=ap.id)
                    self.queue.mark_run_ended(ap.id)     # 재개 경로가 없다
                    self._step(run, "hitl", f"승인 대기 {ap.id} — 실행 보류", False)
                    return decision, None
                self.queue.decide(
                    ap.id, approve=True, by="DAWN_AUTO_APPROVE", note="데모/테스트 자동 승인"
                )
                sp.event("auto_approved", approval_id=ap.id)
                self._step(run, "hitl", f"자동 승인 {ap.id} (DAWN_AUTO_APPROVE)")

            # ③ run
            result = self.skills.run(name, **kwargs)
            run.tool_calls += 1
            sp.set(**{"dawn.skill.ok": result.ok})
            if not result.ok:
                sp.status = "ERROR"
                sp.status_message = result.error
            self._step(
                run, "run", f"{name} → {'성공' if result.ok else result.error[:70]}", result.ok
            )
            return decision, result


    # ── 도구 루프 (T18-b) ───────────────────────────────────────────────
    #
    # 프로토콜을 태그로 쓰는 이유: JSON 한 덩어리를 요구하면 모델이 코드·본문을
    # 통째로 문자열에 넣어야 하고, 거기서 이스케이프가 깨진다. 실제로 넣을 것이
    # 파일 내용이라 그 위험이 크다.
    ACT_SYSTEM = """
너는 지금 **실제로 일한다.** 계획만 내지 말고 도구를 써서 산출물을 만들어라.

## 먼저 알아둘 것

이 세션에서 **너 자신의 도구(Write·Bash·Read 등)는 꺼져 있다. 그게 정상이다.**
네가 무언가를 실행하는 길은 아래 태그 하나뿐이고, 태그를 내면 회사의 스킬
계층이 대신 집행한다. 그래야 게이트 판정과 감사 기록이 남는다.

그러므로 **"권한이 없어 못 한다" 고 답하지 마라.** 그건 틀린 보고다 — 아직
요청하지 않았을 뿐이다. 파일을 만들어야 하면 내용을 설명하지 말고 태그를 내라.

## 형식

한 번에 **하나**만 낸다. 답에 태그 외의 말은 넣지 않는다:

<도구 이름="fs.write">
<인자 이름="path">apps/coss/README.md</인자>
<인자 이름="content">여러 줄
그대로 들어간다</인자>
</도구>

결과가 돌아오면 다음 한 수를 낸다. 할 일이 남지 않았을 때만:

<완료>
무엇을 만들었고 어디에 있는지.
</완료>

## 규칙

- 아래 목록에 있는 도구만 부른다. 없는 것은 거부된다.
- 결과가 실패로 오면 **원인을 읽고 고쳐서** 다시 시도한다. 같은 호출을 반복하지 않는다.
- **회사 게이트**가 막으면(승인 대기·차단) 거기서 끝이다. 우회로를 찾지 말고
  <완료> 로 상황을 보고한다. 이건 위의 "네 도구가 꺼진 것"과 다른 일이다 —
  게이트는 태그를 낸 **뒤에** 판정한다.
- 파일 경로는 저장소 루트 기준 상대경로다.
- 산출물을 답변 본문에 적지 마라. 적을 곳은 파일이다.
"""

    def _act_tools(self) -> str:
        """이 에이전트가 부를 수 있는 도구와 인자. **선언된 것만** 보여준다."""
        lines = []
        for name in sorted(self.compiled.declared_tools):
            try:
                sk = self.skills.get(name)
            except Exception:
                # 선언은 됐는데 등록이 없다 — 부르면 어차피 실패한다. 목록에
                # 올려 두면 모델이 그걸 시도하느라 라운드를 태운다.
                continue
            if sk.run is None:
                continue                      # 실행부 미구현 (preview 전용)
            lines.append(f"- {name}({', '.join(sk.arg_names)})")
        return "\n".join(lines) or "- (없음)"

    @staticmethod
    def _parse_act(text: str) -> tuple[str, str, dict[str, str]]:
        """모델 답 → ("tool", 이름, 인자) 또는 ("done", 본문, {}).

        형식이 아니면 ("done", 원문, {}) 으로 본다 — 모델이 그냥 답을 쓴 것이고,
        그걸 오류로 만들면 멀쩡한 산출물이 버려진다.
        """
        import re

        m = re.search(r"<완료>(.*?)</완료>", text, re.S)
        if m:
            return "done", m.group(1).strip(), {}
        m = re.search(r'<도구\s+이름="([^"]+)"\s*>(.*?)</도구>', text, re.S)
        if not m:
            return "done", text.strip(), {}
        name, body = m.group(1).strip(), m.group(2)
        args = {a.group(1): a.group(2)
                for a in re.finditer(r'<인자\s+이름="([^"]+)"\s*>(.*?)</인자>', body, re.S)}
        return "tool", name, args

    def act(self, run: WorkerRun, task: str, ctx: str, *,
            touches_l3: bool = False, max_rounds: int = 8) -> str:
        """모델이 도구를 골라 가며 일한다. 게이트는 매 호출마다 걸린다.

        `max_rounds` 는 도구 예산(`max_tool_calls`)과 **다른 축**이다. 예산은
        "몇 번 만질 수 있나"고 이건 "몇 번 생각할 수 있나"다. 도구를 안 부르고
        말만 반복하는 경우가 있어서 둘 다 필요하다.
        """
        tools_txt = self._act_tools()
        transcript: list[str] = []
        for _ in range(max_rounds):
            prompt = (
                f"## 업무\n{task}\n\n"
                f"## EG 참조\n{ctx or '(없음)'}\n\n"
                f"## 쓸 수 있는 도구\n{tools_txt}\n\n"
                + ("## 지금까지\n" + "\n\n".join(transcript[-6:]) + "\n\n"
                   if transcript else "")
                + "다음 한 수를 내라."
            )
            reply = self.chat(run, prompt, touches_l3=touches_l3,
                              system_extra=self.ACT_SYSTEM)
            kind, name, args = self._parse_act(reply)
            if kind == "done":
                return name

            if name not in self.compiled.declared_tools:
                # 게이트도 막지만 여기서 먼저 끊는다 — 선언 밖 도구는 preview
                # 조차 만들 이유가 없고, 이유를 알려 줘야 다음 수가 나아진다.
                transcript.append(f"[{name}] 거부 — 이 에이전트에 선언되지 않은 도구다")
                continue

            try:
                decision, result = self.use_skill(run, name, **args)
            except CircuitBreaker as exc:
                return (f"도구 예산 소진 — {exc}\n\n"
                        + "\n\n".join(transcript[-4:]))

            if result is None:
                # block · require_hitl — **여기서 끝난다.** 우회를 허용하면
                # 게이트가 장식이 된다. 사람이 판단할 것을 남기고 보고한다.
                why = "; ".join(decision.reasons) or decision.decision
                return (f"게이트에서 멈췄다 — `{name}` {decision.decision}\n"
                        f"사유: {why}\n"
                        f"승인 큐: {run.hitl_requests[-1] if run.hitl_requests else '-'}\n\n"
                        + "\n\n".join(transcript[-4:]))

            head = f"[{name}] {'성공' if result.ok else '실패'}"
            body = (result.output if result.ok else result.error)[:1200]
            transcript.append(f"{head}\n{body}")

        return ("생각 한도(%d회)에 걸렸다. 지금까지:\n\n" % max_rounds
                + "\n\n".join(transcript[-4:]))

    # ── 모델 호출 ───────────────────────────────────────────────────────
    def resolve_model(self, *, touches_l3: bool) -> llm_mod.Resolved:
        """EG 가 고른 모델. **여기서 정책을 만들지 않는다 — 조회할 뿐.**"""
        policy_id = None
        if self.profile is not None and self.profile.models:
            models = sorted(self.profile.models, key=lambda m: m.id)  # 결정적
            local = [m for m in models if m.prop("cost_tier") == "local"]
            cloud = [m for m in models if m.prop("cost_tier") != "local"]
            # `force_local_when: [l3_data]` 는 "L3 를 만질 때" 라는 조건이지
            # 무조건이 아니다. 문자열을 그냥 넘기면 항상 참이 되어
            # 모든 조직이 로컬로 라우팅된다 — 조건을 실제로 판정한다.
            force_local = (
                touches_l3
                or self.compiled.gate.model_policy == "local_only"
                or (touches_l3 and self.compiled.gate.forces_local_model("l3_data"))
            )
            if force_local:
                # L3 — 로컬만. 없으면 resolve() 가 정책 위반으로 막는다.
                policy_id = local[0].id if local else models[0].id
            else:
                # 평시 — 로컬은 L3 전용으로 아껴 두고 배정된 일반 모델을 쓴다.
                policy_id = (cloud or local)[0].id
        return llm_mod.resolve(policy_id, touches_l3=touches_l3)

    def chat(
        self, run: WorkerRun, prompt: str, *, touches_l3: bool = False,
        max_tokens: int = 1500, system_extra: str = "",
    ) -> str:
        resolved = self.resolve_model(touches_l3=touches_l3)
        run.model, run.provider, run.model_policy = (
            resolved.model,
            resolved.provider,
            resolved.model_policy_id,
        )
        system = self.system_prompt()
        if system_extra:
            # SOUL 뒤에 붙인다 — 앞에 두면 도구 형식이 정체성보다 먼저 읽힌다.
            system = system + "\n\n---\n" + system_extra

        with self.tracer.span(
            OP_CHAT,
            **{
                "gen_ai.operation.name": OP_CHAT,
                "gen_ai.system": resolved.provider,
                "gen_ai.request.model": resolved.model,
                "gen_ai.request.max_tokens": max_tokens,
                "dawn.model.policy": resolved.model_policy_id,
                "dawn.model.local": resolved.is_local,
                "dawn.model.reason": resolved.reason,
            },
        ) as sp:
            content = self.tracer.content(prompt)
            if content:
                sp.event("gen_ai.user.message", content=content)
            t0 = time.monotonic()
            comp = self.client.complete(
                resolved, system=system, prompt=prompt, max_tokens=max_tokens
            )
            sp.set(
                **{
                    "gen_ai.response.model": comp.model,
                    "gen_ai.usage.input_tokens": comp.input_tokens,
                    "gen_ai.usage.output_tokens": comp.output_tokens,
                    "gen_ai.response.finish_reasons": comp.stop_reason,
                    "dawn.latency_ms": round((time.monotonic() - t0) * 1000),
                }
            )
            out = self.tracer.content(comp.text)
            if out:
                sp.event("gen_ai.choice", content=out)

        run.tokens_in += comp.input_tokens
        run.tokens_out += comp.output_tokens
        max_tok = self.budget.get("max_tokens")
        if max_tok and (run.tokens_in + run.tokens_out) > max_tok:
            raise CircuitBreaker(
                f"서킷 브레이커 — 토큰 {run.tokens_in + run.tokens_out} > 예산 {max_tok}"
            )
        self._step(run, "chat", f"{resolved.model} in={comp.input_tokens} out={comp.output_tokens}")
        return comp.text

    # ── ④ eg_record ────────────────────────────────────────────────────
    def eg_record(self, run: WorkerRun, kind: str, summary: str, detail: str = "") -> None:
        with self.tracer.span(
            OP_EXECUTE_TOOL,
            **{
                "gen_ai.tool.name": "eg.record",
                "gen_ai.operation.name": OP_EXECUTE_TOOL,
                "dawn.assets": ",".join(self.skills.get("eg.record").touches),
                "dawn.gate.evaluated": False,
            },
        ) as sp:
            res = self.skills.run("eg.record", kind=kind, summary=summary, detail=detail)
            sp.set(
                **{"dawn.eg.node": res.meta.get("node_id", ""), "dawn.gate.decision": "log_only"}
            )
        run.recorded = run.recorded or res.ok
        self._step(run, "record", f"{kind}: {res.output}", res.ok)

    # ── 전체 루프 ───────────────────────────────────────────────────────
    def run(
        self,
        task: str,
        *,
        touches_l3: bool | None = None,
        extra_skills: list[tuple[str, dict]] | None = None,
        purpose: str = "work",
        act: bool = False,
    ) -> WorkerRun:
        """4단계 루프를 한 번 돈다.

        Args:
            purpose: 이 실행이 **무엇인가** — `work`(실업무) · `drill`(리허설) ·
                `redteam`(공격 시뮬) · `demo`(시연). KPI 는 실업무만 센다.
                드릴·레드팀 run 은 일부러 차단되므로 ④ 에 도달하지 않고, 같이
                집계하면 성공률이 "일을 못 한다"로 읽힌다 (실측 41.9% 중 21건이
                레드팀이었다).
        """
        if purpose not in RUN_PURPOSES:
            raise ValueError(f"알 수 없는 run 목적: {purpose} — {sorted(RUN_PURPOSES)}")
        wr = WorkerRun(agent_id=self.agent_id, task=task, purpose=purpose)
        self._step_n = 0

        with self.tracer.span(
            OP_INVOKE_AGENT,
            **{
                "gen_ai.operation.name": OP_INVOKE_AGENT,
                "gen_ai.agent.id": self.agent_id,
                "gen_ai.agent.name": self.registry.agents[self.agent_id].data["name"],
                "dawn.team": self.compiled.team_id,
                "dawn.division": self.compiled.division_id,
                "dawn.eg_org": self.eg_org or "",
                "dawn.persona": self.compiled.persona,
                "dawn.autonomy": self.compiled.autonomy,
                "dawn.zone": self.compiled.zone or "",
            },
        ) as root:
            wr.trace_id = root.trace_id
            try:
                # ① eg_search — 착수 전 참조
                ctx = self.eg_search(wr, task[:80])

                # ②③ 업무 스킬 (호출자가 지정한 것)
                gathered: list[str] = []
                l3 = bool(touches_l3)
                for name, kwargs in extra_skills or []:
                    decision, result = self.use_skill(wr, name, **kwargs)
                    l3 = l3 or decision.touches_l3
                    if result is not None and result.ok:
                        gathered.append(f"### {name}\n{result.output[:3000]}")

                # 모델 호출 — L3 관여 여부가 라우팅을 바꾼다
                if act:
                    # 도구를 쓰며 일한다. 기본이 아닌 이유: 이 스위치를 켜면
                    # 모델이 파일을 쓰고 명령을 돌린다. 상시 작업·드릴·레드팀은
                    # 그럴 필요가 없고, 한꺼번에 바꾸면 영향 범위가 너무 넓다.
                    wr.output = self.act(wr, task, ctx, touches_l3=l3)
                else:
                    prompt = _build_prompt(task, ctx, gathered)
                    wr.output = self.chat(wr, prompt, touches_l3=l3)

                # ④ eg_record — 없으면 미완료
                # 이름은 **첫 줄만** 쓴다. 앞 80자를 그대로 쓰면 긴 지시문에서는
                # 머리말이 잘려 들어가 여러 에이전트의 기록이 전부 같은 이름이
                # 된다(실측 #65: 5건이 동일). EG 는 다음 작업의 참조 근거라,
                # 검색 결과가 구별되지 않으면 근거로 못 쓴다.
                headline = (task.strip().splitlines() or [""])[0][:80]
                self.eg_record(
                    wr,
                    "task",
                    f"[{self.agent_id}] {headline}",
                    (wr.output or "")[:2000],
                )
                root.set(
                    **{
                        "dawn.run.purpose": purpose,
                        "dawn.run.complete": wr.complete,
                        "dawn.run.tool_calls": wr.tool_calls,
                        "dawn.run.hitl": len(wr.hitl_requests),
                    }
                )
            except (CircuitBreaker, llm_mod.LLMError) as exc:
                wr.error = f"{type(exc).__name__}: {exc}"
                root.status = "ERROR"
                root.status_message = wr.error
                self._step(wr, "error", wr.error, False, enforce=False)
                # 중단해도 지금까지의 상태는 기록한다 (*_WORK.md §8 실패 시 처리)
                try:
                    res = self.skills.run(
                        "eg.record",
                        kind="observation",
                        summary=f"[{self.agent_id}] 중단: {task[:60]}",
                        detail=wr.error,
                    )
                    self._step(wr, "record", f"중단 기록: {res.output}", res.ok, enforce=False)
                except Exception:
                    pass
        return wr


def _build_prompt(task: str, eg_context: str, gathered: list[str]) -> str:
    parts = [
        "## 업무",
        task,
        "",
        "## EG 사전 참조 (eg_search 결과)",
        eg_context or "(관련 전례 없음)",
    ]
    if gathered:
        parts += ["", "## 수집한 자료 (skill_run 결과)", *gathered]
    parts += [
        "",
        "## 지시",
        "위 통제 평면(회사 헌법 → 팀 행동양식 → 업무 지침 → 개인 페르소나)과",
        "EG 프로파일을 그대로 따라 산출물을 작성하라.",
        "해당 업무 지침의 산출물 규격을 지키고, 근거 없는 단정을 쓰지 마라.",
    ]
    return "\n".join(parts)


_SENTENCE = re.compile(r"[.!?。\n]")


def summarize(text: str, n: int = 2) -> str:
    parts = [p.strip() for p in _SENTENCE.split(text) if p.strip()]
    return " ".join(parts[:n])[:300]
