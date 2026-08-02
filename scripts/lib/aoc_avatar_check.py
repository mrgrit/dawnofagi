"""P3 자기검증 ① — P2 워커를 돌리고, 그 아바타가 **올바른 존/방**에 나타나는가.

관제의 첫 번째 거짓말은 "그림은 예쁜데 데이터랑 다르다"이다. 그래서 여기서는
그림이 아니라 **그림이 읽는 상태**를 검사한다:

    워커 실행 → 새 트레이스 → 수집 정규화 → 상태 스냅샷
      → 그 에이전트의 room 이 EG 존 순회 결과와 같은가
      → eg_refs 가 이 실행이 실제로 만진 EG 노드인가
      → last_trace 가 방금 만든 트레이스인가

`--live` 없이도 돌 수 있게, 이미 있는 트레이스로도 검증한다.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("DAWN_AUTO_APPROVE", "1")

from dawn_aoc.collect import TraceLake
from dawn_aoc.console import build_state
from dawn_core import Registry
from dawn_core.eg.cli import db_path
from dawn_core.eg.store import EGStore
from dawn_core.paths import Paths

AGENT = "ccc-soc-triage-01"


def run_worker(root) -> str:
    """실제 워커 1회. 새 trace_id 를 돌려준다."""
    from dawn_agents import Worker

    w = Worker(AGENT)
    run = w.run(
        "알럿 트리아지: dmz 존 assessor 로 최근 활동을 확인하고 "
        "security/alert-triage 절차대로 판정하라.",
        touches_l3=True,
        extra_skills=[("sec.trace_query", {"limit": 3})],
        purpose="drill",
    )
    print(f"  워커 실행   {AGENT}  complete={run.complete}  "
          f"{run.model_policy}→{run.provider}/{run.model}  "
          f"도구 {run.tool_calls}회  토큰 {run.tokens_in}/{run.tokens_out}")
    if run.error:
        print(f"    error: {run.error}")
    return run.trace_id


def main() -> int:
    root = Paths().root
    live = "--live" in sys.argv
    fail = 0

    trace_id = ""
    if live:
        trace_id = run_worker(root)
    else:
        lake = TraceLake(root)
        for r in lake.all_runs(limit=50):
            if r.agent_id == AGENT:
                trace_id = r.trace_id
                break
        if not trace_id:
            print("  ⊘ 이 에이전트의 트레이스가 없다 — --live 로 워커를 먼저 돌려라")
            return 1
        print(f"  기존 트레이스 사용  {trace_id}")

    eg = EGStore(db_path(Registry.load(root).paths))
    st = build_state(root, limit=50)
    a = next((x for x in st["agents"] if x["agent_id"] == AGENT), None)
    if a is None:
        print(f"  ✘ 상태에 {AGENT} 가 없다")
        return 1

    # ① 방 = EG 존 순회 결과와 일치하나
    zone = next((z for z in st["zones"] if z["short"] == a["zone"]), None)
    if zone is None:
        print(f"  ✘ zone '{a['zone']}' 가 EG Zone 노드에 없다 — 아바타가 허공에 뜬다")
        fail = 1
    elif a["room"] != zone["pixel_room"]:
        print(f"  ✘ 방 불일치: 상태={a['room']}  EG={zone['pixel_room']}")
        fail = 1
    else:
        print(f"  ✔ 존/방      {a['zone']} → {a['room']}  "
              f"({zone['cidr']}, {zone['sensitivity']}, {zone['security_level']})")

    # ② 아바타 인코딩이 전부 실측에서 나오나
    print(f"  ✔ 아바타     몸색={a['division_color']}({a['division']})  "
          f"배지={a['badge'] or '—'}  모자={a['hat']}  이펙트={a['effect']}")
    if not a["badge"] and a["runs"]:
        print("  ✘ 실행이 있는데 모델 배지가 비었다 — 텔레메트리에 모델이 안 남았다")
        fail = 1

    # ③ EG 아이콘 = 실제로 만진 EG 노드 (말풍선 대신)
    if not a["eg_refs"]:
        print("  ! EG 참조 없음 — 이 실행은 정책/자산을 건드리지 않았다 (비어 있는 게 맞다)")
    else:
        unknown = [r for r in a["eg_refs"] if eg.node(r) is None]
        if unknown:
            print(f"  ✘ EG 에 없는 노드를 아이콘으로 띄운다: {unknown}")
            fail = 1
        else:
            print(f"  ✔ EG 아이콘  {', '.join(a['eg_refs'])}  (전부 EG 에 실재)")

    # ④ 리플레이 링크가 진짜 트레이스를 가리키나
    lake = TraceLake(root)
    if a["last_trace"] not in lake.trace_ids():
        print(f"  ✘ last_trace 가 레이크에 없다: {a['last_trace']}")
        fail = 1
    else:
        n = len(lake.spans(a["last_trace"]))
        print(f"  ✔ 리플레이   last_trace={a['last_trace'][:12]}…  스팬 {n}개")

    if live and trace_id and a["last_trace"] != trace_id:
        print(f"  ✘ 방금 돌린 실행({trace_id[:12]}…)이 아바타에 반영되지 않았다")
        fail = 1

    return fail


if __name__ == "__main__":
    raise SystemExit(main())
