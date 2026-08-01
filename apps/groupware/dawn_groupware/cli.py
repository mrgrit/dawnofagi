"""dawn-web — 홈페이지·그룹웨어 CLI.

    dawn-web site                 공개 홈페이지 (L0, dmz 앞단)
    dawn-web portal               사내 그룹웨어 (user/int 존)
    dawn-web useradd <계정>        계정 생성
    dawn-web usermod <계정>        권한 변경 / 비밀번호 재설정 / 사용 중지
    dawn-web users                계정 목록
    dawn-web bootstrap            첫 관리자 계정 생성 (비밀번호 자동 생성, 1회 출력)
    dawn-web resetpw [계정]        비밀번호 임의 재발급 (1회 출력)
    dawn-web audit                감사 로그
    dawn-web inquiries            홈페이지 문의 접수함
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

from dawn_agents import load_dotenv
from dawn_core import jsonl
from dawn_core.paths import Paths

from .audit import AuditLog
from .auth import CAPABILITIES, UserStore

B, D, G, R, Y, Z = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _root() -> Path:
    return Paths().root


def _t(s: str, c: str) -> str:
    return f"{c}{s}{Z}" if sys.stdout.isatty() else s


# ── 서버 ────────────────────────────────────────────────────────────────


def _serve(app, host: str, port: int, label: str, note: str = "") -> int:
    import uvicorn

    print(f"{B}{label}{Z}", flush=True)
    if host in ("127.0.0.1", "localhost"):
        print(f"  http://localhost:{port}/   {D}(로컬 전용 — 외부는 --host 0.0.0.0){Z}",
              flush=True)
    else:
        print(f"  {B}http://{_lan_ip()}:{port}/{Z}   ← 브라우저에서 여기로", flush=True)
    if note:
        print(f"  {D}{note}{Z}", flush=True)
    print(f"  {D}Ctrl-C 로 종료{Z}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    return 0


def _lan_ip() -> str:
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "<호스트 IP>"


def cmd_site(args) -> int:
    from .app import build_site

    return _serve(build_site(_root()), args.host, args.port,
                  "공개 홈페이지 (L0)",
                  "공개 자산이다 — 사내 데이터·EG·계정에 접근하지 않는다")


def cmd_portal(args) -> int:
    from .app import build_portal

    app = build_portal(_root(), tenant=args.tenant, office_url=args.office_url)
    return _serve(app, args.host, args.port, "사내 그룹웨어",
                  "승인 큐 · EG 조정 · 관제 — 인증 필요")


# ── 계정 ────────────────────────────────────────────────────────────────


def _read_password(prompt: str = "비밀번호: ") -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    pw = getpass.getpass(prompt)
    again = getpass.getpass("한 번 더: ")
    if pw != again:
        raise SystemExit("비밀번호가 일치하지 않는다")
    return pw


def cmd_useradd(args) -> int:
    users = UserStore(_root())
    pw = args.password or _read_password()
    caps = args.capabilities.split(",") if args.capabilities else ["portal.view"]
    u = users.create(args.username, pw, name=args.name or args.username,
                     org=args.org, title=args.title, tenant=args.tenant,
                     capabilities=[c.strip() for c in caps if c.strip()])
    AuditLog(_root()).write("admin.user.create", actor="cli", target=u.username,
                            org=u.org, capabilities=u.capabilities)
    print(_t(f"✔ 계정 생성  {u.username}  {u.org}  [{', '.join(u.capabilities)}]", G))
    return 0


def cmd_usermod(args) -> int:
    users = UserStore(_root())
    if users.get(args.username) is None:
        print(f"없는 계정: {args.username}", file=sys.stderr)
        return 1
    if args.capabilities is not None:
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        users.set_capabilities(args.username, caps)
        print(_t(f"✔ 권한  {args.username} → {', '.join(caps)}", G))
    if args.password is not None:
        users.set_password(args.username, args.password or _read_password())
        print(_t(f"✔ 비밀번호 변경  {args.username}", G))
    if args.disable:
        users.set_disabled(args.username, True)
        print(_t(f"✔ 사용 중지  {args.username}", Y))
    if args.enable:
        users.set_disabled(args.username, False)
        print(_t(f"✔ 사용 재개  {args.username}", G))
    AuditLog(_root()).write("admin.user.update", actor="cli", target=args.username)
    return 0


def cmd_users(args) -> int:
    users = UserStore(_root()).list(tenant=args.tenant)
    if args.json:
        print(json.dumps([u.public() for u in users], ensure_ascii=False, indent=2))
        return 0
    print(f"{B}계정{Z} (테넌트 {args.tenant})")
    if not users:
        print(f"  {D}없다 — dawn-web bootstrap 으로 첫 관리자를 만들어라{Z}")
    for u in users:
        mark = _t("중지", R) if u.disabled else _t("정상", G)
        print(f"  {u.username:<16} {u.name:<12} {u.org:<16} {mark}  "
              f"{D}{', '.join(u.capabilities)}{Z}")
    return 0


def cmd_bootstrap(args) -> int:
    """첫 관리자. 비밀번호를 만들어 **한 번만** 출력한다 — 어디에도 저장하지 않는다."""
    users = UserStore(_root())
    if users.list() and not args.force:
        print("이미 계정이 있다. 그래도 만들려면 --force", file=sys.stderr)
        return 1
    pw = args.password or secrets.token_urlsafe(12)
    u = users.create(args.username, pw, name=args.name or args.username, org=args.org,
                     title="관리자", capabilities=list(CAPABILITIES))
    AuditLog(_root()).write("admin.bootstrap", actor="cli", target=u.username, org=u.org)
    print(f"{B}첫 관리자 계정{Z}")
    print(f"  계정      {u.username}")
    print(f"  조직      {u.org}")
    print(f"  비밀번호  {_t(pw, Y)}")
    print(f"  {D}이 비밀번호는 여기서만 보인다. 저장하지 않았다. 로그인 후 바꿔라.{Z}")
    print(f"  {D}권한: {', '.join(u.capabilities)}{Z}")
    return 0


def cmd_resetpw(args) -> int:
    """비밀번호를 임의로 재발급하고 **한 번만** 출력한다.

    테스트·드릴이 계정 비밀번호를 바꾸므로, 사람이 다시 들어가려면 이게 필요하다.
    (드릴은 매 실행 임의 비밀번호를 쓴다 — 저장소에 상수를 두지 않기 위해서다.)
    """
    users = UserStore(_root())
    targets = [args.username] if args.username else [u.username for u in users.list()]
    print(f"{B}비밀번호 재발급{Z}  {D}여기서만 보인다. 저장하지 않았다.{Z}")
    for name in targets:
        if users.get(name) is None:
            print(f"  {name:<16} {_t('없는 계정', R)}")
            continue
        pw = secrets.token_urlsafe(12)
        users.set_password(name, pw)
        AuditLog(_root()).write("admin.user.resetpw", actor="cli", target=name)
        print(f"  {name:<16} {_t(pw, Y)}")
    return 0


def cmd_caps(args) -> int:
    print(f"{B}능력 카탈로그{Z}  {D}여기 없는 문자열은 권한으로 인정되지 않는다{Z}")
    for cap, desc in CAPABILITIES.items():
        print(f"  {cap:<24} {desc}")
    return 0


# ── 로그 ────────────────────────────────────────────────────────────────


def cmd_audit(args) -> int:
    recs = AuditLog(_root()).tail(args.limit, action_prefix=args.action or "")
    if args.json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    print(f"{B}감사 로그{Z} ({len(recs)}건)")
    for r in recs:
        col = G if r["result"] in ("ok", "approved") else R
        result = _t(f"{r['result']:<9}", col)
        print(f"  {r['at'].replace('T', ' '):<20} {result} "
              f"{r['action']:<22} {r['actor']:<14} {r.get('target', '')}")
        d = r.get("detail") or {}
        note = "; ".join(f"{k}={v}" for k, v in d.items()
                         if k in ("reason", "skill", "severity", "capability", "error"))
        if note:
            print(f"      {D}{note[:150]}{Z}")
    return 0


def cmd_inquiries(args) -> int:
    p = _root() / "var" / "website" / "inquiries.jsonl"
    if not p.is_file():
        print("접수된 문의가 없다")
        return 0
    lines = jsonl.read(p)
    if args.json:
        print(json.dumps(lines[-args.limit:], ensure_ascii=False, indent=2))
        return 0
    print(f"{B}문의{Z} ({len(lines)}건)")
    for rec in lines[-args.limit:]:
        print(f"  {rec['at'].replace('T', ' ')}  {rec.get('name', '')} "
              f"<{rec.get('email', '')}>  {rec.get('org', '')}")
        print(f"      {D}{rec.get('message', '')[:160]}{Z}")
    return 0


# ── 파서 ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawn-web", description="홈페이지 · 그룹웨어")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("site", help="공개 홈페이지")
    x.add_argument("--port", type=int, default=8810)
    x.add_argument("--host", default="0.0.0.0")
    x.set_defaults(func=cmd_site)

    x = s.add_parser("portal", help="사내 그룹웨어")
    x.add_argument("--port", type=int, default=8811)
    x.add_argument("--host", default="0.0.0.0")
    x.add_argument("--tenant", type=int, default=0)
    x.add_argument("--office-url", default="")
    x.set_defaults(func=cmd_portal)

    x = s.add_parser("useradd", help="계정 생성")
    x.add_argument("username")
    x.add_argument("--name", default="")
    x.add_argument("--org", default="org:dawn")
    x.add_argument("--title", default="")
    x.add_argument("--tenant", type=int, default=0)
    x.add_argument("--capabilities", default="portal.view",
                   help="쉼표 구분. dawn-web caps 로 목록 확인")
    x.add_argument("--password", default=None, help="생략하면 프롬프트")
    x.set_defaults(func=cmd_useradd)

    x = s.add_parser("usermod", help="권한·비밀번호·사용여부 변경")
    x.add_argument("username")
    x.add_argument("--capabilities", default=None)
    x.add_argument("--password", nargs="?", const="", default=None)
    x.add_argument("--disable", action="store_true")
    x.add_argument("--enable", action="store_true")
    x.set_defaults(func=cmd_usermod)

    x = s.add_parser("users", help="계정 목록")
    x.add_argument("--tenant", type=int, default=0)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_users)

    x = s.add_parser("bootstrap", help="첫 관리자 계정")
    x.add_argument("username", nargs="?", default="admin")
    x.add_argument("--name", default="")
    x.add_argument("--org", default="org:dawn")
    x.add_argument("--password", default=None)
    x.add_argument("--force", action="store_true")
    x.set_defaults(func=cmd_bootstrap)

    x = s.add_parser("resetpw", help="비밀번호 임의 재발급 (1회 출력)")
    x.add_argument("username", nargs="?", default="",
                   help="생략하면 전 계정")
    x.set_defaults(func=cmd_resetpw)

    x = s.add_parser("caps", help="능력 카탈로그")
    x.set_defaults(func=cmd_caps)

    x = s.add_parser("audit", help="감사 로그")
    x.add_argument("--limit", type=int, default=50)
    x.add_argument("--action", default="", help="접두사 필터 (auth. / hitl. / eg. / admin.)")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_audit)

    x = s.add_parser("inquiries", help="홈페이지 문의 접수함")
    x.add_argument("--limit", type=int, default=30)
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_inquiries)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n종료")
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(_t(f"✘ {type(exc).__name__}: {exc}", R), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
