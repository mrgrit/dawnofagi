"""에이전트와의 **읽기 전용** 대화 — 관제 콘솔에서 직접 묻는다.

콘솔에서 에이전트를 클릭하면 그 에이전트에게 바로 물을 수 있다.
"아까 그 판단 왜 그렇게 했나", "지금 뭘 할 수 있나", "예전에 비슷한 일 있었나".

에이전트는 네 가지를 근거로 답한다. 근거를 넣는 순서가 곧 답변 품질이다:

    1. SOUL.md      — 나는 누구인가
    2. 게이트        — 내가 무엇을 할 수 있는가 (권한의 진실은 gate.yaml 이다)
    3. 자기 trace    — 내가 실제로 무엇을 했는가
    4. EG           — 이 회사가 전에 무엇을 겪었는가

**도구를 주지 않는다.** llm.complete() 만 태운다 — 대화 중에 파일을 고치거나
명령을 실행하는 경로가 아예 없다. 통제 평면을 대화창으로 우회할 수 있으면
gate.yaml 이 선언하는 권한이 거짓이 되기 때문이다.

모델도 임의로 고르지 않는다. 그 에이전트가 EG 에서 배정받은 모델을 그대로 쓴다 —
대화라고 해서 더 좋은 모델을 붙이면, 화면에서 보는 답이 실제 그 에이전트의
능력이 아니게 된다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

MAX_RUNS = 6          # 최근 실행 몇 건을 근거로 넣나
MAX_EG_HITS = 6       # EG 검색 상위 몇 건
MAX_OUTPUT_CHARS = 900  # 실행 산출물은 잘라 넣는다 — 컨텍스트를 다 먹지 않게


class ConverseError(Exception):
    """대화를 성립시킬 수 없다 — 원인을 그대로 화면에 띄운다."""


# ── 근거 수집 ────────────────────────────────────────────────────────────
def _soul(root: Path, agent_id: str) -> str:
    p = root / "org" / "agents" / agent_id / "SOUL.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _eg_db(root: Path) -> Path:
    """EG DB 경로 — dawn_core.eg.cli.db_path 와 같은 규칙."""
    for env in ("EG_DB_PATH", "BASTION_GRAPH_DB"):
        v = os.getenv(env, "").strip()
        if v:
            return Path(v).expanduser()
    return root / "var" / "eg" / "bastion_graph.db"


def _routing(root: Path, agent_id: str) -> dict[str, Any]:
    """이 에이전트의 조직·게이트·배정 모델. EG 가 없으면 여기서 끊는다."""
    from dawn_core.eg.bridge import routing_table
    from dawn_core.eg.store import EGStore
    from dawn_core.eg.traverse import model_for_org
    from dawn_core.registry import Registry

    db = _eg_db(root)
    if not db.is_file():
        raise ConverseError(f"EG DB 가 없다: {db} — make eg-load 먼저 실행하라")

    store = EGStore(db)
    rows = {r["agent"]: r for r in routing_table(Registry.load(root), store)}
    row = rows.get(agent_id)
    if row is None:
        raise ConverseError(f"레지스트리에 없는 에이전트다: {agent_id}")

    eg_org = row.get("eg_org")
    if not eg_org:
        raise ConverseError(f"{agent_id} 의 팀에 eg_org 가 없다")

    m = model_for_org(store, eg_org, touches_l3=False)
    if m.get("blocked") or not m.get("model_id"):
        raise ConverseError(m.get("reason") or f"{eg_org} 에 모델 배정이 없다")

    return {"store": store, "eg_org": eg_org, "model_id": m["model_id"],
            "model_name": m.get("model"), "gate_policy": row.get("gate_policy", "")}


def _recent_runs(root: Path, agent_id: str, limit: int = MAX_RUNS) -> list[Any]:
    """이 에이전트의 최근 실행. 최신부터 훑고 필요한 만큼만 모은다."""
    from dawn_aoc.collect import TraceLake

    lake = TraceLake(root)
    out: list[Any] = []
    for tid in reversed(lake.trace_ids()):
        for run in lake.normalize(tid):
            if getattr(run, "agent_id", "") == agent_id:
                out.append(run)
        if len(out) >= limit:
            break
    return out[:limit]


# 질문에 흔히 섞이는 말 — 검색어로 쓰면 아무거나 걸린다.
_STOP = {
    "그리고", "그래서", "무슨", "무엇", "어떻게", "했나", "한다", "있나", "없나",
    "왜", "언제", "누가", "지금", "최근", "관련", "대해", "대한", "정도", "경우",
    "그렇게", "이렇게", "그때", "지난", "이번", "다시", "혹시", "정말", "제대로",
}

# 한국어는 명사에 조사·어미가 붙어 온다("판단했나"). 접두 검색만으로는
# "그렇게"·"했고" 같은 잡음이 그대로 검색어가 되어 EG 히트를 흐린다.
# 뒤에서부터 두 번까지 떼어내고, 남은 게 2자 미만이면 버린다.
_PARTICLE = re.compile(
    r"(으로|에서|에게|부터|까지|이나|라도|처럼|보다|마다|한테|였나|였다|했나|했고"
    r"|한다|하고|하는|인가|인지|들이|들을|은|는|이|가|을|를|에|의|로|와|과|도|만"
    r"|고|서|며|면|지|다|나)$"
)


def _stem(tok: str) -> str:
    for _ in range(2):
        stripped = _PARTICLE.sub("", tok)
        if stripped == tok:
            break
        tok = stripped
    return tok


def _keywords(question: str, limit: int = 6) -> list[str]:
    """한국어 질문에서 검색어를 뽑는다.

    FTS 는 문장을 통째로 넣으면 0건이다(구절 AND 로 해석). 실측:
        '최근에 무슨 일을 했고, 왜 그렇게 판단했나?' → 0건
        '판단*'                                  → 5건
    한글은 조사가 붙으므로(판단했나) 앞 4자만 잘라 접두 검색으로 잡는다.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tok in re.split(r"[^0-9A-Za-z가-힣_-]+", question):
        tok = tok.strip()
        if tok in _STOP:
            continue
        key = _stem(tok) if re.search(r"[가-힣]", tok) else tok
        if len(key) < 2 or key in _STOP:
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:limit]


