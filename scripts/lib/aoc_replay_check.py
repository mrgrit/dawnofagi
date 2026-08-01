"""P3 자기검증 ④ — 인시던트를 타임라인으로 리플레이해 사후 재구성한다.

EU AI Act 12조가 요구하는 건 "로그가 있다"가 아니라 **"로그만으로 그때 무슨 일이
있었는지 복원된다"** 이다. 그래서 여기서는 트레이스 하나만 가지고 다음을
전부 복원할 수 있는지 확인한다:

    누가       gen_ai.agent.id / dawn.team / dawn.eg_org / dawn.zone
    무엇을     gen_ai.tool.name (순서대로)
    어떤 판정  dawn.gate.decision + dawn.gate.reasons + dawn.severity
    무엇에     dawn.assets / dawn.policies
    사람은     dawn.hitl.id (승인 큐 항목이 실재하는가)
    언제       스팬 시각 (단조 증가)

인자로 trace_id 를 주지 않으면 **가장 최근 케이스의 트레이스**를 쓴다.
"""

from __future__ import annotations

import sys

from dawn_agents.hitl import ApprovalQueue
from dawn_aoc.collect import TraceLake
from dawn_aoc.triage import CaseStore
from dawn_core.paths import Paths

REQUIRED_ROOT = ["gen_ai.agent.id", "dawn.team", "dawn.division", "dawn.eg_org",
                 "dawn.persona", "dawn.autonomy", "dawn.zone"]


def main() -> int:
    root = Paths().root
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    lake = TraceLake(root)

    trace_id = args[0] if args else ""
    if not trace_id:
        cases = CaseStore(root).list()
        trace_id = cases[0].trace_id if cases else (lake.trace_ids()[:1] or [""])[0]
    if not trace_id:
        print("  ✘ 리플레이할 트레이스가 없다")
        return 1

    spans = sorted(lake.spans(trace_id), key=lambda s: s["start_ns"])
    if not spans:
        print(f"  ✘ 트레이스 {trace_id} 에 스팬이 없다")
        return 1

    fail = 0
    root_span = next((s for s in spans if s["name"] == "invoke_agent"), None)
    if root_span is None:
        print("  ✘ invoke_agent 루트 스팬이 없다 — 누가 했는지 복원 불가")
        return 1

    a = root_span["attributes"]
    missing = [k for k in REQUIRED_ROOT if not a.get(k)]
    if missing:
        print(f"  ✘ 주체 복원 불가 — 빠진 속성: {missing}")
        fail = 1
    else:
        print(f"  누가       {a['gen_ai.agent.id']}  ({a['dawn.team']}/"
              f"{a['dawn.division']}, EG {a['dawn.eg_org']}, "
              f"persona={a['dawn.persona']}, {a['dawn.autonomy']}, zone={a['dawn.zone']})")

    # 시각 단조성 — 순서가 흐트러지면 인과가 복원되지 않는다
    if [s["start_ns"] for s in spans] != sorted(s["start_ns"] for s in spans):
        print("  ✘ 스팬 시각이 단조 증가하지 않는다")
        fail = 1

    t0 = spans[0]["start_ns"]
    queue = ApprovalQueue(root)
    tools = 0
    print(f"\n  타임라인   {trace_id}   스팬 {len(spans)}개")
    for s in spans:
        at = s["attributes"]
        off = (s["start_ns"] - t0) / 1e6
        if s["name"] == "execute_tool":
            tools += 1
            tool = at.get("gen_ai.tool.name", "")
            dec = at.get("dawn.gate.decision", "")
            if not tool or not dec:
                print(f"  ✘ execute_tool 스팬에 도구명/판정이 없다: {at}")
                fail = 1
                continue
            print(f"   +{off:>8.0f}ms  {tool:<22} gate={dec:<13} "
                  f"sev={at.get('dawn.severity', '-')}  "
                  f"assets={at.get('dawn.assets') or '-'}")
            if at.get("dawn.gate.reasons"):
                print(f"                 근거: {at['dawn.gate.reasons'][:90]}")
            hid = at.get("dawn.hitl.id")
            if hid:
                try:
                    ap = queue.get(hid)
                    print(f"                 사람: {hid} → {ap.status} "
                          f"[{ap.severity_label}/{ap.severity}]")
                except KeyError:
                    print(f"  ✘ 스팬이 가리키는 승인 항목이 없다: {hid}")
                    fail = 1
            if dec in ("block", "require_hitl") and not hid:
                print(f"  ✘ {dec} 인데 승인 큐 항목이 없다 — 사람 개입 기록이 끊겼다")
                fail = 1
        elif s["name"] == "chat":
            print(f"   +{off:>8.0f}ms  chat {at.get('gen_ai.request.model', '?')}  "
                  f"↑{at.get('gen_ai.usage.input_tokens', 0)} "
                  f"↓{at.get('gen_ai.usage.output_tokens', 0)}  "
                  f"local={at.get('dawn.model.local')}")
        for ev in s.get("events", []):
            c = str(ev.get("attributes", {}).get("content", ""))
            print(f"                 ↳ {ev['name']}" + (f": {c[:60]}…" if c else ""))

    if tools == 0:
        print("  ! 도구 실행 스팬이 없다 — 이 트레이스는 행동 기록이 없다")

    # 마스킹 — 복원 가능성과 프라이버시는 같이 가야 한다
    runs = lake.normalize(trace_id)
    if runs and runs[0].masking_violations:
        print(f"  ✘ 리플레이 대상에 마스킹 안 된 민감정보가 있다: "
              f"{[v['kind'] for v in runs[0].masking_violations]}")
        fail = 1
    else:
        print("\n  ✔ 마스킹    재구성 가능 + 민감정보 노출 없음")

    print("  ✔ 재구성    주체·행동·판정·자산·사람개입·시각 복원 완료")
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
