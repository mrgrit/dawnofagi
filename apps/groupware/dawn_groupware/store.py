"""업무 데이터 — 공지·문서·일정. SQLite.

**테넌트 격리를 구조로 강제한다.** 모든 테이블에 `tenant` 컬럼이 있고, 모든 조회는
`Store(tenant=N)` 로 묶인 커넥션을 통해서만 나간다. 테넌트를 인자로 받는 조회 함수는
만들지 않는다 — 인자로 받으면 언젠가 잘못된 값이 들어간다 (05_conventions #4).

EG(회사의 뇌)와 다른 DB 를 쓴다. 업무 데이터가 EG 를 오염시키면 안 되고,
EG 재주입이 업무 데이터를 날려서도 안 된다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS notice (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L1',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notice_tenant  ON notice(tenant, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_document_tenant ON document(tenant, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_event_tenant   ON event(tenant, starts_at);
"""

# 문서 보안등급 — EG SecurityLevel 과 같은 이름을 쓴다 (다른 어휘를 만들지 않는다)
SECURITY_LEVELS = {
    "L0": "공개",
    "L1": "사내",
    "L2": "제한 — 담당 조직",
    "L3": "기밀 — 인사·재무·개인정보. 클라우드 모델 전송 금지",
}
LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Row:
    """테이블 행 하나 — dict 로 다니면 오타가 조용히 통과한다."""

    kind: str
    data: dict[str, Any]

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.data}


class Store:
    """업무 데이터. **테넌트에 묶인 채로만** 존재한다."""

    def __init__(self, root: Path, *, tenant: int = 0) -> None:
        self.tenant = int(tenant)
        self.path = Path(root) / "var" / "groupware" / "portal.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ── 공지 ────────────────────────────────────────────────────────────
    def add_notice(self, *, title: str, body: str, author: str, org: str = "",
                   pinned: bool = False) -> int:
        cur = self.db.execute(
            "INSERT INTO notice(tenant,title,body,author,org,pinned,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (self.tenant, title, body, author, org, int(pinned), _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def notices(self, limit: int = 50) -> list[Row]:
        rows = self.db.execute(
            "SELECT * FROM notice WHERE tenant=? ORDER BY pinned DESC, created_at DESC"
            " LIMIT ?", (self.tenant, limit),
        ).fetchall()
        return [Row("notice", dict(r)) for r in rows]

    # ── 문서 ────────────────────────────────────────────────────────────
    def add_document(self, *, title: str, body: str, author: str, org: str = "",
                     security_level: str = "L1") -> int:
        if security_level not in SECURITY_LEVELS:
            raise ValueError(f"모르는 보안등급: {security_level}")
        now = _now()
        cur = self.db.execute(
            "INSERT INTO document(tenant,title,body,author,org,security_level,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (self.tenant, title, body, author, org, security_level, now, now),
        )
        self.db.commit()
        return cur.lastrowid

    def documents(self, *, max_level: str = "L3", limit: int = 100) -> list[Row]:
        """`max_level` 보다 높은 등급의 문서는 **행 자체가 나오지 않는다.**

        제목만 보여주고 본문을 가리는 방식은 안 쓴다 — 제목이 곧 정보인 경우가 많다.
        """
        cap = LEVEL_RANK.get(max_level, 0)
        allowed = [k for k, v in LEVEL_RANK.items() if v <= cap]
        marks = ",".join("?" * len(allowed))
        rows = self.db.execute(
            f"SELECT * FROM document WHERE tenant=? AND security_level IN ({marks})"
            " ORDER BY updated_at DESC LIMIT ?",
            (self.tenant, *allowed, limit),
        ).fetchall()
        return [Row("document", dict(r)) for r in rows]

    def document(self, doc_id: int, *, max_level: str = "L3") -> Row | None:
        r = self.db.execute(
            "SELECT * FROM document WHERE tenant=? AND id=?", (self.tenant, doc_id)
        ).fetchone()
        if r is None:
            return None
        if LEVEL_RANK.get(r["security_level"], 3) > LEVEL_RANK.get(max_level, 0):
            return None
        return Row("document", dict(r))

    # ── 일정 ────────────────────────────────────────────────────────────
    def add_event(self, *, title: str, starts_at: str, author: str, ends_at: str = "",
                  location: str = "", body: str = "", org: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO event(tenant,title,starts_at,ends_at,location,body,author,org,"
            "created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (self.tenant, title, starts_at, ends_at, location, body, author, org, _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def events(self, *, since: str = "", limit: int = 50) -> list[Row]:
        rows = self.db.execute(
            "SELECT * FROM event WHERE tenant=? AND starts_at>=? ORDER BY starts_at LIMIT ?",
            (self.tenant, since or "0000", limit),
        ).fetchall()
        return [Row("event", dict(r)) for r in rows]

    # ── 진단 ────────────────────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        out = {}
        for t in ("notice", "document", "event"):
            out[t] = self.db.execute(
                f"SELECT COUNT(*) FROM {t} WHERE tenant=?", (self.tenant,)
            ).fetchone()[0]
        return out

    def foreign_rows(self) -> int:
        """다른 테넌트 행 수 — 격리 테스트용. 조회 경로가 아니라 **검사** 경로다."""
        n = 0
        for t in ("notice", "document", "event"):
            n += self.db.execute(
                f"SELECT COUNT(*) FROM {t} WHERE tenant<>?", (self.tenant,)
            ).fetchone()[0]
        return n


def seed_if_empty(store: Store, registry) -> int:
    """빈 포털에 최소 콘텐츠를 넣는다 — 첫 화면이 비어 있으면 아무도 안 쓴다.

    내용은 **레지스트리에서** 온다. 지어낸 공지를 넣지 않는다.
    """
    if store.counts()["notice"]:
        return 0
    n = 0
    store.add_notice(
        title="그룹웨어를 열었다 — 여기가 사람이 개입하는 통로다",
        body=(
            "이 포털은 게시판이 아니라 **관문**이다.\n\n"
            "· 승인 큐: 에이전트가 비가역 행동을 시도하면 여기로 온다. 사람이 누르기 전엔 안 돈다.\n"
            "· EG 조정: 에이전트 행동을 바꾸려면 코드가 아니라 EG(페르소나·정책)를 고친다.\n"
            "· 관제 콘솔: 지금 누가 무엇을 하고 있는지 픽셀 오피스에서 본다.\n\n"
            "공지·문서·일정은 그다음이다."
        ),
        author="system", org="org:dawn", pinned=True,
    )
    n += 1
    for bid, b in sorted(registry.businesses.items()):
        d = b.data
        phases = d.get("roadmap") or []
        body = [d.get("mission", "")]
        if phases:
            body.append("\n로드맵")
            for p in phases:
                body.append(f"  · [{p.get('status', '?')}] {p.get('phase')} — {p.get('goal', '')}")
        store.add_document(
            title=f"[사업] {d.get('name', bid)}",
            body="\n".join(body),
            author="system", org="org:dawn",
            security_level="L1" if d.get("data_sensitivity") != "L3" else "L2",
        )
        n += 1
    return n


__all__ = ["LEVEL_RANK", "SECURITY_LEVELS", "Row", "Store", "seed_if_empty"]
