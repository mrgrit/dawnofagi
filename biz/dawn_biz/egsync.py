"""업무 데이터 ↔ EG 정합성.

P5 지시문: "데이터를 EG의 Asset으로 등록(kind·irreversibility·SecurityLevel·Zone)."

**등록이 아니라 대조다.** 업무 시스템이 EG 에 노드를 밀어 넣게 하면 EG 가
업무 데이터의 사본이 된다. 여기서는 반대로 한다:

    업무 데이터는 `eg_asset` 으로 **자기가 어느 자산에 속하는지 선언**하고,
    이 모듈이 그 선언이 EG 에서 실재하는지·등급이 맞는지 **검사**한다.

어긋나면 `make biz-egcheck` 가 실패한다. 새 업무 종류를 만들면 EG 시드에
자산을 먼저 추가해야 한다 — 관제가 심각도를 계산할 근거가 거기 있기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .store import KIND_ASSET, KIND_LEVEL, LEVEL_RANK, BizStore


@dataclass
class AssetCheck:
    asset_id: str
    exists: bool = False
    name: str = ""
    zone: str = ""
    security_level: str = ""
    irreversibility: str = ""
    rows: int = 0
    kinds: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exists and not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "ok": self.ok}

    def line(self) -> str:
        mark = "✔" if self.ok else "✘"
        return (f"  {mark} {self.asset_id:<22} {self.name[:22]:<24} "
                f"{self.zone:<10} {self.security_level:<7} "
                f"{self.irreversibility:<13} 행 {self.rows}")


def check(store: BizStore, eg_store) -> list[AssetCheck]:
    """업무 데이터가 참조하는 자산이 EG 에 실재하고 등급이 맞는지."""
    refs = store.all_asset_refs()
    by_asset: dict[str, list[str]] = {}
    for kind, asset in KIND_ASSET.items():
        by_asset.setdefault(asset, []).append(kind)

    out: list[AssetCheck] = []
    for asset_id in sorted(set(KIND_ASSET.values()) | set(refs)):
        c = AssetCheck(asset_id=asset_id, rows=refs.get(asset_id, 0),
                       kinds=sorted(by_asset.get(asset_id, [])))
        if not asset_id:
            c.problems.append("자산을 선언하지 않은 행이 있다 — 관제가 방을 못 정한다")
            out.append(c)
            continue

        node = eg_store.node(asset_id) if eg_store else None
        if node is None:
            c.problems.append(
                f"EG 에 없는 자산이다. eg/seed/04_assets.json 에 먼저 추가하라 "
                f"(참조 종류: {', '.join(c.kinds) or '-'})"
            )
            out.append(c)
            continue

        c.exists = True
        c.name = node.name
        c.irreversibility = node.prop("irreversibility", "")
        zones = [z.id for z in eg_store.out(asset_id, "LOCATED_IN")]
        secs = [s.id for s in eg_store.out(asset_id, "CLASSIFIED_AS")]
        c.zone = zones[0] if zones else ""
        c.security_level = secs[0].replace("sec:", "") if secs else ""

        if not zones:
            c.problems.append("LOCATED_IN 존이 없다 — 픽셀 오피스에서 방을 못 정한다")
        if not secs:
            c.problems.append("CLASSIFIED_AS 등급이 없다 — 심각도 계산이 미분류로 떨어진다")

        # 업무 데이터의 기본 등급이 자산 등급을 **넘으면** 안 된다.
        # (자산보다 민감한 데이터를 그 자산에 넣는 셈이다)
        asset_rank = LEVEL_RANK.get(c.security_level, -1)
        for kind in c.kinds:
            row_rank = LEVEL_RANK.get(KIND_LEVEL.get(kind, "L1"), 0)
            if asset_rank >= 0 and row_rank > asset_rank:
                c.problems.append(
                    f"{kind} 기본 등급 {KIND_LEVEL[kind]} > 자산 등급 "
                    f"{c.security_level} — 자산보다 민감한 데이터를 담고 있다"
                )
        out.append(c)
    return out


def summary(checks: list[AssetCheck]) -> dict[str, Any]:
    return {
        "assets": len(checks),
        "ok": sum(1 for c in checks if c.ok),
        "problems": [f"{c.asset_id}: {p}" for c in checks for p in c.problems],
        "rows": sum(c.rows for c in checks),
    }


def zone_rows(store: BizStore, eg_store) -> dict[str, int]:
    """존별 업무 데이터 행 수 — 픽셀 오피스가 방을 채우는 근거."""
    out: dict[str, int] = {}
    for asset_id, n in store.all_asset_refs().items():
        node = eg_store.node(asset_id) if eg_store else None
        zones = [z.id for z in eg_store.out(asset_id, "LOCATED_IN")] if node else []
        key = zones[0] if zones else "(미배정)"
        out[key] = out.get(key, 0) + n
    return out


__all__ = ["AssetCheck", "check", "summary", "zone_rows"]
