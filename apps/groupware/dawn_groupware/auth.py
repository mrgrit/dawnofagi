"""인증·권한 — 구현은 [`dawn_core.identity`](../../../packages/dawn_core/dawn_core/identity.py).

**관제 콘솔도 같은 로그인을 인정해야 해서** 공용 계층으로 내렸다. 콘솔이 그룹웨어를
import 하면 패키지 순환이 된다(그룹웨어가 이미 `dawn_aoc.console` 을 쓴다).

여기는 기존 import 경로를 유지하는 껍데기다 — 계정·능력의 정의는 한 곳뿐이어야 한다.
"""

from __future__ import annotations

from dawn_core.identity import (
    CAPABILITIES,
    CRITICAL_SEVERITY,
    PBKDF2_ROUNDS,
    SALT_BYTES,
    User,
    UserStore,
    can_approve,
    hash_password,
    org_chain,
    verify_password,
)

__all__ = [
    "CAPABILITIES",
    "CRITICAL_SEVERITY",
    "PBKDF2_ROUNDS",
    "SALT_BYTES",
    "User",
    "UserStore",
    "can_approve",
    "hash_password",
    "org_chain",
    "verify_password",
]
