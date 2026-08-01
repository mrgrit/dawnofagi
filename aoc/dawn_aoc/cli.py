"""dawn-aoc — 관제 콘솔 CLI.

    dawn-aoc collect            수집·정규화 (스팬 → run), PII 재검증
    dawn-aoc scan [--judge]     탐지 → 트리아지 → 케이스 생성
    dawn-aoc cases              케이스 목록
    dawn-aoc respond <case-id>  대응 플레이북 집행 (비가역은 승인 큐로)
    dawn-aoc control            킬 스위치 상태 / pause·kill·isolate·resume
    dawn-aoc kpi                KPI 대시보드 + 자율화 검토
    dawn-aoc guard <input|output> "텍스트"   동기 가드레일 단독 실행
    dawn-aoc replay <trace-id>  타임라인 리플레이 (사후 재구성)
    dawn-aoc serve              픽셀 오피스 + 상태 API
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dawn_agents import load_dotenv
from dawn_core.paths import Paths

from . import console as console_mod
from . import kpi as kpi_mod
from .collect import TraceLake
from .detect import input_gate, output_gate
from .killswitch import KillSwitch
from .respond import Responder
from .triage import PLAYBOOKS, CaseStore

B, D, G, R, Y, Z = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _t(s: str, c: str) -> str:
    return f"{c}{s}{Z}" if sys.stdout.isatty() else s


def _root() -> Path:
    return Paths().root


# ── collect ─────────────────────────────────────────────────────────────


def cmd_collect(args) -> int:
    lake = TraceLake(_root())
    runs = lake.all_runs(limit=args.limit)
    p = lake.persist(runs)
    st = lake.stats(runs)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}수집 계층{Z}  semconv {st['semconv_version']}")
    print(f"  트레이스 {st['traces']}  ·  run {st['runs']}  ·  스팬 {st['spans']}")
    print(f"  토큰     {st['tokens']:,}")
    gd = ", ".join(f"{k}={v}" for k, v in sorted(st["gate_decisions"].items()))
    print(f"  게이트   {gd or '(없음)'}")
    print(f"  에이전트 {', '.join(st['agents']) or '(없음)'}")
    mv = st["masking_violations"]
    print(f"  PII 마스킹 위반  {_t(str(mv), R if mv else G)}"
          + ("   ← 수집 계층이 유출을 잡았다" if mv else "   (재검증 통과)"))
    print(f"  → {p.relative_to(_root())}")
    return 0


# ── scan ────────────────────────────────────────────────────────────────


def cmd_scan(args) -> int:
    res = console_mod.scan(_root(), with_judge=args.judge, limit=args.limit)
    if args.json:
        out = {k: v for k, v in res.items() if not k.startswith("_")}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}탐지·트리아지{Z}  run {res['runs_scanned']}건 스캔")
    for jr in res.get("judged", {}).values():
        v = jr.get("verdict", "?")
        mark = _t("fail", R) if v == "fail" else _t(v, G)
        print(f"  judge[{jr.get('judge_model', '')}]  {mark}  "
              f"근거={jr.get('groundedness')} 완결={jr.get('completeness')} "
              f"궤적={jr.get('trajectory')}"
              + (f"  {_t(jr['error'], Y)}" if jr.get("error") else ""))
        for i in jr.get("issues", [])[:3]:
            print(f"      {D}· {i}{Z}")
    if not res["new_cases"]:
        print(f"  {D}새 케이스 없음{Z}")
        return 0
    print(f"\n  {B}새 케이스 {len(res['new_cases'])}건{Z}")
    store = CaseStore(_root())
    for c in res["new_cases"]:
        case = store.get(c["id"])
        print("  " + case.line())
        for d in case.detections[:4]:
            print(f"      {D}· [{d['detector']}] {d['summary'][:80]}{Z}")
        print(f"      권고: {', '.join(case.recommended) or '(없음)'}")
    return 0


# ── cases ───────────────────────────────────────────────────────────────


def cmd_cases(args) -> int:
    store = CaseStore(_root())
    cases = store.list(args.status)
    if args.json:
        print(json.dumps([c.to_dict() for c in cases], ensure_ascii=False, indent=2))
        return 0
    if args.id:
        c = store.get(args.id)
        print(f"{B}{c.id}{Z}  [{c.axis}] {c.severity}  {c.agent_id}")
        print(f"  심각도  {c.severity_label}/{c.severity_score}  "
              f"(자산 {', '.join(c.assets) or '-'})")
        print(f"  정책    {', '.join(c.policies) or '-'}")
        print(f"  트레이스 {c.trace_id}")
        print(f"\n  {B}탐지 {len(c.detections)}건{Z}")
        for d in c.detections:
            print(f"    · [{d['detector']}] {d['severity']:<8} {d['summary']}")
            if d.get("evidence"):
                print(f"        증거: {d['evidence'][:100]}")
            if d.get("framework"):
                print(f"        {D}{d['framework']}{Z}")
        print(f"\n  권고    {', '.join(c.recommended)}")
        if c.actions:
            print(f"  {B}집행 이력{Z}")
            for a in c.actions:
                print(f"    {'✔' if a['executed'] else '✋'} {a['playbook']}  "
                      f"{a.get('detail') or a.get('reason', '')}")
        return 0
    print(f"{B}케이스{Z} ({len(cases)}건)")
    for c in cases:
        print("  " + c.line())
    return 0


# ── respond ─────────────────────────────────────────────────────────────


def cmd_respond(args) -> int:
    store = CaseStore(_root())
    case = store.get(args.case_id)
    r = Responder(_root())
    pbs = args.playbooks.split(",") if args.playbooks else None
    results = r.execute(case, pbs, by=args.by)
    store.save(case)
    if args.json:
        print(json.dumps([x.to_dict() for x in results], ensure_ascii=False, indent=2))
        return 0
    print(f"{B}대응 집행{Z}  {case.id}  [{case.severity}] {case.title[:60]}")
    for x in results:
        print(x.line())
    print(f"\n  {D}가역 액션은 즉시 집행, 비가역은 승인 큐로 (사람이 누르기 전엔 집행 안 함){Z}")
    return 0


# ── control (kill switch) ───────────────────────────────────────────────


def cmd_control(args) -> int:
    ks = KillSwitch(_root())
    if args.action == "list":
        states = ks.all()
        if args.json:
            print(json.dumps([s.to_dict() for s in states], ensure_ascii=False, indent=2))
            return 0
        print(f"{B}제어 계층 (킬 스위치){Z}  — 에이전트가 수정할 수 없다")
        if not states:
            print(f"  {D}모든 에이전트 running (제어 개입 없음){Z}")
        for s in states:
            print("  " + s.line())
        return 0

    if not args.agent:
        print("에이전트 id 가 필요하다", file=sys.stderr)
        return 2
    reason = args.reason or f"{args.action} by {args.by}"
    fn = {"pause": ks.pause, "kill": ks.kill, "isolate": ks.isolate,
          "revoke": ks.revoke_credentials}.get(args.action)
    if fn:
        st = fn(args.agent, reason=reason, by=args.by, case_id=args.case_id or "")
    elif args.action == "resume":
        st = ks.resume(args.agent, by=args.by, reason=reason)
    else:
        return 2
    print(_t("✔ " + st.line(), G))
    return 0


# ── kpi ─────────────────────────────────────────────────────────────────


def cmd_kpi(args) -> int:
    st = console_mod.build_state(_root())
    if args.json:
        print(json.dumps({"kpis": st["kpis"], "autonomy_reviews": st["autonomy_reviews"]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{B}KPI 대시보드{Z}   {st['generated_at']}")
    print(f"  {D}COMPANY.md §3 의 목표와 1:1 대응{Z}\n")
    for k in st["kpis"]:
        obj = kpi_mod.KPI(**{x: k[x] for x in
                             ("name", "value", "unit", "direction", "target", "sample", "note")})
        print(obj.line())
        if obj.note:
            print(f"      {D}{obj.note}{Z}")
    print(f"\n{B}자율화 등급 검토{Z}  {D}승급은 KPI 충족 시에만 · 강등은 인시던트 즉시{Z}")
    for rv in st["autonomy_reviews"]:
        obj = kpi_mod.AutonomyReview(**rv)
        print(obj.line())
    return 0


# ── guard ───────────────────────────────────────────────────────────────


def cmd_guard(args) -> int:
    fn = input_gate if args.gate == "input" else output_gate
    res = fn(args.text)
    if args.json:
        print(json.dumps({"gate": res.gate, "passed": res.passed,
                          "detections": [d.to_dict() for d in res.detections],
                          "sanitized": res.sanitized}, ensure_ascii=False, indent=2))
        return 0 if res.passed else 1
    mark = _t("통과", G) if res.passed else _t("차단", R)
    print(f"{B}{res.gate} 게이트{Z}  {mark}")
    for d in res.detections:
        print("  " + d.line())
        if d.evidence:
            print(f"      증거: {d.evidence}")
        print(f"      {D}{d.framework}{Z}")
    if res.sanitized and res.sanitized != args.text:
        print(f"\n  {B}정화된 출력{Z}\n  {res.sanitized[:300]}")
    return 0 if res.passed else 1


# ── replay ──────────────────────────────────────────────────────────────


def cmd_replay(args) -> int:
    lake = TraceLake(_root())
    tid = args.trace_id or (lake.trace_ids()[:1] or [""])[0]
    if not tid:
        print("트레이스가 없다", file=sys.stderr)
        return 1
    spans = sorted(lake.spans(tid), key=lambda s: s["start_ns"])
    if args.json:
        print(json.dumps(spans, ensure_ascii=False, indent=2))
        return 0
    t0 = spans[0]["start_ns"]
    print(f"{B}타임라인 리플레이{Z}  {tid}   스팬 {len(spans)}개")
    print(f"  {D}EU AI Act 12조 — 사후 재구성{Z}\n")
    for s in spans:
        a = s["attributes"]
        off = (s["start_ns"] - t0) / 1e6
        mark = {"OK": _t("●", G), "ERROR": _t("✘", R)}.get(s["status"], "○")
        label = s["name"]
        if s["name"] == "execute_tool":
            label = f"{a.get('gen_ai.tool.name', '?')}  gate={a.get('dawn.gate.decision', '-')}"
        elif s["name"] == "chat":
            label = (f"chat {a.get('gen_ai.request.model', '?')} "
                     f"in={a.get('gen_ai.usage.input_tokens', 0)} "
                     f"out={a.get('gen_ai.usage.output_tokens', 0)}")
        elif s["name"] == "invoke_agent":
            label = f"invoke_agent {a.get('gen_ai.agent.name', '')}"
        print(f"  +{off:>9.0f}ms  {mark} {label}   {D}{s['duration_ms']}ms{Z}")
        for ev in s.get("events", []):
            c = ev.get("attributes", {}).get("content", "")
            extra = f" — {str(c)[:70]}" if c else ""
            print(f"              {D}↳ {ev['name']}{extra}{Z}")
    return 0


# ── serve ───────────────────────────────────────────────────────────────


def cmd_serve(args) -> int:
    import http.server

    root = _root()
    console_mod.write_state(root)
    app_dir = root / "apps" / "pixel-office"
    state_path = root / "var" / "aoc" / "state.json"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(app_dir), **kw)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                console_mod.write_state(root)          # 요청 시 갱신 (폴링 아님)
                body = state_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/trace/"):
                tid = self.path.rsplit("/", 1)[-1]
                spans = sorted(TraceLake(root).spans(tid), key=lambda s: s["start_ns"])
                body = json.dumps(spans, ensure_ascii=False).encode()
                self.send_response(200 if spans else 404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, fmt, *a):  # 조용히
            pass

    # ThreadingHTTPServer: SO_REUSEADDR 를 켜준다(재기동 시 TIME_WAIT 로 안 막힘) +
    # /api/state 계산이 정적 파일 응답을 막지 않는다.
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"{B}픽셀 오피스{Z}  http://localhost:{args.port}/")
        print("  상태 API   /api/state   ·  트레이스 /api/trace/<id>")
        print(f"  {D}Ctrl-C 로 종료{Z}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료")
    return 0


def cmd_state(args) -> int:
    p = console_mod.write_state(_root())
    st = json.loads(p.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}콘솔 상태{Z} → {p.relative_to(_root())}")
    print(f"  본부 {len(st['divisions'])} · 존 {len(st['zones'])} · "
          f"에이전트 {len(st['agents'])} · run {len(st['runs'])} · "
          f"케이스 {len(st['cases'])} · 제어 {len(st['control'])}")
    return 0


# ── parser ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn-aoc", description="AOC 관제 콘솔")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("collect", help="수집·정규화 + PII 재검증")
    x.add_argument("--limit", type=int, default=200)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_collect)

    x = s.add_parser("scan", help="탐지 → 트리아지")
    x.add_argument("--judge", action="store_true", help="LLM-judge 까지 (모델 호출)")
    x.add_argument("--limit", type=int, default=50)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_scan)

    x = s.add_parser("cases", help="케이스")
    x.add_argument("id", nargs="?")
    x.add_argument("--status", default=None)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_cases)

    x = s.add_parser("respond", help="대응 플레이북 집행")
    x.add_argument("case_id")
    x.add_argument("--playbooks", default=None,
                   help=f"쉼표 구분. 가능: {','.join(PLAYBOOKS)}")
    x.add_argument("--by", default="aoc")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_respond)

    x = s.add_parser("control", help="킬 스위치")
    x.add_argument("action", nargs="?", default="list",
                   choices=["list", "pause", "kill", "isolate", "revoke", "resume"])
    x.add_argument("agent", nargs="?")
    x.add_argument("--reason", default=None)
    x.add_argument("--by", default="aoc")
    x.add_argument("--case-id", default=None)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_control)

    x = s.add_parser("kpi", help="KPI + 자율화 검토")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_kpi)

    x = s.add_parser("guard", help="동기 가드레일 단독 실행")
    x.add_argument("gate", choices=["input", "output"])
    x.add_argument("text")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_guard)

    x = s.add_parser("replay", help="타임라인 리플레이")
    x.add_argument("trace_id", nargs="?")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_replay)

    x = s.add_parser("state", help="콘솔 상태 스냅샷 생성")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_state)

    x = s.add_parser("serve", help="픽셀 오피스 + 상태 API")
    x.add_argument("--port", type=int, default=8800)
    x.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(_t(f"✘ {type(exc).__name__}: {exc}", R), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
