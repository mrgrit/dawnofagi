"""EG — 시드 정합성 · 핵심 순회 · fail-safe · 통제 평면 브리지.

실물 `eg/seed/*.json` 을 임시 DB 에 주입해서 검증한다.
시드를 잘못 고치면 여기서 잡힌다.
"""

from __future__ import annotations

import json

import pytest
from dawn_core import Registry
from dawn_core.eg import (
    EGStore,
    all_severities,
    autonomy_violations,
    gate_for,
    load,
    model_for_org,
    org_profile,
    severity_of,
)
from dawn_core.eg import bridge as eg_bridge
from dawn_core.eg.loader import check_references, derive_owned_by, read_seeds
from dawn_core.paths import Paths

# 설계서(docs/context/02_eg_schema.md §4)가 명시한 시드 규모: 노드 74 · 엣지 136.
# 엣지가 137 인 이유: P2 에서 `USES_MODEL org:ccc → model:gptoss` 를 추가했다.
# CCC 는 L3 자산(asset:fw-ips · asset:secrets)을 소유하는데 클라우드 모델만
# 배정돼 있어 pol:l3-local-only 가 자기 자산 조회를 block 했다 —
# P1 에서 org:ga 에 있었던 것과 같은 종류의 충돌. 로컬 경로를 열어 해소했다.
EXPECTED_NODES = 74
EXPECTED_EDGES = 137


@pytest.fixture(scope="module")
def paths() -> Paths:
    return Paths()


@pytest.fixture(scope="module")
def seed_dir(paths):
    return paths.root / "eg" / "seed"


@pytest.fixture(scope="module")
def store(tmp_path_factory, paths, seed_dir) -> EGStore:
    db = tmp_path_factory.mktemp("eg") / "test_graph.db"
    load(seed_dir, db)
    return EGStore(db)


# ── 시드 규모·정합 ───────────────────────────────────────────────────────


def test_seed_scale_matches_design(seed_dir):
    seeds = read_seeds(seed_dir)
    assert sum(len(s.nodes) for s in seeds) == EXPECTED_NODES
    assert sum(len(s.edges) for s in seeds) == EXPECTED_EDGES


def test_seed_references_are_intact(seed_dir):
    assert check_references(read_seeds(seed_dir)) == []


def test_company_is_renamed(store):
    assert store.node("org:el34") is None, "구 회사명 노드가 남아 있다"
    dawn = store.node("org:dawn")
    assert dawn is not None
    assert "dawn of AGI" in dawn.name
    assert "AGI로 가는 길" in dawn.prop("mission")


def test_all_seed_nodes_are_governance_layer(store):
    gov = store.nodes(layer="governance")
    assert len(gov) == EXPECTED_NODES
    for n in gov:
        assert n.meta.get("created_by"), f"{n.id}: provenance(created_by) 없음"
        assert n.meta.get("updated_at"), f"{n.id}: provenance(updated_at) 없음"


def test_owned_by_is_derived_for_every_asset(seed_dir, store):
    """owner_org 속성만 있고 엣지가 없던 자산도 순회 가능해야 한다."""
    seeds = read_seeds(seed_dir)
    assert derive_owned_by(seeds), "파생 대상이 없다 — 시드가 바뀌었나?"
    for a in store.nodes(type="Asset"):
        owner = a.prop("owner_org")
        if not owner:
            continue
        owners = {o.id for o in store.out(a.id, "OWNED_BY")}
        assert owner in owners, f"{a.id}: owner_org={owner} 인데 OWNED_BY 엣지가 없다"


# ── 핵심 순회 ①  심각도 ─────────────────────────────────────────────────


def test_severity_is_irreversibility_plus_rank(store):
    s = severity_of(store, "asset:payment")  # irreversible(3) × L3(3)
    assert s.score == 6
    assert s.label == "최고"
    s = severity_of(store, "asset:web-search")  # read(0) × L0(0)
    assert s.score == 0
    assert s.label == "낮음"


def test_highest_severity_assets_are_the_irreversible_l3_ones(store):
    top = [s.asset_id for s in all_severities(store) if s.classified and s.score == 6]
    for expected in ("asset:payment", "asset:pii", "asset:ledger", "asset:payroll", "asset:fw-ips"):
        assert expected in top, f"{expected} 가 최고 심각도가 아니다"


