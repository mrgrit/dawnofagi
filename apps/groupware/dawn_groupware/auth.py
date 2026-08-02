"""인증·권한 — **EG OrgUnit 기반**. 최소권한.

사람 계정은 `var/groupware/users.json` 에 있다(저장소에 안 들어간다).
비밀번호는 PBKDF2-HMAC-SHA256 으로 해시하고 원문은 어디에도 남기지 않는다.

## 권한 모델

능력(capability)은 **역할이 아니라 조직 + 명시 권한**의 곱이다.

    can(user, "hitl.approve")            이 사람이 승인 권한을 가졌나
    can_approve(user, agent_org, sev)    **그 에이전트**를 승인할 수 있나

두 번째가 핵심이다. `hitl.approve` 를 가졌다고 전사 승인권이 생기지 않는다.
승인자는 그 에이전트의 조직이거나 **상위 조직**이어야 한다 (EG OrgUnit 트리 순회).
최고 심각도(≥6)는 `hitl.approve.critical` 이 따로 필요하다 — 비가역·L3 를
아무나 눌러서는 안 된다.

## 왜 조직 트리인가

경영관리부 사람이 CCC 에이전트의 방화벽 변경을 승인하면, 그건 승인이 아니라
책임 회피다. 승인은 **그 일을 아는 조직**이 해야 한다. 조직 경계는 EG 에 이미
있으므로 여기서 새로 만들지 않고 순회한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PBKDF2_ROUNDS = 240_000
SALT_BYTES = 16

# 능력 카탈로그 — 여기 없는 문자열은 권한으로 인정하지 않는다 (오타가 곧 권한이 되면 안 된다)
CAPABILITIES = {
    "portal.view": "그룹웨어 열람 (공지·문서·일정·디렉터리)",
    "portal.post": "공지·문서·일정 작성",
    "hitl.view": "승인 큐 열람",
    "hitl.approve": "승인/거부 (자기 조직 및 하위 조직 한정)",
    "hitl.approve.critical": "최고 심각도(비가역·L3) 승인",
    "eg.view": "EG(페르소나·정책) 열람",
    "eg.edit": "EG 조정 — 사람이 에이전트에 개입하는 주 통로",
    "control.view": "통제 평면 열람 (L2·L3·L4 문서와 경계)",
    "control.edit": "통제 평면 조정 — 에이전트 추가·삭제, 규칙·경계 수정",
    "aoc.view": "관제 콘솔·픽셀 오피스 접근",
    "aoc.control": "킬 스위치 조작 (일시중지·격리·종료)",
    "admin": "계정 관리",
}

CRITICAL_SEVERITY = 6      # actiongate 의 최고 점수


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """PBKDF2. 원문은 반환하지도, 로그에 남기지도 않는다."""
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 한다")
    salt = salt or secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
    )
    return secrets.compare_digest(dk.hex(), dk_hex)


@dataclass
class User:
    username: str
    name: str
    org: str                                  # EG OrgUnit id — 권한의 뿌리
    title: str = ""
    email: str = ""
    tenant: int = 0                           # 자사 = 테넌트 #0
    capabilities: list[str] = field(default_factory=list)
    password_hash: str = ""
    disabled: bool = False
    created_at: str = ""
    last_login: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def public(self) -> dict[str, Any]:
        """비밀번호 해시를 뺀 표현 — 화면·API 로 나가는 것."""
        d = self.to_dict()
        d.pop("password_hash", None)
        return d

    def can(self, capability: str) -> bool:
        if self.disabled:
            return False
        if capability not in CAPABILITIES:
            return False                       # 카탈로그에 없는 능력은 존재하지 않는다
        return "admin" in self.capabilities or capability in self.capabilities


class UserStore:
    """사람 계정 저장소. `var/` 아래고 저장소에 커밋되지 않는다."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "var" / "groupware" / "users.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get(self, username: str) -> User | None:
        d = self._load().get(username)
        if d is None:
            return None
        return User(**{k: v for k, v in d.items() if k in User.__annotations__})

    def list(self, *, tenant: int = 0) -> list[User]:
        out = []
        for d in self._load().values():
            u = User(**{k: v for k, v in d.items() if k in User.__annotations__})
            if u.tenant == tenant:              # 테넌트 격리 — 크로스테넌트 조회 경로 없음
                out.append(u)
        return sorted(out, key=lambda u: (u.org, u.username))

    def create(self, username: str, password: str, *, name: str, org: str,
               title: str = "", email: str = "", tenant: int = 0,
               capabilities: list[str] | None = None) -> User:
        data = self._load()
        if username in data:
            raise ValueError(f"이미 있는 계정: {username}")
        caps = capabilities or ["portal.view"]
        unknown = [c for c in caps if c not in CAPABILITIES]
        if unknown:
            raise ValueError(f"카탈로그에 없는 능력: {', '.join(unknown)}")
        u = User(username=username, name=name, org=org, title=title, email=email,
                 tenant=tenant, capabilities=caps,
                 password_hash=hash_password(password), created_at=_now())
        data[username] = u.to_dict()
        self._save(data)
        return u

    def delete(self, username: str) -> bool:
        """계정을 지운다. 없으면 False.

        **비활성화(`set_disabled`)와 다르다.** 퇴사·오생성처럼 흔적을 남길 이유가
        없을 때만 쓴다 — 감사 로그의 행위자 이름은 계정을 지워도 남는다.
        """
        data = self._load()
        if username not in data:
            return False
        del data[username]
        self._save(data)
        return True

    def set_capabilities(self, username: str, caps: list[str]) -> User:
        unknown = [c for c in caps if c not in CAPABILITIES]
        if unknown:
            raise ValueError(f"카탈로그에 없는 능력: {', '.join(unknown)}")
        data = self._load()
        if username not in data:
            raise KeyError(username)
        data[username]["capabilities"] = caps
        self._save(data)
        return self.get(username)

    def set_password(self, username: str, password: str) -> None:
        data = self._load()
        if username not in data:
            raise KeyError(username)
        data[username]["password_hash"] = hash_password(password)
        self._save(data)

    def set_disabled(self, username: str, disabled: bool) -> User:
        data = self._load()
        if username not in data:
            raise KeyError(username)
        data[username]["disabled"] = disabled
        self._save(data)
        return self.get(username)

    def touch_login(self, username: str) -> None:
        data = self._load()
        if username in data:
            data[username]["last_login"] = _now()
            self._save(data)

    def authenticate(self, username: str, password: str) -> User | None:
        u = self.get(username)
        if u is None:
            # 계정이 없어도 해시를 한 번 계산한다 — 응답 시간으로 계정 존재를 알 수 없게.
            verify_password(password, hash_password("dummy-timing-equalizer"))
            return None
        if u.disabled or not verify_password(password, u.password_hash):
            return None
        return u


