"""dawn-agent — 에이전트 하네스 CLI.

dawn-agent info <agent-id>        이 에이전트가 무엇을 할 수 있나
dawn-agent run <agent-id> "업무"   워커 루프 1회
dawn-agent preview <agent> <skill> 게이트 판정만 (실행 안 함)
dawn-agent team <team-id> "목표"    팀 오케스트레이션
dawn-agent emit <event-type>       이벤트 발생 → 훅 기동
dawn-agent hitl [list|approve|deny] 승인 큐
dawn-agent trace [trace-id]        스팬 트리
"""

from __future__ import annotations

import argparse
import json
import sys

from dawn_core import Registry, jsonl
from dawn_core.paths import Paths

from .events import Event, default_dispatcher
from .hitl import ApprovalQueue
from .orchestrator import Assignment, TeamOrchestrator
from .worker import Worker

B, D, G, R, Y, Z = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _t(s: str, c: str) -> str:
    return f"{c}{s}{Z}" if sys.stdout.isatty() else s


# ── info ────────────────────────────────────────────────────────────────


def cmd_info(args) -> int:
    w = Worker(args.agent)
    print(f"{B}{w.registry.agents[args.agent].data['name']}{Z}  ({args.agent})")
    print(f"  팀/본부   {w.compiled.team_id} / {w.compiled.division_id}   EG={w.eg_org}")
    print(f"  페르소나  {w.compiled.persona}    자율화 {w.compiled.autonomy}")
    print(f"  통제 평면 {len(w.compiled.layers)}계층, 프롬프트 {len(w.system_prompt()):,}자")
    print(f"  예산      {w.budget}")
    try:
        r = w.resolve_model(touches_l3=False)
        ok, why = w.client.available(r)
        mark = _t("사용 가능", G) if ok else _t(f"불가 — {why}", R)
        print(f"  모델(평시) {r.model_policy_id} → {r.provider}/{r.model}  {mark}")
    except Exception as e:
        print(f"  모델(평시) {_t(str(e), R)}")
    try:
        r3 = w.resolve_model(touches_l3=True)
        print(f"  모델(L3)   {r3.model_policy_id} → {r3.provider}/{r3.model}")
    except Exception as e:
        print(f"  모델(L3)   {_t(type(e).__name__ + ': ' + str(e)[:90], Y)}")

    print(f"\n  {B}스킬 게이트 판정{Z}")
    for s in w.compiled.declared_tools:
        if s not in w.skills:
            print(f"    {D}· {s}  (스킬 미등록){Z}")
            continue
        d = w.gate.evaluate(w.skills.preview(s), declared_tools=w.compiled.declared_tools)
        print("    " + d.line())
    return 0


# ── preview ─────────────────────────────────────────────────────────────


def cmd_preview(args) -> int:
    w = Worker(args.agent)
    kwargs = json.loads(args.args) if args.args else {}
    pv = w.skills.preview(args.skill, **kwargs)
    d = w.gate.evaluate(pv, declared_tools=w.compiled.declared_tools)
    if args.json:
        print(
            json.dumps({"preview": pv.to_dict(), "gate": d.to_dict()}, ensure_ascii=False, indent=2)
        )
        return 0
    print(f"{B}skill_preview{Z}  {pv.line()}")
    for r in pv.reasons:
        print(f"    · {r}")
    print(f"\n{B}행동 게이트{Z}  {d.line()}")
    for k, v in d.sources.items():
        print(f"    {k:<16} {v}")
    if d.assets:
        print(
            f"    자산            {', '.join(d.assets)}  (심각도 {d.severity_label}/{d.severity})"
        )
    if d.policies:
        print(f"    걸린 정책       {', '.join(d.policies)}")
    return 1 if d.blocked else 0


# ── run ─────────────────────────────────────────────────────────────────


def _print_run(wr, verbose: bool = True) -> None:
    print(f"\n{B}워커 실행{Z}  {wr.agent_id}   trace={wr.trace_id}")
    if verbose:
        for s in wr.steps:
            print(s.line())
    mark = _t("완료", G) if wr.complete else _t("미완료", R)
    print(f"\n  결과      {mark}" + (f"   {_t(wr.error, R)}" if wr.error else ""))
    print(
        f"  모델      {wr.model_policy} → {wr.provider}/{wr.model}"
        f"   토큰 in={wr.tokens_in} out={wr.tokens_out}"
    )
    print(f"  도구 호출 {wr.tool_calls}   HITL {len(wr.hitl_requests)}   차단 {len(wr.blocked)}")
    if wr.hitl_requests:
        print(f"  승인 대기 {', '.join(wr.hitl_requests)}")
    if wr.output:
        print(f"\n{B}산출물{Z}\n{'-' * 60}\n{wr.output[:2500]}\n{'-' * 60}")


