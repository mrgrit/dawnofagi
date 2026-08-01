"""업무 데이터 — 문서·지식 / CRM / 프로젝트 / 경리·자산.

P4 그룹웨어의 `Store` 와 같은 규율을 따른다:

* **테넌트에 묶인 커넥션만 존재한다.** 조회 함수는 tenant 를 인자로 받지 않는다.
* 모든 행은 `security_level` 을 갖고, 그 등급은 EG `SecurityLevel` 과 같은 어휘다.
* 모든 행은 `eg_asset` 을 갖는다 — **이 데이터가 EG 의 어느 자산에 속하는가.**
  관제가 심각도를 계산할 때, 픽셀 오피스가 방을 정할 때 이걸 쓴다.
  자산 없이 떠 있는 업무 데이터는 관제 대상이 되지 못한다.

DB 는 그룹웨어와 분리한다(`var/biz/business.db`). 공지·일정이 날아가도 계약이
살아 있어야 하고, 반대도 마찬가지다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

# 업무 데이터 종류 → EG Asset. **여기가 업무 시스템과 EG 를 잇는 유일한 지점이다.**
# 새 종류를 추가하면 EG 시드에도 자산을 추가해야 하고, `egsync.check()` 가 그걸 강제한다.
KIND_ASSET = {
    "document": "asset:knowledge",
    "customer": "asset:crm",
    "inquiry": "asset:crm",
    "contract": "asset:contract",
    "project": "asset:project",
    "task": "asset:project",
    "expense": "asset:ledger",
    "fixed_asset": "asset:fixed-asset",
}

# 종류별 기본 보안등급. 경비(L3)는 인사·재무이므로 로컬 모델 전용 경로를 탄다.
KIND_LEVEL = {
    "document": "L1", "customer": "L2", "inquiry": "L2", "contract": "L2",
    "project": "L1", "task": "L1", "expense": "L3", "fixed_asset": "L1",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L1',
  eg_asset TEXT NOT NULL DEFAULT 'asset:knowledge',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_revision (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  author TEXT NOT NULL,
  at TEXT NOT NULL
);
-- contentless(content='')로 만들면 DELETE 가 안 돼 개정 시 재색인이 실패한다.
-- 본문을 두 번 저장하는 대신 개정이 되는 쪽을 택했다.
CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
  USING fts5(title, body, tags);

CREATE TABLE IF NOT EXISTS customer (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  name TEXT NOT NULL,
  segment TEXT NOT NULL DEFAULT '',
  contact_name TEXT NOT NULL DEFAULT '',
  contact_email TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  owner_org TEXT NOT NULL DEFAULT 'org:mgmt',
  security_level TEXT NOT NULL DEFAULT 'L2',
  eg_asset TEXT NOT NULL DEFAULT 'asset:crm',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inquiry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  customer_id INTEGER,
  source TEXT NOT NULL DEFAULT 'website',
  name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  org_name TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  draft TEXT NOT NULL DEFAULT '',
  drafted_by TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L2',
  eg_asset TEXT NOT NULL DEFAULT 'asset:crm',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  amount_krw INTEGER NOT NULL DEFAULT 0,
  starts_on TEXT NOT NULL DEFAULT '',
  ends_on TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  signed_by TEXT NOT NULL DEFAULT '',
  signed_at TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L2',
  eg_asset TEXT NOT NULL DEFAULT 'asset:contract',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  key TEXT NOT NULL,
  name TEXT NOT NULL,
  business TEXT NOT NULL DEFAULT '',
  owner_team TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  security_level TEXT NOT NULL DEFAULT 'L1',
  eg_asset TEXT NOT NULL DEFAULT 'asset:project',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  project_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  phase TEXT NOT NULL DEFAULT 'build',
  depends_on TEXT NOT NULL DEFAULT '',
  assignee TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo',
  result TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L1',
  eg_asset TEXT NOT NULL DEFAULT 'asset:project',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expense (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  request_id TEXT NOT NULL,
  requester TEXT NOT NULL,
  requester_org TEXT NOT NULL DEFAULT '',
  amount_krw INTEGER NOT NULL DEFAULT 0,
  category TEXT NOT NULL DEFAULT '',
  receipt_id TEXT NOT NULL DEFAULT '',
  ledger_entry TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'submitted',
  verdict TEXT NOT NULL DEFAULT '',
  processed_by TEXT NOT NULL DEFAULT '',
  hitl_id TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  security_level TEXT NOT NULL DEFAULT 'L3',
  eg_asset TEXT NOT NULL DEFAULT 'asset:ledger',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fixed_asset (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant INTEGER NOT NULL,
  tag TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '',
  holder TEXT NOT NULL DEFAULT '',
  acquired_on TEXT NOT NULL DEFAULT '',
  amount_krw INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'in_use',
  security_level TEXT NOT NULL DEFAULT 'L1',
  eg_asset TEXT NOT NULL DEFAULT 'asset:fixed-asset',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_doc_t   ON document(tenant, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_cus_t   ON customer(tenant, name);
CREATE INDEX IF NOT EXISTS ix_inq_t   ON inquiry(tenant, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_con_t   ON contract(tenant, status);
CREATE INDEX IF NOT EXISTS ix_prj_t   ON project(tenant, status);
CREATE INDEX IF NOT EXISTS ix_tsk_t   ON task(tenant, project_id, status);
CREATE INDEX IF NOT EXISTS ix_exp_t   ON expense(tenant, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_fa_t    ON fixed_asset(tenant, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Row:
    kind: str
    data: dict[str, Any]

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)

    @property
    def eg_asset(self) -> str:
        return self.data.get("eg_asset", "")

    @property
    def level(self) -> str:
        return self.data.get("security_level", "L1")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.data}


class BizStore:
    """업무 데이터. 테넌트에 묶인 채로만 존재한다."""

    def __init__(self, root: Path, *, tenant: int = 0) -> None:
        self.tenant = int(tenant)
        self.root = Path(root)
        self.path = self.root / "var" / "biz" / "business.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ── 공통 ────────────────────────────────────────────────────────────
    def _insert(self, table: str, **cols: Any) -> int:
        cols.setdefault("tenant", self.tenant)
        cols.setdefault("eg_asset", KIND_ASSET.get(table, ""))
        cols.setdefault("security_level", KIND_LEVEL.get(table, "L1"))
        keys = ",".join(cols)
        marks = ",".join("?" * len(cols))
        cur = self.db.execute(f"INSERT INTO {table}({keys}) VALUES({marks})",
                              tuple(cols.values()))
        self.db.commit()
        return cur.lastrowid

    def _rows(self, table: str, where: str = "", params: tuple = (),
              *, max_level: str = "L3", limit: int = 200) -> list[Row]:
        cap = LEVEL_RANK.get(max_level, 0)
        allowed = [k for k, v in LEVEL_RANK.items() if v <= cap]
        marks = ",".join("?" * len(allowed))
        sql = (f"SELECT * FROM {table} WHERE tenant=? AND security_level IN ({marks})"
               + (f" AND {where}" if where else "") + f" ORDER BY id DESC LIMIT {int(limit)}")
        rows = self.db.execute(sql, (self.tenant, *allowed, *params)).fetchall()
        return [Row(table, dict(r)) for r in rows]

    def _one(self, table: str, row_id: int, *, max_level: str = "L3") -> Row | None:
        r = self.db.execute(f"SELECT * FROM {table} WHERE tenant=? AND id=?",
                            (self.tenant, row_id)).fetchone()
        if r is None:
            return None
        if LEVEL_RANK.get(r["security_level"], 3) > LEVEL_RANK.get(max_level, 0):
            return None                          # 등급 초과 = 존재 자체를 알리지 않는다
        return Row(table, dict(r))

    # ── 문서·지식 ───────────────────────────────────────────────────────
    def add_document(self, *, title: str, body: str, author: str, org: str = "",
                     tags: str = "", security_level: str = "L1") -> int:
        now = _now()
        did = self._insert("document", title=title, body=body, author=author, org=org,
                           tags=tags, security_level=security_level,
                           created_at=now, updated_at=now)
        self._index_document(did, title, body, tags)
        self.db.execute(
            "INSERT INTO document_revision(tenant,document_id,revision,title,body,author,at)"
            " VALUES(?,?,?,?,?,?,?)", (self.tenant, did, 1, title, body, author, now))
        self.db.commit()
        return did

    def revise_document(self, doc_id: int, *, title: str, body: str, author: str) -> int:
        """개정. **이전 판을 지우지 않는다** — 산출물의 근거는 시점이 중요하다."""
        cur = self._one("document", doc_id)
        if cur is None:
            raise KeyError(f"문서 없음: {doc_id}")
        rev = int(cur["revision"]) + 1
        now = _now()
        self.db.execute(
            "UPDATE document SET title=?,body=?,revision=?,updated_at=? "
            "WHERE tenant=? AND id=?", (title, body, rev, now, self.tenant, doc_id))
        self.db.execute(
            "INSERT INTO document_revision(tenant,document_id,revision,title,body,author,at)"
            " VALUES(?,?,?,?,?,?,?)", (self.tenant, doc_id, rev, title, body, author, now))
        self.db.commit()
        self._index_document(doc_id, title, body, cur["tags"])
        return rev

    def _index_document(self, doc_id: int, title: str, body: str, tags: str) -> None:
        self.db.execute("DELETE FROM document_fts WHERE rowid=?", (doc_id,))
        self.db.execute(
            "INSERT INTO document_fts(rowid,title,body,tags) VALUES(?,?,?,?)",
            (doc_id, title, body, tags))
        self.db.commit()

    def documents(self, *, max_level: str = "L3", limit: int = 100) -> list[Row]:
        return self._rows("document", max_level=max_level, limit=limit)

    def document(self, doc_id: int, *, max_level: str = "L3") -> Row | None:
        return self._one("document", doc_id, max_level=max_level)

    def revisions(self, doc_id: int) -> list[Row]:
        rows = self.db.execute(
            "SELECT * FROM document_revision WHERE tenant=? AND document_id=?"
            " ORDER BY revision DESC", (self.tenant, doc_id)).fetchall()
        return [Row("document_revision", dict(r)) for r in rows]

    def search_documents(self, query: str, *, max_level: str = "L3",
                         limit: int = 20) -> list[Row]:
        """FTS5 전문 검색. **등급 필터가 검색에도 걸린다** — 못 읽는 문서는 안 걸린다."""
        if not query.strip():
            return []
        try:
            hits = self.db.execute(
                "SELECT rowid, rank FROM document_fts WHERE document_fts MATCH ?"
                " ORDER BY rank LIMIT ?", (query, limit * 3)).fetchall()
        except sqlite3.OperationalError:
            # 사용자가 넣은 문자열이 FTS 문법을 깨는 건 오류가 아니라 흔한 일이다
            # 단어 안의 따옴표를 없애고 감싼다 — 안 없애면 또 unterminated 다
            words = [w.replace('"', " ").strip() for w in query.split()]
            safe = " ".join(f'"{w}"' for w in words if w)
            if not safe:
                return []
            hits = self.db.execute(
                "SELECT rowid, rank FROM document_fts WHERE document_fts MATCH ?"
                " ORDER BY rank LIMIT ?", (safe, limit * 3)).fetchall()
        out = []
        for h in hits:
            row = self._one("document", h["rowid"], max_level=max_level)
            if row is not None:
                out.append(row)
            if len(out) >= limit:
                break
        return out

    # ── CRM ─────────────────────────────────────────────────────────────
    def add_customer(self, *, name: str, segment: str = "", contact_name: str = "",
                     contact_email: str = "", note: str = "",
                     owner_org: str = "org:mgmt") -> int:
        return self._insert("customer", name=name, segment=segment,
                            contact_name=contact_name, contact_email=contact_email,
                            note=note, owner_org=owner_org, created_at=_now())

    def customers(self, *, max_level: str = "L3", limit: int = 200) -> list[Row]:
        return self._rows("customer", max_level=max_level, limit=limit)

    def customer(self, cid: int, *, max_level: str = "L3") -> Row | None:
        return self._one("customer", cid, max_level=max_level)

    def add_inquiry(self, *, name: str, email: str, message: str, org_name: str = "",
                    source: str = "website", customer_id: int | None = None) -> int:
        now = _now()
        return self._insert("inquiry", customer_id=customer_id, source=source, name=name,
                            email=email, org_name=org_name, message=message,
                            created_at=now, updated_at=now)

    def inquiries(self, *, status: str = "", max_level: str = "L3",
                  limit: int = 100) -> list[Row]:
        where, params = ("status=?", (status,)) if status else ("", ())
        return self._rows("inquiry", where, params, max_level=max_level, limit=limit)

    def inquiry(self, iid: int, *, max_level: str = "L3") -> Row | None:
        return self._one("inquiry", iid, max_level=max_level)

    def set_inquiry_draft(self, iid: int, *, draft: str, category: str,
                          drafted_by: str, trace_id: str = "") -> None:
        """응답 **초안**만 저장한다. 발송은 `comm.external_send` 라 게이트가 세운다."""
        self.db.execute(
            "UPDATE inquiry SET draft=?,category=?,drafted_by=?,trace_id=?,"
            "status='drafted',updated_at=? WHERE tenant=? AND id=?",
            (draft, category, drafted_by, trace_id, _now(), self.tenant, iid))
        self.db.commit()

    def add_contract(self, *, customer_id: int, title: str, amount_krw: int,
                     starts_on: str = "", ends_on: str = "") -> int:
        return self._insert("contract", customer_id=customer_id, title=title,
                            amount_krw=amount_krw, starts_on=starts_on, ends_on=ends_on,
                            created_at=_now())

    def contracts(self, *, max_level: str = "L3", limit: int = 100) -> list[Row]:
        return self._rows("contract", max_level=max_level, limit=limit)

    def sign_contract(self, cid: int, *, signed_by: str) -> None:
        """체결. **사람만** 부른다 — `crm.contract_sign` 은 비가역 스킬이고 실행부가 없다."""
        if not signed_by.startswith("human:"):
            raise PermissionError("계약 체결은 사람만 한다 (signed_by=human:<이름>)")
        self.db.execute(
            "UPDATE contract SET status='signed',signed_by=?,signed_at=? "
            "WHERE tenant=? AND id=?", (signed_by, _now(), self.tenant, cid))
        self.db.commit()

    # ── 프로젝트·이슈 ───────────────────────────────────────────────────
    def add_project(self, *, key: str, name: str, business: str = "",
                    owner_team: str = "") -> int:
        return self._insert("project", key=key, name=name, business=business,
                            owner_team=owner_team, created_at=_now())

    def projects(self, *, max_level: str = "L3", limit: int = 100) -> list[Row]:
        return self._rows("project", max_level=max_level, limit=limit)

    def project_by_key(self, key: str) -> Row | None:
        r = self.db.execute("SELECT * FROM project WHERE tenant=? AND key=?",
                            (self.tenant, key)).fetchone()
        return Row("project", dict(r)) if r else None

    def add_task(self, *, project_id: int, title: str, body: str = "",
                 phase: str = "build", depends_on: str = "", assignee: str = "") -> int:
        now = _now()
        return self._insert("task", project_id=project_id, title=title, body=body,
                            phase=phase, depends_on=depends_on, assignee=assignee,
                            created_at=now, updated_at=now)

    def tasks(self, *, project_id: int | None = None, status: str = "",
              max_level: str = "L3", limit: int = 200) -> list[Row]:
        wh, params = [], []
        if project_id is not None:
            wh.append("project_id=?")
            params.append(project_id)
        if status:
            wh.append("status=?")
            params.append(status)
        return self._rows("task", " AND ".join(wh), tuple(params),
                          max_level=max_level, limit=limit)

    def update_task(self, tid: int, *, status: str = "", result: str = "",
                    assignee: str = "", trace_id: str = "") -> None:
        sets, params = ["updated_at=?"], [_now()]
        for col, val in (("status", status), ("result", result),
                         ("assignee", assignee), ("trace_id", trace_id)):
            if val:
                sets.append(f"{col}=?")
                params.append(val)
        self.db.execute(f"UPDATE task SET {','.join(sets)} WHERE tenant=? AND id=?",
                        (*params, self.tenant, tid))
        self.db.commit()

    # ── 경리·자산 ───────────────────────────────────────────────────────
    def add_expense(self, *, request_id: str, requester: str, amount_krw: int,
                    category: str = "", receipt_id: str = "",
                    requester_org: str = "") -> int:
        now = _now()
        return self._insert("expense", request_id=request_id, requester=requester,
                            requester_org=requester_org, amount_krw=amount_krw,
                            category=category, receipt_id=receipt_id,
                            created_at=now, updated_at=now)

    def expenses(self, *, status: str = "", max_level: str = "L3",
                 limit: int = 100) -> list[Row]:
        where, params = ("status=?", (status,)) if status else ("", ())
        return self._rows("expense", where, params, max_level=max_level, limit=limit)

    def expense_by_request(self, request_id: str, *, max_level: str = "L3") -> Row | None:
        r = self.db.execute("SELECT * FROM expense WHERE tenant=? AND request_id=?",
                            (self.tenant, request_id)).fetchone()
        if r is None:
            return None
        if LEVEL_RANK.get(r["security_level"], 3) > LEVEL_RANK.get(max_level, 0):
            return None
        return Row("expense", dict(r))

    def set_expense_verdict(self, request_id: str, *, verdict: str, status: str,
                            processed_by: str, hitl_id: str = "",
                            trace_id: str = "") -> None:
        self.db.execute(
            "UPDATE expense SET verdict=?,status=?,processed_by=?,hitl_id=?,trace_id=?,"
            "updated_at=? WHERE tenant=? AND request_id=?",
            (verdict, status, processed_by, hitl_id, trace_id, _now(),
             self.tenant, request_id))
        self.db.commit()

    def add_fixed_asset(self, *, tag: str, name: str, kind: str = "", holder: str = "",
                        acquired_on: str = "", amount_krw: int = 0) -> int:
        return self._insert("fixed_asset", tag=tag, name=name, kind=kind, holder=holder,
                            acquired_on=acquired_on, amount_krw=amount_krw,
                            created_at=_now())

    def fixed_assets(self, *, max_level: str = "L3", limit: int = 200) -> list[Row]:
        return self._rows("fixed_asset", max_level=max_level, limit=limit)

    # ── 진단 ────────────────────────────────────────────────────────────
    TABLES = ("document", "customer", "inquiry", "contract", "project", "task",
              "expense", "fixed_asset")

    def counts(self) -> dict[str, int]:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant=?",
                                   (self.tenant,)).fetchone()[0]
                for t in self.TABLES}

    def foreign_rows(self) -> int:
        return sum(self.db.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant<>?",
                                   (self.tenant,)).fetchone()[0] for t in self.TABLES)

    def all_asset_refs(self) -> dict[str, int]:
        """이 테넌트의 업무 데이터가 참조하는 EG 자산과 행 수."""
        out: dict[str, int] = {}
        for t in self.TABLES:
            for r in self.db.execute(
                f"SELECT eg_asset, COUNT(*) c FROM {t} WHERE tenant=? GROUP BY eg_asset",
                (self.tenant,),
            ).fetchall():
                out[r["eg_asset"]] = out.get(r["eg_asset"], 0) + r["c"]
        return out

    def export(self, table: str, limit: int = 50) -> str:
        return json.dumps([r.to_dict() for r in self._rows(table, limit=limit)],
                          ensure_ascii=False, indent=2)


__all__ = ["KIND_ASSET", "KIND_LEVEL", "LEVEL_RANK", "BizStore", "Row"]