# ── 조직 트리 순회 — 승인 권한의 근거 ────────────────────────────────────


def org_chain(eg_store, org_id: str, *, max_depth: int = 8) -> list[str]:
    """`org_id` 에서 루트까지의 조직 사슬 (자기 자신 포함)."""
    chain, cur, seen = [], org_id, set()
    for _ in range(max_depth):
        if not cur or cur in seen:
            break
        seen.add(cur)
        chain.append(cur)
        if eg_store is None or eg_store.node(cur) is None:
            break
        parents = [n.id for n in eg_store.out(cur, "PART_OF")]
        cur = parents[0] if parents else ""
    return chain


def can_approve(user: User, *, agent_org: str, severity: int,
                eg_store=None) -> tuple[bool, str]:
    """이 사람이 **이 에이전트의** 요청을 승인할 수 있나.

    Returns: (가능한가, 이유). 거부 이유는 화면에 그대로 보여준다 —
    "권한 없음"만 뜨는 화면은 사람을 우회 경로로 몰아낸다.
    """
    if not user.can("hitl.approve"):
        return False, "hitl.approve 권한이 없다"
    if severity >= CRITICAL_SEVERITY and not user.can("hitl.approve.critical"):
        return False, (f"최고 심각도({severity}) — hitl.approve.critical 권한이 필요하다. "
                       "비가역·L3 는 아무나 승인하지 않는다")
    if not agent_org:
        return False, "이 요청에 조직 정보가 없다 — 승인 책임을 특정할 수 없다"
    # 승인자는 그 에이전트의 조직이거나 상위 조직이어야 한다
    chain = org_chain(eg_store, agent_org)
    if user.org not in chain:
        return False, (f"조직 밖이다. 이 요청은 {agent_org} 소속이고, "
                       f"승인 가능 조직은 {', '.join(chain)} 이다 "
                       f"(당신: {user.org})")
    return True, ""


__all__ = [
    "CAPABILITIES",
    "CRITICAL_SEVERITY",
    "User",
    "UserStore",
    "can_approve",
    "hash_password",
    "org_chain",
    "verify_password",
]
