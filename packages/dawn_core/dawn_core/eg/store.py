"""EG 스토어 — bastion_graph.db 와 **와이어 호환**되는 그래프 저장소.

경로 A(BOOTSTRAP.md): 거버넌스 계층을 bastion 런타임 EG 와 같은 DB·같은 스키마에 얹는다.

    거버넌스 (layer=governance)  OrgUnit·Persona·Policy·SecurityLevel·Zone·Asset
                                 ·AutonomyLevel·ModelPolicy   ← 사람이 채움
    런타임   (layer=runtime)     Task·Finding·Observation·Skill ← 에이전트가 축적
                                 + bastion 기존 Playbook·Experience·Concept…

왜 bastion.KnowledgeGraph 를 직접 임포트하지 않는가:
  1. bastion 의 NODE_TYPES/EDGE_TYPES 는 거버넌스 8종을 모르므로 add_node 가 거부한다.
     런타임에 그 전역 집합을 패치하는 것은 el34 를 침범하는 셈이고 깨지기 쉽다.
  2. **fresh Linux 배포에 el34 가 없어도 EG 가 서야 한다.** 임포트 의존이면 못 선다.

그래서 스키마(테이블·인덱스·FTS5)를 그대로 복제한 얇은 스토어를 둔다.
같은 파일을 bastion 이 열어도, 우리가 열어도 동작한다 — `BASTION_GRAPH_DB` 로 가리키면 된다.
bastion 시드 DB 위에 얹으려면 `--from-bastion-seed` 를 쓴다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 타입 (eg/schema.json 과 일치해야 한다 — validate.py 가 대조한다) ──────
GOVERNANCE_NODE_TYPES = {
    "OrgUnit",
    "Persona",
    "Policy",
    "SecurityLevel",
    "Zone",
    "Asset",
    "AutonomyLevel",
    "ModelPolicy",
}
RUNTIME_NODE_TYPES = {"Task", "Finding", "Observation", "Skill"}

GOVERNANCE_EDGE_TYPES = {
    "PART_OF",
    "HAS_PERSONA",
    "USES_MODEL",
    "OPERATES_AT",
    "GOVERNED_BY",
    "APPLIES_TO",
    "CLASSIFIED_AS",
    "LOCATED_IN",
    "MAPS_TO",
    "ACTS_ON",
    "OWNED_BY",
}
RUNTIME_EDGE_TYPES = {"PERFORMED_BY", "TOUCHED", "ABOUT", "OBSERVES", "CONSTRAINED_BY"}

NODE_TYPES = GOVERNANCE_NODE_TYPES | RUNTIME_NODE_TYPES
EDGE_TYPES = GOVERNANCE_EDGE_TYPES | RUNTIME_EDGE_TYPES

# bastion 이 이미 쓰는 타입 — 같은 DB 를 공유하므로 읽을 때 거부하면 안 된다.
BASTION_NODE_TYPES = {
    "Playbook",
    "Experience",
    "Error",
    "Recovery",
    "Concept",
    "Insight",
    "Narrative",
    "Anchor",
    "Mission",
    "Vision",
    "Goal",
    "Strategy",
    "KPI",
    "Plan",
    "Todo",
    "Harness",
}

# bastion 과 같은 스키마. 한 글자도 바꾸지 않는다 — 호환이 목적이다.
SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    content     TEXT DEFAULT '{}',
    embedding   BLOB,
    meta        TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    type        TEXT NOT NULL,
    weight      REAL DEFAULT 1.0,
    meta        TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(src, dst, type)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED,
    type UNINDEXED,
    name,
    content_text,
    tokenize='unicode61 remove_diacritics 1'
);
"""


class EGError(Exception):
    """EG 스토어 오류."""


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    name: str
    content: dict[str, Any]
    meta: dict[str, Any]

    @property
    def layer(self) -> str:
        return self.meta.get("layer", "unknown")

    def prop(self, key: str, default: Any = None) -> Any:
        return self.content.get(key, default)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    type: str
    meta: dict[str, Any]