def cmd_run(args) -> int:
    w = Worker(args.agent)
    skills = [tuple(x) for x in json.loads(args.skills)] if args.skills else None
    wr = w.run(args.task, touches_l3=args.l3, extra_skills=skills)
    if args.json:
        print(json.dumps(wr.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_run(wr)
        print(f"\n{B}OTel 스팬 트리{Z}")
        print(w.tracer.tree())
    return 0 if wr.complete else 1


# ── team ────────────────────────────────────────────────────────────────


def cmd_team(args) -> int:
    reg = Registry.load()
    orch = TeamOrchestrator(args.team, registry=reg)
    members = reg.teams[args.team].agent_ids
    if not members:
        print(_t(f"{args.team} 에 에이전트가 없다", R), file=sys.stderr)
        return 2
    assignments = [Assignment(agent_id=members[0], task=args.goal, phase="P1")]
    if len(members) > 1:
        assignments.append(
            Assignment(
                agent_id=members[1],
                task="위 산출물을 검증하라",
                phase="P4",
                depends_on=[members[0]],
                role="verifier",
            )
        )
    viol = TeamOrchestrator.check_separation(assignments)
    if viol:
        print(_t(f"검증자 ≠ 생산자 위반: {viol}", R), file=sys.stderr)
        return 2

    tr = orch.delegate(args.goal, assignments)
    if args.json:
        print(json.dumps(tr.to_dict(), ensure_ascii=False, indent=2))
        return 0 if tr.complete else 1
    print(f"{B}팀 오케스트레이션{Z}  {tr.team_id}  trace={tr.trace_id}")
    print(f"  {D}리더는 무발화 — 라우팅·통합만 한다{Z}")
    print(f"  위임 순서 {' → '.join(tr.order) or '(없음)'}")
    if tr.skipped:
        print(f"  건너뜀    {', '.join(tr.skipped)}")
    for r in tr.runs.values():
        _print_run(r, verbose=False)
    return 0 if tr.complete else 1


# ── emit ────────────────────────────────────────────────────────────────


def cmd_emit(args) -> int:
    d = default_dispatcher()
    payload = json.loads(args.payload) if args.payload else {}
    ev = Event(type=args.event, source=args.source, payload=payload)
    hs = d.handlers_for(ev.type)
    print(f"{B}이벤트{Z} {ev.type}  id={ev.id}  → 핸들러 {len(hs)}개")
    if not hs:
        print(f"  {D}등록된 핸들러 없음 — 아무 일도 일어나지 않는다 (상시 폴링 아님){Z}")
        return 0
    if args.dry_run:
        for h in hs:
            print(f"  · {h.work_id} → {h.agent_id}")
            print(f"    task: {h.build_task(ev)[:160]}")
        return 0

    rc = 0
    for h in hs:
        print(f"\n  ▸ {h.work_id} → {h.agent_id}")
        w = Worker(h.agent_id)
        wr = w.run(
            h.build_task(ev),
            touches_l3=h.touches_l3,
            extra_skills=h.build_skills(ev) if h.build_skills else None,
        )
        _print_run(wr, verbose=True)
        rc = rc or (0 if wr.complete else 1)
    return rc


# ── hitl ────────────────────────────────────────────────────────────────


def cmd_hitl(args) -> int:
    q = ApprovalQueue(Paths().root)
    if args.action == "list":
        items = q.list(args.status, purpose=args.purpose)
        if args.json:
            print(json.dumps([a.to_dict() for a in items], ensure_ascii=False, indent=2))
            return 0
        c = q.counts()
        print(f"{B}HITL 승인 큐{Z}  ({len(items)}건)"
              + (f"  {D}대기: " + " · ".join(f"{k} {v}" for k, v in sorted(c.items()))
                 + f"{Z}" if c else ""))
        for a in items:
            print("  " + a.line())
            for r in a.reasons[:3]:
                print(f"      {D}· {r}{Z}")
        return 0
    if args.action in ("approve", "deny"):
        if not args.id:
            print("승인 id 가 필요하다", file=sys.stderr)
            return 2
        before = q.get(args.id)
        if before.run_ended:
            # 사람이 눌러 놓고 집행됐다고 믿는 것이 가장 나쁘다.
            print(_t("이 요청을 낸 실행은 이미 끝났다 — 승인해도 그 행동은 "
                     "집행되지 않는다.", Y))
            print(f"  {D}판단은 기록으로 남는다. 그 행동이 지금 필요하면 "
                  f"작업을 다시 돌려라.{Z}")
        a = q.decide(args.id, approve=args.action == "approve", by=args.by, note=args.note or "")
        print(_t(f"✔ {a.id} → {a.status} (by {a.decided_by})", G))
        return 0
    if args.action == "expire":
        if not args.note:
            print("--note 로 만료 사유를 적어라 (거부가 아니라 만료다)", file=sys.stderr)
            return 2
        done = q.expire(purpose=args.purpose, note=args.note)
        print(_t(f"⌛ {len(done)}건 만료 (승인도 거부도 아니다)", G))
        return 0
    if args.action == "clear":
        print(f"{q.clear()}건 삭제")
        return 0
    return 2


# ── trace ───────────────────────────────────────────────────────────────


def cmd_trace(args) -> int:
    root = Paths().root
    d = root / "var" / "traces"
    if not d.is_dir():
        print("트레이스 없음")
        return 0
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("트레이스 없음")
        return 0
    target = (
        next((f for f in files if f.stem == args.trace_id), files[0]) if args.trace_id else files[0]
    )
    spans = jsonl.read(target)
    if args.json:
        print(json.dumps(spans, ensure_ascii=False, indent=2))
        return 0

    print(
        f"{B}트레이스{Z} {target.stem}   스팬 {len(spans)}개   {D}{target.relative_to(root)}{Z}\n"
    )
    by_parent: dict[str | None, list[dict]] = {}
    for s in spans:
        by_parent.setdefault(s.get("parent_span_id"), []).append(s)

    def walk(parent, depth):
        for s in sorted(by_parent.get(parent, []), key=lambda x: x["start_ns"]):
            a = s["attributes"]
            mark = {"OK": _t("●", G), "ERROR": _t("✘", R)}.get(s["status"], "○")
            extra = ""
            if s["name"] == "chat":
                extra = (
                    f"  {a.get('gen_ai.request.model', '?')}"
                    f"  in={a.get('gen_ai.usage.input_tokens', 0)}"
                    f" out={a.get('gen_ai.usage.output_tokens', 0)}"
                )
            elif s["name"] == "execute_tool":
                extra = (
                    f"  {a.get('gen_ai.tool.name', '?')}  gate={a.get('dawn.gate.decision', '-')}"
                )
            elif s["name"] == "invoke_agent":
                extra = f"  {a.get('gen_ai.agent.name', '')}"
            print(f"{'  ' * depth}{mark} {s['name']}{extra}  {D}{s['duration_ms']}ms{Z}")
            for ev in s.get("events", []):
                print(f"{'  ' * (depth + 1)}{D}↳ {ev['name']}{Z}")
            walk(s["span_id"], depth + 1)

    walk(None, 0)
    return 0


# ── parser ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn-agent", description="에이전트 하네스")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("info", help="에이전트 능력·게이트 조회")
    x.add_argument("agent")
    x.set_defaults(func=cmd_info)

    x = s.add_parser("preview", help="스킬 게이트 판정 (실행 안 함)")
    x.add_argument("agent")
    x.add_argument("skill")
    x.add_argument("--args", default=None, help='JSON, 예: \'{"path":"README.md"}\'')
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_preview)

    x = s.add_parser("run", help="워커 루프 1회")
    x.add_argument("agent")
    x.add_argument("task")
    x.add_argument("--skills", default=None, help='JSON 배열, 예: \'[["fs.read",{"path":"x"}]]\'')
    x.add_argument("--l3", action="store_true", help="L3 자산 관여로 가정")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_run)

    x = s.add_parser("team", help="팀 오케스트레이션")
    x.add_argument("team")
    x.add_argument("goal")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_team)

    x = s.add_parser("emit", help="이벤트 발생 → 훅 기동")
    x.add_argument("event")
    x.add_argument("--source", default="manual")
    x.add_argument("--payload", default=None)
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(func=cmd_emit)

    x = s.add_parser("hitl", help="승인 큐")
    x.add_argument(
        "action", choices=["list", "approve", "deny", "expire", "clear"],
        default="list", nargs="?"
    )
    x.add_argument("--purpose", default=None,
                   help="이 목적만 (work|drill|redteam|demo|unknown)")
    x.add_argument("id", nargs="?")
    x.add_argument("--status", default=None)
    x.add_argument("--by", default="human")
    x.add_argument("--note", default=None)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_hitl)

    x = s.add_parser("trace", help="OTel 스팬 트리")
    x.add_argument("trace_id", nargs="?")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_trace)
    return p


def _load_dotenv() -> None:
    """.env 를 환경변수로 (이미 설정된 값은 덮지 않는다). 시크릿은 파일에만."""
    import os

    env = Paths().root / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(_t(f"✘ {type(exc).__name__}: {exc}", R), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
