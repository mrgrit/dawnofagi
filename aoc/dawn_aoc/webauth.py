"""관제 콘솔 인증 — **그룹웨어 로그인을 그대로 인정한다** (TODO T2).

콘솔은 전 에이전트의 텔레메트리·케이스·자산 이름을 그대로 보여준다. 인증 없이
열려 있으면 그게 곧 정찰 표면이다 — 실측으로 `/api/state` 가 2.4MB 를 그냥 줬다.

## 로그인을 새로 만들지 않는다

계정을 두 벌 두면 권한이 갈라지고, 갈라진 권한은 관리되지 않는다. 그룹웨어가
이미 세션·능력·감사를 갖고 있으므로 **그 세션 쿠키를 검증만 한다.**

되는 이유는 **쿠키가 포트를 구분하지 않기 때문**이다. `:8811` 에서 로그인하면
같은 호스트의 `:8800` 에도 `dawn_portal` 쿠키가 간다. 다만 **호스트는 구분한다** —
`localhost:8811` 로 로그인하고 `192.168.0.108:8800` 을 열면 쿠키가 안 간다.

## 열지 않는 것이 기본이다

서명 키가 없거나 `itsdangerous` 가 없으면 **인증을 못 하는 것이지 통과가 아니다.**
그럴 땐 전부 거부한다(fail closed). "검증기가 없으니 그냥 통과"가 가장 나쁘다.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

COOKIE = "dawn_portal"                      # 그룹웨어 SessionMiddleware 와 같은 이름
MAX_AGE = 8 * 3600                          # 같은 수명
CAPABILITY = "aoc.view"                     # 콘솔을 볼 수 있는 능력
SESSION_ENV = "DAWN_PORTAL_SECRET"
SESSION_FILE = Path("var") / "groupware" / "session.key"


@dataclass
class Viewer:
    """이 요청은 누구인가."""

    ok: bool
    username: str = ""
    name: str = ""
    why: str = ""                           # 거부 사유 (화면에 그대로 뜬다)


def session_secret(root: Path) -> str:
    """그룹웨어와 **같은 키**. 없으면 빈 문자열 — 만들지 않는다.

    여기서 키를 만들면 그룹웨어가 만든 것과 달라져 로그인이 서로 안 통한다.
    콘솔은 읽기만 한다.
    """
    import os

    env = os.environ.get(SESSION_ENV)
    if env:
        return env
    f = Path(root) / SESSION_FILE
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def _unsign(secret: str, raw: str) -> dict | None:
    """Starlette SessionMiddleware 의 쿠키를 푼다.

    형식: `b64(json).timestamp.signature` — itsdangerous TimestampSigner.
    같은 라이브러리를 쓴다. 직접 검증식을 짜면 그쪽이 바뀔 때 조용히 어긋난다.
    """
    try:
        from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
    except ImportError:
        return None
    try:
        data = TimestampSigner(secret).unsign(raw, max_age=MAX_AGE)
        return json.loads(base64.urlsafe_b64decode(data))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


def check(root: Path, cookie_header: str) -> Viewer:
    """요청 하나를 판정한다. **모르면 거부한다.**"""
    secret = session_secret(root)
    if not secret:
        return Viewer(False, why="세션 키가 없다 — 그룹웨어를 먼저 기동하라 (make web-bg)")

    jar = SimpleCookie()
    try:
        jar.load(cookie_header or "")
    except Exception:                       # 깨진 쿠키 헤더로 콘솔이 죽지 않는다
        return Viewer(False, why="쿠키를 읽지 못했다")
    morsel = jar.get(COOKIE)
    if morsel is None:
        return Viewer(False, why="로그인이 필요하다")

    sess = _unsign(secret, morsel.value)
    if sess is None:
        return Viewer(False, why="세션이 유효하지 않다 — 다시 로그인하라")
    username = str(sess.get("u") or "")
    if not username:
        return Viewer(False, why="로그인이 필요하다")

    from dawn_core.identity import UserStore

    u = UserStore(root).get(username)
    if u is None or u.disabled:
        return Viewer(False, why="사용할 수 없는 계정이다")
    if not u.can(CAPABILITY):
        return Viewer(False, username=username, name=u.name,
                      why=f"'{CAPABILITY}' 능력이 없다 — 관리자에게 요청하라")
    return Viewer(True, username=username, name=u.name)


def login_url(host_header: str, port: int = 8811) -> str:
    """그룹웨어 로그인 주소. **콘솔과 같은 호스트여야 쿠키가 공유된다.**"""
    host = (host_header or "").split(":")[0] or "localhost"
    return f"http://{host}:{port}/login"


__all__ = ["CAPABILITY", "COOKIE", "MAX_AGE", "SESSION_ENV", "SESSION_FILE",
           "Viewer", "check", "login_url", "session_secret"]