def test_unclassified_asset_fails_safe_to_max(tmp_path, seed_dir):
    """미분류를 '낮음'으로 읽으면 안 된다. 등급 없음 = 아직 판단 안 됨 = 최고로 취급."""
    db = tmp_path / "g.db"
    load(seed_dir, db)
    store = EGStore(db)
    store.upsert_node(
        "asset:unknown-thing",
        "Asset",
        "정체불명 자산",
        {"kind": "system", "irreversibility": "write"},
        {"layer": "runtime"},
    )
    s = severity_of(store, "asset:unknown-thing")
    assert s.unclassified
    assert s.sec_rank == 3, "미분류인데 rank 0 으로 떨어졌다 — fail-safe 실패"
    assert s.score == 4

    g = gate_for(store, "asset:unknown-thing")
    assert not g.classified
    assert g.requires_hitl, "미분류 자산에 HITL 이 안 걸린다"


# ── 핵심 순회 ②  게이트 ─────────────────────────────────────────────────


def test_l3_assets_are_blocked_and_hitl(store):
    for aid in ("asset:pii", "asset:payment", "asset:ledger", "asset:payroll"):
        g = gate_for(store, aid)
        assert g.sec_id == "sec:L3"
        assert g.strongest == "block"
        assert g.requires_hitl
        assert "pol:l3-local-only" in [p.id for p in g.policies]


def test_public_asset_has_no_gate(store):
    g = gate_for(store, "asset:web-search")
    assert g.sec_id == "sec:L0"
    assert not g.requires_hitl
    assert g.policies == []


def test_gate_strength_ordering(store):
    """여러 정책이 걸리면 가장 센 것이 이긴다."""
    g = gate_for(store, "asset:crm")  # L2
    assert {"block", "require_hitl", "log_only"} <= g.enforcements
    assert g.strongest == "block"


# ── 핵심 순회 ③  개입 지점 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "org_id, persona_id",
    [
        ("org:ccc", "persona:secops"),
        ("org:hr", "persona:corporate"),
        ("org:ax-sec", "persona:consulting"),
        ("org:aoc-dev", "persona:aoc-dev"),
    ],
)
def test_org_to_persona_chain(store, org_id, persona_id):
    prof = org_profile(store, org_id)
    assert persona_id in [p.id for p in prof.personas]
    assert prof.policies, f"{org_id}: 페르소나 경유 정책이 없다"


def test_persona_is_inherited_when_org_has_none(store):
    """자기 페르소나가 없는 조직은 상위를 타고 company-default 에 닿는다."""
    prof = org_profile(store, "org:biz-support")
    assert prof.personas, "상속으로도 페르소나에 못 닿는다"
    assert "persona:company-default" in [p.id for p in prof.personas]


def test_every_org_reaches_a_persona(store):
    for org in store.nodes(type="OrgUnit"):
        assert org_profile(store, org.id).personas, f"{org.id}: 페르소나 미도달"


def test_hr_policies_include_pii_and_local_only(store):
    pols = {p.id for p in org_profile(store, "org:hr").policies}
    assert "pol:pii-hr-fin" in pols
    assert "pol:l3-local-only" in pols


# ── 모델 라우팅 ──────────────────────────────────────────────────────────


def test_model_routing_differs_per_org(store):
    assert model_for_org(store, "org:aoc-dev")["model"] == "CC Opus"
    assert "open model" in model_for_org(store, "org:hr")["model"]
    # org:ccc 는 클라우드(Sonnet) + 로컬(gpt-oss) 둘 다 배정 — L3 경로 확보용
    ccc = {m.prop("model") for m in org_profile(store, "org:ccc").models}
    assert "CC Sonnet" in ccc and "gpt-oss:120b" in ccc


def test_ccc_has_a_local_path_for_its_l3_assets(store):
    """CCC 는 L3 자산(방화벽·자격증명)을 소유한다 — 로컬 모델이 없으면
    pol:l3-local-only 가 자기 자산 조회까지 막는다."""
    r = model_for_org(store, "org:ccc", touches_l3=True)
    assert not r["blocked"], "CCC 가 L3 를 만질 때 쓸 모델이 없다"


