"""환경 설정 — .env 를 한 번만 읽는다.

시크릿은 파일에만 있고 코드엔 없다 (05_conventions #1). 패키지가 저장소에
종속적이므로 루트의 .env 를 자동으로 읽되, **이미 설정된 환경변수는 덮지 않는다**.
"""

from __future__ import annotations

import os

_loaded = False


def load_dotenv(force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    _loaded = True
    try:
        from dawn_core.paths import find_root

        env = find_root() / ".env"
    except Exception:
        return
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