class EGStore:
    """bastion_graph.db 호환 그래프 스토어."""

    def __init__(self, db_path: str | Path, *, strict_types: bool = True) -> None:
        self.db_path = Path(db_path)
        self.strict_types = strict_types
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── 연결 ────────────────────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=15.0)
        c.row_factory = sqlite3.Row
        # WAL — writer 가 reader 를 막지 않는다 (bastion 과 동일 설정)
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 15000")
        return c

    def _init_schema(self) -> None:
        with self._conn() as c:
            for stmt in SCHEMA.strip().split(";\n"):
                if stmt.strip():
                    c.execute(stmt)
            c.commit()

    # ── 쓰기 ────────────────────────────────────────────────────────────
    def upsert_node(
        self,
        node_id: str,
        type: str,
        name: str,
        content: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        if self.strict_types and type not in NODE_TYPES:
            raise EGError(f"알 수 없는 노드 타입: {type} (허용: {sorted(NODE_TYPES)})")
        content = content or {}
        meta = meta or {}
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO nodes (id, type, name, content, meta, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    type = excluded.type, name = excluded.name,
                    content = excluded.content, meta = excluded.meta,
                    updated_at = datetime('now')
                """,
                (
                    node_id,
                    type,
                    name,
                    json.dumps(content, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            c.execute("DELETE FROM nodes_fts WHERE id = ?", (node_id,))
            c.execute(
                "INSERT INTO nodes_fts (id, type, name, content_text) VALUES (?,?,?,?)",
                (node_id, type, name, _fts_text(name, content)),
            )
            c.commit()
        return node_id

    def upsert_edge(
        self, src: str, dst: str, type: str, meta: dict[str, Any] | None = None
    ) -> None:
        if self.strict_types and type not in EDGE_TYPES:
            raise EGError(f"알 수 없는 엣지 타입: {type}")
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO edges (src, dst, type, weight, meta)
                VALUES (?, ?, ?, 1.0, ?)
                ON CONFLICT(src, dst, type) DO UPDATE SET meta = excluded.meta
                """,
                (src, dst, type, json.dumps(meta or {}, ensure_ascii=False)),
            )
            c.commit()

    def delete_layer(self, layer: str) -> int:
        """해당 layer 의 노드와 그에 연결된 엣지를 지운다 (재주입 전 정리용)."""
        with self._conn() as c:
            ids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM nodes WHERE json_extract(meta,'$.layer') = ?", (layer,)
                )
            ]
            if not ids:
                return 0
            ph = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM edges WHERE src IN ({ph}) OR dst IN ({ph})", ids * 2)
            c.execute(f"DELETE FROM nodes WHERE id IN ({ph})", ids)
            c.execute(f"DELETE FROM nodes_fts WHERE id IN ({ph})", ids)
            c.commit()
        return len(ids)

    # ── 읽기 ────────────────────────────────────────────────────────────
    def node(self, node_id: str) -> Node | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def nodes(self, type: str | None = None, layer: str | None = None) -> list[Node]:
        q, params = "SELECT * FROM nodes", []
        where = []
        if type:
            where.append("type = ?")
            params.append(type)
        if layer:
            where.append("json_extract(meta,'$.layer') = ?")
            params.append(layer)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id"
        with self._conn() as c:
            return [_row_to_node(r) for r in c.execute(q, params)]

    def edges(self, type: str | None = None) -> list[Edge]:
        q, params = "SELECT src, dst, type, meta FROM edges", []
        if type:
            q += " WHERE type = ?"
            params.append(type)
        with self._conn() as c:
            return [
                Edge(r["src"], r["dst"], r["type"], json.loads(r["meta"] or "{}"))
                for r in c.execute(q, params)
            ]

    def out(self, node_id: str, edge_type: str | None = None) -> list[Node]:
        """node_id 에서 나가는 엣지의 목적지 노드들."""
        q = "SELECT n.* FROM edges e JOIN nodes n ON n.id = e.dst WHERE e.src = ?"
        params = [node_id]
        if edge_type:
            q += " AND e.type = ?"
            params.append(edge_type)
        with self._conn() as c:
            return [_row_to_node(r) for r in c.execute(q, params)]

    def inc(self, node_id: str, edge_type: str | None = None) -> list[Node]:
        """node_id 로 들어오는 엣지의 출발 노드들."""
        q = "SELECT n.* FROM edges e JOIN nodes n ON n.id = e.src WHERE e.dst = ?"
        params = [node_id]
        if edge_type:
            q += " AND e.type = ?"
            params.append(edge_type)
        with self._conn() as c:
            return [_row_to_node(r) for r in c.execute(q, params)]

    def search(self, query: str, type: str | None = None, limit: int = 20) -> list[Node]:
        """FTS5 전문 검색 — eg_search 의 기반."""
        q = "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.id = f.id WHERE nodes_fts MATCH ?"
        params: list[Any] = [query]
        if type:
            q += " AND f.type = ?"
            params.append(type)
        q += " ORDER BY rank LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            try:
                return [_row_to_node(r) for r in c.execute(q, params)]
            except sqlite3.OperationalError:
                # FTS 구문 오류(특수문자 등) → 이름 LIKE 로 폴백
                like = f"%{query}%"
                return [
                    _row_to_node(r)
                    for r in c.execute(
                        "SELECT * FROM nodes WHERE name LIKE ? OR content LIKE ? LIMIT ?",
                        (like, like, limit),
                    )
                ]

    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            nodes = {
                r["type"]: r["n"]
                for r in c.execute("SELECT type, COUNT(*) n FROM nodes GROUP BY type")
            }
            edges = {
                r["type"]: r["n"]
                for r in c.execute("SELECT type, COUNT(*) n FROM edges GROUP BY type")
            }
            layers = {
                (r["layer"] or "unknown"): r["n"]
                for r in c.execute(
                    "SELECT json_extract(meta,'$.layer') layer, COUNT(*) n "
                    "FROM nodes GROUP BY layer"
                )
            }
        return {
            "db": str(self.db_path),
            "nodes_total": sum(nodes.values()),
            "edges_total": sum(edges.values()),
            "nodes_by_type": dict(sorted(nodes.items())),
            "edges_by_type": dict(sorted(edges.items())),
            "nodes_by_layer": dict(sorted(layers.items())),
        }

    def dump(self, layer: str | None = None) -> dict[str, Any]:
        """스냅샷용 덤프 (롤백·감사)."""
        ns = self.nodes(layer=layer)
        ids = {n.id for n in ns}
        es = [e for e in self.edges() if e.src in ids and e.dst in ids] if layer else self.edges()
        return {
            "nodes": [
                {"id": n.id, "type": n.type, "name": n.name, "content": n.content, "meta": n.meta}
                for n in ns
            ],
            "edges": [{"type": e.type, "from": e.src, "to": e.dst} for e in es],
        }


# ── 헬퍼 ────────────────────────────────────────────────────────────────


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        content=json.loads(row["content"] or "{}"),
        meta=json.loads(row["meta"] or "{}"),
    )


def _fts_text(name: str, content: dict[str, Any]) -> str:
    """FTS 색인 텍스트. 거버넌스 노드는 사람이 검색할 만한 필드를 모두 넣는다."""
    parts: list[str] = [name]
    for key in (
        "mission",
        "statement",
        "rule",
        "role",
        "tone",
        "label",
        "handling_rule",
        "gate_rule",
        "promote_kpi",
        "rationale",
        "escalation_rule",
        "model",
        "sensitivity",
        "cidr",
        "pixel_room",
        "kind",
        "irreversibility",
        "category",
        "enforcement",
        "source_ref",
        "model_constraint",
        "owner_org",
    ):
        v = content.get(key)
        if isinstance(v, str):
            parts.append(v)
    for key in ("principles", "prohibited", "outputs"):
        v = content.get(key)
        if isinstance(v, Iterable) and not isinstance(v, str):
            parts.extend(str(x) for x in v)
    return " \n".join(p for p in parts if p)