def test_l3_forces_local_model(store):
    """pol:l3-local-only — L3 관여 시 로컬이 아니면 차단."""
    r = model_for_org(store, "org:hr", touches_l3=True)
    assert r["forced_local"]
    assert not r["blocked"]
    assert r["policy"] == "pol:l3-local-only"

    r = model_for_org(store, "org:aoc-dev", touches_l3=True)
    assert r["blocked"], "로컬 모델 없는 조직이 L3 를 만졌는데 차단되지 않았다"


def test_ga_is_local_only_after_intervention(store):
    """경리총무팀은 원장·경비(L3)를 다루므로 로컬 전용이어야 한다."""
    prof = org_profile(store, "org:ga")
    assert prof.is_local_only, "org:ga 가 클라우드 모델을 쓴다 — L3 유출 경로"


# ── 자율화 ──────────────────────────────────────────────────────────────


def test_autonomy_violations_catch_hr_and_fin(store):
    v = {(x["org"], x["asset"]) for x in autonomy_violations(store)}
    assert ("org:hr", "asset:payroll") in v
    assert ("org:fin", "asset:ledger") in v


def test_hr_and_fin_operate_at_a0(store):
    for oid in ("org:hr", "org:fin"):
        assert org_profile(store, oid).autonomy.id == "auto:A0"


# ── eg_search ───────────────────────────────────────────────────────────


def test_eg_search_finds_governance_nodes(store):
    assert any(n.id == "pol:l3-local-only" for n in store.search("로컬 모델"))
    assert any(n.type == "Persona" for n in store.search("에이전트", type="Persona"))


def test_eg_search_survives_special_characters(store):
    store.search('"unbalanced (quote')  # 예외 없이 폴백되면 통과


# ── 통제 평면 ↔ EG 브리지 ────────────────────────────────────────────────


def test_bridge_is_consistent(store):
    rep = eg_bridge.check(Registry.load(), store)
    assert rep.ok, "통제 평면과 EG 가 어긋난다:\n" + "\n".join(
        f"  [{i.where}] {i.message}" for i in rep.errors
    )


def test_every_team_maps_to_an_eg_org(store):
    reg = Registry.load()
    for tid, team in reg.teams.items():
        eg_org = team.data.get("eg_org")
        assert eg_org, f"{tid}: eg_org 매핑 없음"
        assert store.node(eg_org) is not None, f"{tid} → {eg_org} 가 EG 에 없다"


def test_bridge_detects_model_policy_conflict(tmp_path, seed_dir):
    """gate 가 local_only 인데 EG 가 클라우드만 배정하면 오류로 잡아야 한다."""
    db = tmp_path / "conflict.db"
    load(seed_dir, db)
    store = EGStore(db)
    # 개입을 되돌려 충돌을 만든다: org:ga 를 다시 클라우드 모델로
    with store._conn() as c:
        c.execute("DELETE FROM edges WHERE src='org:ga' AND type='USES_MODEL'")
        c.execute(
            "INSERT INTO edges (src,dst,type,weight,meta) VALUES "
            "('org:ga','model:haiku','USES_MODEL',1.0,'{}')"
        )
        c.commit()
    rep = eg_bridge.check(Registry.load(), store)
    assert not rep.ok
    assert any("모델 정책 정면 충돌" in i.message for i in rep.errors)


def test_routing_table_covers_all_agents(store):
    reg = Registry.load()
    rows = eg_bridge.routing_table(reg, store)
    assert {r["agent"] for r in rows} == set(reg.agents)
    for r in rows:
        assert r["eg_org"], f"{r['agent']}: EG 조직 미매핑"


# ── 스냅샷 ──────────────────────────────────────────────────────────────


def test_snapshot_roundtrip(tmp_path, seed_dir):
    from dawn_core.eg import snapshot

    db = tmp_path / "s.db"
    load(seed_dir, db)
    path = snapshot(db, tmp_path / "snaps", label="t")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["graph"]["nodes"]) == EXPECTED_NODES
    assert doc["stats"]["nodes_by_layer"]["governance"] == EXPECTED_NODES