def _search(store, query: str, limit: int) -> list:
    try:
        return store.search(query, limit=limit)
    except Exception:
        return []


def _eg_hits(store, agent_id: str, question: str, limit: int = MAX_EG_HITS) -> list[dict[str, str]]:
    """EG 를 두 갈래로 조회한다 — 대화가 곧 EG 동작 점검이 되는 지점이다.

    1. 에이전트 ID  — 이 에이전트가 EG 에 남긴 자기 경험
    2. 질문 키워드  — 회사가 전에 겪은 관련 사례
    """
    hits: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(nodes, why: str):
        for n in nodes:
            if n.id in seen:
                continue
            seen.add(n.id)
            hits.append({"id": n.id, "type": n.type, "name": n.name,
                         "layer": n.layer, "why": why})

    add(_search(store, agent_id, limit), "내 경험")
    kws = _keywords(question)
    if kws:
        add(_search(store, " OR ".join(f"{k}*" for k in kws), limit), "질문 관련")
    return hits[: limit * 2]


# ── 프롬프트 조립 ────────────────────────────────────────────────────────
def _fmt_runs(root: Path, runs: list[Any]) -> str:
    from dawn_aoc.console import _task_and_output

    if not runs:
        return "(기록된 실행이 없다)"
    lines = []
    for i, r in enumerate(runs, 1):
        try:
            task, output = _task_and_output(r)
        except Exception:
            task, output = getattr(r, "task", ""), ""
        out = (output or "")[:MAX_OUTPUT_CHARS]
        lines.append(
            f"[{i}] trace={getattr(r, 'trace_id', '')[:12]} "
            f"model={getattr(r, 'model', '')} status={getattr(r, 'status', '')}\n"
            f"    지시: {task}\n"
            f"    산출: {out}"
        )
    return "\n".join(lines)


def _fmt_profile(prof) -> str:
    def names(xs):
        return ", ".join(getattr(x, "name", str(x)) for x in xs) or "(없음)"

    return (
        f"조직: {prof.org_name} ({prof.org_id})\n"
        f"미션: {prof.mission or '(없음)'}\n"
        f"페르소나: {names(prof.personas)}\n"
        f"정책: {names(prof.policies)}\n"
        f"자산: {names(prof.assets)}\n"
        f"자율화 수준: A{prof.autonomy_level}"
    )


def _fmt_hits(hits: list[dict[str, str]]) -> str:
    if not hits:
        return "(관련 항목 없음)"
    return "\n".join(
        f"  - ({h.get('why','')}) [{h['layer']}/{h['type']}] {h['name']} ({h['id']})"
        for h in hits
    )


# ── 공개 ────────────────────────────────────────────────────────────────
def converse(root: Path, agent_id: str, question: str, *, timeout: int = 300) -> dict[str, Any]:
    """에이전트에게 한 번 묻고 답을 받는다. 근거도 함께 돌려준다."""
    from dawn_agents import llm
    from dawn_core.eg.traverse import org_profile

    question = (question or "").strip()
    if not question:
        raise ConverseError("질문이 비어 있다")

    r = _routing(root, agent_id)
    soul = _soul(root, agent_id)
    runs = _recent_runs(root, agent_id)
    prof = org_profile(r["store"], r["eg_org"])
    hits = _eg_hits(r["store"], agent_id, question)

    system = (
        f"{soul}\n\n"
        "---\n"
        "너는 지금 관제 콘솔에서 사람의 질문을 받고 있다. 네가 한 일과 네 권한에 대해 답하라.\n"
        "\n"
        "규칙:\n"
        "- 근거로 주어진 것(게이트·실행기록·EG)만 가지고 답한다. 없으면 없다고 말한다.\n"
        "- 실행기록을 인용할 때는 trace 를 함께 밝힌다.\n"
        "- 이 대화에서 너는 도구를 쓸 수 없다. 무언가 실행해야 하는 요청이면,\n"
        "  하겠다고 답하지 말고 '이 창에서는 실행할 수 없다'고 말한 뒤 어디서 하면 되는지 알려라.\n"
        "- 짧게 답한다. 물은 것에 답하고, 묻지 않은 것을 늘어놓지 않는다.\n"
    )

    prompt = (
        f"## 내 권한 (게이트 정책: {r['gate_policy'] or '미지정'})\n"
        f"{_fmt_profile(prof)}\n\n"
        f"## 내 최근 실행 {len(runs)}건\n{_fmt_runs(root, runs)}\n\n"
        f"## 질문과 관련해 EG 에서 찾은 것\n{_fmt_hits(hits)}\n\n"
        f"## 질문\n{question}\n"
    )

    resolved = llm.resolve(r["model_id"], touches_l3=False)
    comp = llm.LLMClient(timeout=timeout).complete(
        resolved, system=system, prompt=prompt, max_tokens=1500
    )

    return {
        "agent_id": agent_id,
        "answer": comp.text,
        "model": comp.model,
        "provider": comp.provider,
        "model_policy": r["model_id"],
        "eg_org": r["eg_org"],
        # 근거를 화면에 같이 띄운다 — 답만 보여주면 EG 가 도는지 알 수 없다.
        "evidence": {
            "runs": len(runs),
            "eg_hits": hits,
            "soul": bool(soul),
        },
    }
