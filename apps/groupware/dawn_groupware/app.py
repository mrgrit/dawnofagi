"""ASGI 앱 조립 — **공개 사이트와 그룹웨어는 다른 앱이다.**

한 프로세스에 합치지 않는 이유: 공개 사이트는 dmz 앞단(L0)에 놓이고 그룹웨어는
user/int 존에 놓인다 (04_tech_stack). 같은 프로세스면 존 분리가 배포 설정에만
의존하게 되는데, 그건 언젠가 실수로 무너진다. 프로세스를 나누면 공개 쪽 코드에
EG·업무 DB·계정 저장소로 가는 임포트 자체가 없다.

    build_site(root)    공개 홈페이지 — 세션 없음, 인증 없음, 내부 상태 없음
    build_portal(root)  그룹웨어    — 세션·인증·권한·감사
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dawn_core import Registry
from dawn_core.paths import Paths
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from . import portal, site
from .audit import AuditLog
from .egedit import EGEditor
from .render import Safe, a, h1, join, p, page
from .store import Store, seed_if_empty

SESSION_ENV = "DAWN_PORTAL_SECRET"
SESSION_FILE = "var/groupware/session.key"


def _root(root: Path | str | None = None) -> Path:
    return Path(root) if root else Paths().root


def _session_secret(root: Path) -> str:
    """세션 서명 키. 환경변수 우선, 없으면 `var/` 에 만들어 재사용한다.

    **저장소에 절대 들어가지 않는다.** 매 기동마다 새로 만들면 재시작할 때마다
    전원 로그아웃되므로 파일로 유지하되 0600 이다.
    """
    env = os.environ.get(SESSION_ENV)
    if env:
        if len(env) < 32:
            raise SystemExit(f"{SESSION_ENV} 는 32자 이상이어야 한다")
        return env
    path = root / SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return key


async def _not_found(request: Request, exc) -> HTMLResponse:
    body = join([h1("없는 페이지"), p("주소를 확인하라."), p(a("← 처음으로", href="/"))])
    return HTMLResponse(page("404", body), status_code=404)


async def _server_error(request: Request, exc) -> HTMLResponse:
    # 스택 트레이스를 사용자에게 보여주지 않는다 — 내부 경로가 곧 정보다.
    body = join([
        h1("처리 중 오류가 났다"),
        p("관리자에게 알려라. 상세 내용은 서버 로그에만 남는다."),
        p(a("← 처음으로", href="/")),
    ])
    return HTMLResponse(page("500", body), status_code=500)


EXCEPTION_HANDLERS = {404: _not_found, 500: _server_error}


def build_site(root: Path | str | None = None) -> Starlette:
    """공개 홈페이지 (L0). 세션도 인증도 없다."""
    r = _root(root)
    app = Starlette(routes=site.routes, exception_handlers=EXCEPTION_HANDLERS)
    app.state.root = r
    app.state.registry = Registry.load(r)
    return app


def build_portal(root: Path | str | None = None, *, tenant: int = 0,
                 office_url: str = "", secure_cookie: bool | None = None) -> Starlette:
    """사내 그룹웨어. 세션·인증·권한·감사."""
    from dawn_agents.hitl import ApprovalQueue
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    from .auth import UserStore

    r = _root(root)
    registry = Registry.load(r)
    db = db_path(registry.paths)
    eg = EGStore(db) if db.is_file() else None

    https_only = (os.environ.get("DAWN_PORTAL_HTTPS", "0") == "1"
                  if secure_cookie is None else secure_cookie)
    middleware = [
        Middleware(
            SessionMiddleware,
            secret_key=_session_secret(r),
            session_cookie="dawn_portal",
            https_only=https_only,        # HTTPS 배포 시 DAWN_PORTAL_HTTPS=1
            same_site="lax",              # CSRF 토큰과 이중 방어
            max_age=8 * 3600,
        ),
    ]

    app = Starlette(routes=portal.routes, middleware=middleware,
                    exception_handlers=EXCEPTION_HANDLERS)
    app.state.root = r
    app.state.registry = registry
    app.state.eg = eg
    app.state.users = UserStore(r)
    app.state.audit = AuditLog(r)
    app.state.queue = ApprovalQueue(r)
    app.state.egeditor = EGEditor(r)
    store = Store(r, tenant=tenant)
    seed_if_empty(store, registry)
    app.state.store = store
    app.state.office_url = office_url or os.environ.get(
        "DAWN_OFFICE_URL", "http://localhost:8800/"
    )
    return app


async def _site_health(request: Request) -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse("ok")


__all__ = ["Safe", "build_portal", "build_site"]
