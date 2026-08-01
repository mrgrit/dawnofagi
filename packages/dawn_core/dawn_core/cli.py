"""dawn — 조직 레지스트리와 통제 평면 CLI.

dawn registry             레지스트리 검증 + 요약
dawn registry --tree      조직도 트리
dawn compile <agent-id>   에이전트 통제 평면 컴파일 (프롬프트/번들 출력)
dawn compile --all --write  전 에이전트 번들을 var/control-plane/ 에 생성
dawn lint                 Control Readiness Score
dawn gate <agent-id>      실효 게이트 조회
"""

from __future__ import annotations

import argparse
import json
import sys

from .control_plane import CompileError, compile_agent, compile_all, write_bundles
from .eg.cli import add_subparser as add_eg_subparser
from .eg.store import EGError
from .lint import format_report
from .lint import run as run_lint
from .registry import Registry, RegistryError

C_OK, C_ERR, C_DIM, C_RST = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _tty(s: str, color: str) -> str:
    return f"{color}{s}{C_RST}" if sys.stdout.isatty() else s


# ── registry ────────────────────────────────────────────────────────────


def cmd_registry(args) -> int:
    reg = Registry.load(args.root)
    if args.json:
        print(json.dumps(reg.summary(), ensure_ascii=False, indent=2))
        return 0

    s = reg.summary()
    print(_tty("✔ 레지스트리 정합성 OK", C_OK))
    print(f"  사업   {s['businesses']:>3}  {s['businesses_by_status']}")
    print(f"  본부   {s['divisions']:>3}")
    print(f"  팀     {s['teams']:>3}")
    print(f"  에이전트 {s['agents']:>3}  (활성 {s['agents_active']})")
    print(f"  업무   {s['works']:>3}")

    if args.tree:
        print()
        for bid in sorted(reg.businesses):
            b = reg.businesses[bid]
            mark = "●" if b.is_live else "○"
            print(
                f"{mark} 사업 {b.data['name']} [{b.status}] → 본부 {', '.join(b.data['owning_divisions'])}"
            )
        print()
        for did in sorted(reg.divisions):
            d = reg.divisions[did]
            print(f"■ {d.data['name']} ({did})  zone={d.data.get('zone', '-')}")
            for tid in d.data.get("teams", []):
                t = reg.teams[tid]
                n = len(t.agent_ids)
                badge = f"{n} agent" if n else _tty("dormant", C_DIM)
                print(f"  └─ {t.data['name']} ({tid})  [{badge}]")
                for aid in t.agent_ids:
                    a = reg.agents[aid]
                    print(
                        f"       · {aid}  role={a.data['role']} persona={a.data['persona']} "
                        f"autonomy={a.data['autonomy']}"
                    )
    return 0


# ── compile ─────────────────────────────────────────────────────────────


def cmd_compile(args) -> int:
    reg = Registry.load(args.root)

    if args.all:
        ok, failed = compile_all(reg)
        for aid in sorted(ok):
            c = ok[aid]
            print(
                _tty(f"✔ {aid}", C_OK) + f"  layers={len(c.layers)} tools={len(c.declared_tools)} "
                f"autonomy={c.gate.autonomy} model={c.gate.model_policy}"
            )
            for w in c.warnings:
                print(f"    {_tty('!', C_DIM)} {w}")
        for aid, msg in failed.items():
            print(_tty(f"✘ {aid}", C_ERR))
            print("\n".join(f"   {ln}" for ln in msg.splitlines()[1:]))
        if args.write and not failed:
            written = write_bundles(reg)
            print(f"\n번들 {len(written)}개 → {reg.paths.build_dir}")
        return 1 if failed else 0

    if not args.agent:
        print("에이전트 id 를 주거나 --all 을 쓰라", file=sys.stderr)
        return 2

    c = compile_agent(reg, args.agent)
    if args.prompt:
        print(c.system_prompt())
    else:
        print(json.dumps(c.bundle(), ensure_ascii=False, indent=2))
    return 0


# ── gate ────────────────────────────────────────────────────────────────


def cmd_gate(args) -> int:
    reg = Registry.load(args.root)
    c = compile_agent(reg, args.agent)
    g = c.gate
    print(f"에이전트 {args.agent}  (팀 {c.team_id} · 본부 {c.division_id})")
    print(f"  게이트 출처: {' → '.join(g.sources)}")
    print(f"  자율화     : {g.autonomy}")
    print(
        f"  모델 정책  : {g.model_policy}"
        + (f" (강제 로컬: {', '.join(sorted(g.force_local_when))})" if g.force_local_when else "")
    )
    print(f"  예산       : {g.budget}")
    print(f"  HITL 조건  : {', '.join(sorted(g.hitl_require_on))}")
    if g.amount_threshold_krw is not None:
        print(f"  금액 임계  : {g.amount_threshold_krw:,.0f}원")
    print(f"  실효 도구 ({len(c.declared_tools)}):")
    for t in c.declared_tools:
        print(f"    + {t}")
    print(f"  허용 패턴 : {', '.join(sorted(g.allow))}")
    print(f"  차단 패턴 : {', '.join(sorted(g.deny))}")
    return 0


# ── lint ────────────────────────────────────────────────────────────────


def cmd_lint(args) -> int:
    reg = Registry.load(args.root)
    rep = run_lint(reg)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(rep))
    return 0 if rep.passed else 1


# ── entry ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn", description="the dawn of AGI — 레지스트리·통제 평면")
    p.add_argument("--root", default=None, help="모노레포 루트 (기본: 자동 탐색)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("registry", help="레지스트리 검증 + 요약")
    r.add_argument("--tree", action="store_true", help="조직도 트리 출력")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_registry)

    c = sub.add_parser("compile", help="통제 평면 컴파일")
    c.add_argument("agent", nargs="?")
    c.add_argument("--all", action="store_true")
    c.add_argument("--write", action="store_true", help="var/control-plane/ 에 번들 생성")
    c.add_argument("--prompt", action="store_true", help="번들 대신 시스템 프롬프트 출력")
    c.set_defaults(func=cmd_compile)

    g = sub.add_parser("gate", help="실효 게이트 조회")
    g.add_argument("agent")
    g.set_defaults(func=cmd_gate)

    lt = sub.add_parser("lint", help="Control Readiness Score")
    lt.add_argument("--json", action="store_true")
    lt.set_defaults(func=cmd_lint)

    add_eg_subparser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RegistryError, CompileError, EGError) as exc:
        print(_tty("✘ " + str(exc), C_ERR), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
