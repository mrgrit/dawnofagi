"""판단 기록 (`Judgment`) — 사람이 무엇을 어떻게 판단했나.

P8 디지털 트윈의 **수집 계층**이다. 예측도 발산 측정도 아직 하지 않는다
(`docs/instructions/P8_digital_twin.md` §5 — 표본이 없으면 모델이 아니라 잡음이다).
지금 켜 두는 이유는 하나다: **나중에 켜면 그 사이의 판단이 영영 사라진다.**

## 새로 수집하지 않는다

이 회사는 이미 **모든 변경에 사유를 요구**한다. 감사를 위해 만든 규칙인데,
부수적으로 사람의 판단 말뭉치를 모으고 있다. 그러니 새 입력창을 만들 게 아니라
이미 흐르는 것을 노드로 만들면 된다 — 사람이 추가로 할 일은 없다.

## 판단이란 무엇인가 — 감사 줄 중에서 고르는 기준

**결정과 사유가 둘 다 있는 감사 줄이 판단이다.**

이 기준이 필요한 이유가 있다. 승인 화면은 권한이 없을 때도 감사에 남기는데,
그 줄에도 `reason` 이 붙는다("당신 등급으로는 이 심각도를 승인할 수 없다").
그건 **시스템이 막은 것**이지 사람이 판단한 게 아니다. 그걸 같이 학습하면
트윈은 "이 사람은 거부한다"를 배운다 — 실제로는 누를 기회조차 없었는데.

그래서 `decision` 키가 명시된 줄만 판단으로 본다. 막힌 줄에는 그 키가 없다.

## 계층 — 왜 governance 가 아닌가

P8 문서는 "거버넌스 계층"이라고 적었지만 **그대로 하면 안 된다.**
`loader.py` 가 `eg-load` 때 `delete_layer("governance")` 를 부른다. 거버넌스는
YAML 에서 언제든 다시 만들 수 있게 설계된 계층이라 통째로 지우고 새로 넣는다.
판단은 다시 만들 수 없다 — 지우면 그것으로 끝이다.

그래서 `layer="judgment"` 로 따로 둔다. 덤으로 §4-④ 도 이 계층 하나로
표현된다: **판단은 L3(개인정보)다.** 클라우드 모델에 보내지 않고, 본인이
열람·삭제할 수 있어야 한다 (`forget`).
"""

from __future__ import annotations

import hashlib
from typing import Any

JUDGMENT_LAYER = "judgment"

# 사유가 이미 강제되는 경로들. 여기 없는 감사 액션은 판단으로 보지 않는다.
# 값은 `source` — "어디서 내린 판단인가". 나중에 발산을 볼 때 경로별로 나눠야
# 한다: 승인 큐의 판단과 EG 조정의 판단은 성격이 다르다.
JUDGMENT_ACTIONS: dict[str, tuple[str, str]] = {
    "hitl.decide": ("hitl", "승인 큐"),
    "portal.order.decide": ("work_order", "작업 지시 결재"),
    "eg.update": ("eg_edit", "EG 조정"),
    "control.save": ("control_edit", "통제 평면 조정"),
    # 에이전트 생성·삭제는 `control.agent.create` / `.delete` 로 남는다.
    # op 가 액션 이름에 붙으므로 정확 일치로는 못 잡는다 — 접두어로 본다.
    "control.agent": ("control_edit", "통제 평면 조정"),
}


def action_source(action: str) -> tuple[str, str] | None:
    """감사 액션 → (source, 표시명). 선언되지 않은 액션이면 None.

    정확 일치를 먼저 보고, 없으면 `<선언>.` 으로 시작하는지 본다. 접두어를
    쓰는 이유는 `control.agent.create` 처럼 **동작이 액션 이름에 붙어 오는**
    경로가 있어서다. 반대로 `.` 없이 붙는 이름은 걸리지 않는다 —
    `control.agentX` 같은 것이 조용히 판단으로 세어지면 안 된다.
    """
    hit = JUDGMENT_ACTIONS.get(action)
    if hit is not None:
        return hit
    for declared, meta in JUDGMENT_ACTIONS.items():
        if action.startswith(declared + "."):
            return meta
    return None

