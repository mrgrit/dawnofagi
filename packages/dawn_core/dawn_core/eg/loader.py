"""EG 시드 로더 — eg/seed/*.json 을 그래프에 주입한다.

BOOTSTRAP.md 경로 A 의 실동작 구현.
의사코드였던 것을 실제로 돌아가게 만든 것이며, 다음을 추가로 강제한다:

  · `layer='governance'` 를 모든 거버넌스 노드에 붙인다 (런타임과 섞이지 않게)
  · `created_by` / `updated_at` provenance 를 채운다 (개입 추적 — schema.json conventions)
  · 주입 **전에** 참조 무결성을 검사한다 — 깨진 그래프를 만들지 않는다
  · 재주입은 거버넌스 계층만 교체한다 (런타임 축적분은 건드리지 않는다)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import GOVERNANCE_NODE_TYPES, EGStore

SEED_ORDER = ["01_foundation", "02_policies", "03_personas", "04_assets"]


class LoadError(Exception):
    """시드 로드 실패. 이게 나면 아무것도 주입하지 않는다."""


@dataclass
class Seed:
    """eg/seed/*.json 하나."""

    path: Path
    nodes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (type, props)
    edges: list[dict[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.stem


@dataclass
class LoadResult:
    nodes_upserted: int
    edges_upserted: int
    edges_derived: int
    removed_stale: int
    seeds: list[str]
    db_path: str
    stats: dict[str, Any]


def read_seeds(seed_dir: Path) -> list[Seed]:
    """시드 디렉터리를 정해진 순서로 읽는다."""
    files = sorted(seed_dir.glob("*.json"))
    if not files:
        raise LoadError(f"시드가 없다: {seed_dir}")

    known = {f.stem: f for f in files}
    ordered = [known[n] for n in SEED_ORDER if n in known]
    ordered += [f for f in files if f.stem not in SEED_ORDER]

    seeds: list[Seed] = []
    for path in ordered:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LoadError(f"{path}: JSON 파싱 실패 — {exc}") from exc

        seed = Seed(path=path, meta=doc.get("_meta") or {})
        for key, val in doc.items():
            if key.startswith("_") or key == "edges":
                continue
            if not isinstance(val, list):
                raise LoadError(f"{path}: '{key}' 가 리스트가 아니다")
            if key not in GOVERNANCE_NODE_TYPES:
                raise LoadError(
                    f"{path}: 거버넌스 노드 타입이 아니다 — {key} "
                    f"(허용: {sorted(GOVERNANCE_NODE_TYPES)})"
                )
            for item in val:
                if "id" not in item:
                    raise LoadError(f"{path}: {key} 항목에 id 가 없다 — {item}")
                seed.nodes.append((key, item))

        for e in doc.get("edges", []):
            missing = {"type", "from", "to"} - set(e)
            if missing:
                raise LoadError(f"{path}: 엣지에 {missing} 누락 — {e}")
            seed.edges.append(e)

        seeds.append(seed)
    return seeds


def _display_name(ntype: str, props: dict[str, Any]) -> str:
    """노드 타입별로 사람이 읽을 이름을 고른다."""
    for key in ("name", "label", "role", "statement", "model"):
        v = props.get(key)
        if isinstance(v, str) and v:
            return v
    return props["id"]


def derive_owned_by(seeds: list[Seed]) -> list[dict[str, str]]:
    """Asset.owner_org 속성으로부터 빠진 OWNED_BY 엣지를 파생한다.

    전달받은 시드는 21개 Asset 전부에 `owner_org` 속성을 갖지만
    `OWNED_BY` 엣지는 6개만 명시한다(총 엣지 136 — 설계서 수치와 일치).
    속성만 있고 엣지가 없으면 `OrgUnit -OWNED_BY- Asset` 순회가 조용히 틀린다
    (예: org:hr 이 asset:payroll 을 못 본다 → 자율화 게이트 판정 누락).

    시드 파일은 원본 그대로 두고, **주입 시점에** 파생 엣지를 채운다.
    파생분은 meta.derived=true 로 표시되어 원본과 구분된다.
    """
    declared = {
        (e["from"], e["to"], e["type"]) for s in seeds for e in s.edges if e["type"] == "OWNED_BY"
    }
    org_ids = {p["id"] for s in seeds for t, p in s.nodes if t == "OrgUnit"}
    derived: list[dict[str, str]] = []
    for s in seeds:
        for ntype, props in s.nodes:
            if ntype != "Asset":
                continue
            owner = props.get("owner_org")
            if not owner or owner not in org_ids:
                continue
            key = (props["id"], owner, "OWNED_BY")
            if key not in declared:
                derived.append({"type": "OWNED_BY", "from": props["id"], "to": owner})
                declared.add(key)
    return derived


def check_references(seeds: list[Seed]) -> list[str]:
    """주입 전 참조 무결성. 엣지의 from/to 가 전부 실존하는가."""
    ids = {props["id"] for s in seeds for _, props in s.nodes}
    errs: list[str] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for s in seeds:
        for e in s.edges:
            for side in ("from", "to"):
                if e[side] not in ids:
                    errs.append(
                        f"{s.path.name}: 엣지 {e['type']} 의 {side}='{e[side]}' 노드가 없다"
                    )
            key = (e["from"], e["to"], e["type"])
            if key in seen_edges:
                errs.append(f"{s.path.name}: 중복 엣지 — {e['type']} {e['from']}→{e['to']}")
            seen_edges.add(key)

    dupes: dict[str, int] = {}
    for s in seeds:
        for _, props in s.nodes:
            dupes[props["id"]] = dupes.get(props["id"], 0) + 1
    errs += [f"노드 id 중복 — {i} ({n}회)" for i, n in dupes.items() if n > 1]
    return errs


def load(
    seed_dir: Path,
    db_path: Path,
    *,
    replace: bool = True,
    from_bastion_seed: Path | None = None,
    dry_run: bool = False,
) -> LoadResult:
    """시드를 그래프에 주입한다.

    Args:
        replace: True 면 기존 governance 계층을 먼저 지운다 (런타임 계층은 보존).
        from_bastion_seed: bastion 시드 DB 를 복사해 그 위에 얹는다 (경로 A 완전판).
    """
    seeds = read_seeds(seed_dir)

    errs = check_references(seeds)
    if errs:
        raise LoadError(
            "참조 무결성 실패 — 주입하지 않는다:\n" + "\n".join(f"  - {e}" for e in errs)
        )

    derived = derive_owned_by(seeds)

    if dry_run:
        n = sum(len(s.nodes) for s in seeds)
        e = sum(len(s.edges) for s in seeds)
        return LoadResult(n, e, len(derived), 0, [s.name for s in seeds], str(db_path), {})

    # bastion 런타임 EG 위에 얹기 (경로 A)
    if from_bastion_seed and not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(from_bastion_seed, db_path)

    store = EGStore(db_path)
    removed = store.delete_layer("governance") if replace else 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_nodes = n_edges = 0

    for seed in seeds:
        created_by = seed.meta.get("created_by", "founder")
        for ntype, props in seed.nodes:
            content = {k: v for k, v in props.items() if k != "id"}
            store.upsert_node(
                node_id=props["id"],
                type=ntype,
                name=_display_name(ntype, props),
                content=content,
                meta={
                    "layer": "governance",
                    "created_by": created_by,
                    "updated_at": now,
                    "seed": seed.name,
                },
            )
            n_nodes += 1

    for seed in seeds:
        for e in seed.edges:
            store.upsert_edge(
                e["from"],
                e["to"],
                e["type"],
                meta={"layer": "governance", "seed": seed.name},
            )
            n_edges += 1

    # Asset.owner_org 속성만 있고 엣지가 없는 것들을 채운다 (derive_owned_by 참조)
    for e in derived:
        store.upsert_edge(
            e["from"],
            e["to"],
            e["type"],
            meta={
                "layer": "governance",
                "seed": "derived",
                "derived": True,
                "from_property": "owner_org",
            },
        )

    return LoadResult(
        nodes_upserted=n_nodes,
        edges_upserted=n_edges,
        edges_derived=len(derived),
        removed_stale=removed,
        seeds=[s.name for s in seeds],
        db_path=str(db_path),
        stats=store.stats(),
    )


def snapshot(db_path: Path, out_dir: Path, label: str = "governance") -> Path:
    """주입 후 그래프 상태를 덤프한다 (롤백·감사용)."""
    store = EGStore(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(db_path),
        "stats": store.stats(),
        "graph": store.dump(layer="governance"),
    }
    path = out_dir / f"{label}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