# 상황(situation)으로 남길 키. 트윈이 "무엇 앞에서" 를 재구성하는 재료다.
# 전부 담지 않는 이유: 감사 줄에는 IP·세션 같은 것도 있는데 그건 판단의
# 근거가 아니라 접속 정보다.
_SITUATION_KEYS = (
    "skill", "agent_id", "agent_org", "severity", "assets", "policies",
    "step", "status", "kind", "node_id", "target_kind", "op",
)

_MAX_REASON = 2000


class JudgmentError(Exception):
    """판단을 기록할 수 없다."""


def collecting() -> bool:
    """지금 판단을 쌓아도 되는가.

    `DAWN_JUDGMENT_COLLECT=0` 이면 안 쌓는다. 이 스위치가 있는 이유는 하나다 —
    **테스트가 실 트리에서 돌기 때문이다.** 결재를 흉내내는 테스트가 그대로
    실 판단이 되면 트윈은 "이 사람은 사유 '승인' 으로 승인한다"를 배운다.
    실측으로 판단 5건 중 4건이 그렇게 들어와 있었다.

    끄는 것은 **적재뿐**이다. 감사 로그는 이 스위치와 무관하게 남는다.
    """
    import os

    return os.getenv("DAWN_JUDGMENT_COLLECT", "1").strip() not in ("0", "false", "no")


def _reason_of(detail: dict[str, Any]) -> str:
    """사유는 경로마다 다른 이름으로 온다 — 하나로 모은다."""
    for key in ("note", "reason", "_reason", "why"):
        v = detail.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:_MAX_REASON]
    return ""


# 테스트 클라이언트가 남긴 감사 줄. Starlette `TestClient` 는 클라이언트 주소를
# 이 문자열로 고정한다 — **추론이 아니라 출처가 기록돼 있다.**
TEST_CLIENT_IPS = frozenset({"testclient", "127.0.0.1:testclient"})


def is_judgment(rec: dict[str, Any]) -> bool:
    """이 감사 줄이 사람의 판단인가.

    넷을 모두 만족해야 한다 — 선언된 경로 · 명시된 결정 · 사유 · **사람의 요청**.
    하나라도 없으면 판단으로 세지 않는다. **틀린 표본은 없는 표본보다 나쁘다.**

    마지막 조건이 필요한 이유: `conftest.py` 가 테스트 중 적재를 끄지만
    **백필은 그 스위치가 생기기 전의 이력을 읽는다.** 실측(2026-08-03)으로
    백필이 가져온 3건이 전부 테스트 픽스처였고 — 사유가 "승인" · "범위 밖" ·
    "P4 자기검증" 이었다 — 감사 줄의 `ip` 가 전부 `testclient` 였다.
    스위치는 앞을 막고 이건 뒤를 막는다.
    """
    if action_source(str(rec.get("action", ""))) is None:
        return False
    if str(rec.get("ip", "")).strip() in TEST_CLIENT_IPS:
        return False
    detail = rec.get("detail") or {}
    if not str(detail.get("decision", "")).strip():
        return False
    return bool(_reason_of(detail))


def judgment_id(rec: dict[str, Any]) -> str:
    """같은 판단은 같은 id — 재적재해도 중복이 생기지 않는다."""
    detail = rec.get("detail") or {}
    seed = "|".join([
        str(rec.get("actor", "")), str(rec.get("action", "")),
        str(rec.get("target", "")), str(detail.get("decision", "")),
        str(rec.get("at", "")),
    ])
    return f"judgment:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]}"


def to_node(rec: dict[str, Any]) -> dict[str, Any]:
    """감사 줄 → Judgment 노드 (저장하지 않는다 — 순수 변환이라 테스트가 쉽다)."""
    if not is_judgment(rec):
        raise JudgmentError("판단이 아니다 — 결정과 사유가 둘 다 있어야 한다")
    detail = rec.get("detail") or {}
    source, source_name = action_source(rec["action"])
    decision = str(detail.get("decision", "")).strip()
    actor = str(rec.get("actor", "")) or "-"
    target = str(rec.get("target", ""))
    situation = {k: detail[k] for k in _SITUATION_KEYS if k in detail}

    # 상황은 dict 라 FTS 가 색인하지 못한다. 검색에 걸려야 하는 식별자만
    # 평문으로 한 번 더 적는다 — "ledger 를 건드리는 판단"을 글자로 찾으려면
    # 이게 없으면 엣지를 타는 수밖에 없고, 그건 질문이 자산을 명시할 때뿐이다.
    tags = " ".join(str(x) for x in [
        *(situation.get("assets") or []),
        situation.get("agent_id", ""), situation.get("agent_org", ""),
        situation.get("skill", ""), target, decision, source,
    ] if x)

    return {
        "id": judgment_id(rec),
        "type": "Judgment",
        "name": f"{actor} — {source_name} {decision} ({target})"[:120],
        "content": {
            "actor": actor,
            "action": rec["action"],
            "source": source,
            "target": target,
            "decision": decision,
            "reason": _reason_of(detail),
            "situation": situation,
            "tags": tags,
            "at": rec.get("at", ""),
        },
        "meta": {
            "layer": JUDGMENT_LAYER,
            # L3 — 한 사람의 판단 모델은 개인정보다. 규칙 5(로컬 모델만)가
            # 이 표시를 보고 걸러낸다.
            "sensitivity": "L3",
            "actor": actor,
            "source": source,
        },
    }


def _link_targets(node: dict[str, Any]) -> list[str]:
    """이 판단이 무엇에 대한 것인가 — EG 노드로 이어지는 것만 고른다.

    자산은 판단을 검색 가능하게 만드는 핵심 축이다. "ledger 를 건드리는 일을
    이 사람은 어떻게 판단해 왔나"를 물으려면 엣지가 있어야 한다.
    """
    out: list[str] = []
    situation = node["content"].get("situation") or {}
    for aid in situation.get("assets") or []:
        if isinstance(aid, str) and aid.startswith("asset:"):
            out.append(aid)
    for key in ("agent_org", "node_id"):
        v = situation.get(key)
        if isinstance(v, str) and ":" in v:
            out.append(v)
    tgt = node["content"].get("target", "")
    if ":" in tgt and tgt.split(":", 1)[0] in {"asset", "org", "persona", "pol", "zone"}:
        out.append(tgt)
    return sorted(set(out))


def record(store, rec: dict[str, Any]) -> str | None:
    """감사 줄 하나를 Judgment 노드로 적재한다. 판단이 아니면 None.

    엣지는 **대상이 EG 에 실제로 있을 때만** 건다. 없는 노드로 향하는 엣지는
    그래프를 조용히 오염시킨다 — 순회하다 죽은 링크를 만난다.
    """
    if not is_judgment(rec) or not collecting():
        return None
    node = to_node(rec)
    store.upsert_node(node["id"], node["type"], node["name"],
                      node["content"], node["meta"])
    for dst in _link_targets(node):
        if store.node(dst) is not None:
            store.upsert_edge(node["id"], dst, "ABOUT",
                              {"layer": JUDGMENT_LAYER})
    return node["id"]


# ── 조회 ────────────────────────────────────────────────────────────────
def judgments(store, *, actor: str = "", source: str = "") -> list:
    """판단 목록. `actor` 를 주면 **그 사람 것만** (§4-④ 본인 열람)."""
    out = []
    for n in store.nodes(type="Judgment"):
        if actor and n.meta.get("actor") != actor:
            continue
        if source and n.meta.get("source") != source:
            continue
        out.append(n)
    return sorted(out, key=lambda n: n.content.get("at", ""), reverse=True)


def precedents(store, query: str, *, limit: int = 5) -> list:
    """질문과 관련된 **판례**. 페르소나(추상 원칙)와 다른 것을 준다.

    에이전트가 착수 전에 묻는 것은 원래 "이 회사는 이런 걸 어떻게 보나"인데,
    지금까지는 페르소나밖에 못 찾았다. 페르소나는 적어 둔 원칙이고 판례는
    **실제로 내린 결정**이다. 둘이 어긋나 있다면 그게 곧 P8 이 재려는 발산이다.
    """
    if not (query or "").strip():
        return []

    # 질문이 자산·조직을 **명시**하면 글자가 아니라 엣지로 찾는다. 판단의
    # 사유에 그 자산 이름이 안 나올 수 있어서다 — "월말 마감 대사에 필요하다"
    # 는 asset:ledger 판단이지만 '장부'라는 말이 한 번도 안 나온다.
    hits = _by_edge(store, query, limit)
    if len(hits) >= limit:
        return hits[:limit]

    seen = {n.id for n in hits}
    hits = hits + [n for n in store.search(query, type="Judgment", limit=limit * 3)
                   if n.id not in seen]
    if not hits:
        # 한국어는 조사가 붙어 한 토큰이 된다 — FTS 에서 "한도" 는 "한도를" 에
        # 걸리지 않는다. 접두 검색으로 한 번 더 본다.
        alt = _fts_or_prefix(query)
        if alt:
            hits = store.search(alt, type="Judgment", limit=limit * 3)
    return list(hits)[:limit]


def _by_edge(store, query: str, limit: int) -> list:
    """질문에 적힌 `asset:x` / `org:y` 로 향하는 판단들."""
    import re

    ids = {t for t in re.findall(r"[a-z][a-z0-9_-]*:[a-z0-9_.-]+", query or "")
           if t.split(":", 1)[0] in {"asset", "org", "persona", "pol", "zone"}}
    if not ids:
        return []
    srcs = [e.src for e in store.edges(type="ABOUT")
            if e.dst in ids and e.src.startswith("judgment:")]
    out = [store.node(sid) for sid in dict.fromkeys(srcs)]
    return [n for n in out if n is not None][:limit]


def backfill(store, audit_path, *, apply: bool = False) -> dict[str, Any]:
    """감사 로그를 다시 훑어 Judgment 를 채운다.

    평소에는 필요 없다 — 감사에 줄이 써질 때 같이 적재된다. 이건 **복구용**이다:
    EG DB 를 새로 만들었거나(신규 배포·손상 복구) 적재 훅이 잠깐 죽어 있었을 때,
    감사 로그가 유일한 원본이므로 거기서 되살린다.

    id 가 내용 해시라 여러 번 돌려도 중복이 생기지 않는다.
    """
    import json
    from pathlib import Path

    path = Path(audit_path)
    scanned = recorded = 0
    skipped: dict[str, int] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                skipped["깨진 줄"] = skipped.get("깨진 줄", 0) + 1
                continue
            scanned += 1
            if action_source(str(rec.get("action", ""))) is None:
                continue
            detail = rec.get("detail") or {}
            if str(rec.get("ip", "")).strip() in TEST_CLIENT_IPS:
                skipped["테스트 클라이언트"] = skipped.get("테스트 클라이언트", 0) + 1
                continue
            if not str(detail.get("decision", "")).strip():
                skipped["결정 없음"] = skipped.get("결정 없음", 0) + 1
                continue
            if not _reason_of(detail):
                skipped["사유 없음"] = skipped.get("사유 없음", 0) + 1
                continue
            recorded += 1
            if apply:
                record(store, rec)
    return {"applied": apply, "scanned": scanned, "recorded": recorded,
            "skipped": skipped}


def _fts_or_prefix(query: str) -> str:
    """`한도 초과` → `한도* OR 초과*`.

    문장을 통째로 FTS 에 넣으면 구절 AND 로 해석돼 0건이 나온다. 토큰마다
    접두 검색을 걸고 OR 로 묶는다. 한 글자 토큰은 뺀다 — 아무거나 걸린다.
    """
    import re

    toks = [t for t in re.split(r"[^0-9A-Za-z가-힣_-]+", query or "") if len(t) >= 2]
    return " OR ".join(f"{t}*" for t in toks[:6])


def forget(store, actor: str) -> int:
    """본인 판단 이력 삭제 (§4-④). 지운 수를 돌려준다.

    개인정보라 지울 수 있어야 한다. 감사 로그는 **지우지 않는다** — 그건
    법적 기록이고 append-only 다. 지워지는 것은 트윈의 학습 재료뿐이다.
    """
    if not actor:
        raise JudgmentError("actor 가 필요하다 — 전체 삭제는 이 함수로 못 한다")
    ids = [n.id for n in store.nodes(type="Judgment") if n.meta.get("actor") == actor]
    for nid in ids:
        store.delete_node(nid)
    return len(ids)


__all__ = [
    "JUDGMENT_ACTIONS", "JUDGMENT_LAYER", "JudgmentError", "action_source",
    "collecting", "forget", "is_judgment", "judgment_id", "judgments",
    "precedents", "record", "to_node",
]
