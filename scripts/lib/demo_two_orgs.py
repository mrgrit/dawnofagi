"""P2 DoD-7 — 2개 조직 워커를 실제로 돌린다 (사내 GPU).

경영관리(corporate/로컬/L3) + CCC(secops/로컬/L3) 각 1회.
게이트·라우팅·HITL·텔레메트리가 실물에서 도는지 본다.
"""

import os

os.environ.setdefault("DAWN_AUTO_APPROVE", "1")

from dawn_agents import Worker

DEMOS = [
    (
        "corp-admin-clerk-01",
        "경비 신청 EXP-2026-0801-001 을 corporate/expense-processing 절차대로 처리하라. "
        "금액 임계(10만원) 초과 여부를 판정하고 근거를 밝혀라.",
        [("fin.expense_read", {"request_id": "EXP-2026-0801-001"})],
    ),
    (
        "ccc-soc-triage-01",
        "알럿 트리아지: 10.20.40.81(juiceshop) 대상 비정상 인증 시도 47회. "
        "security/alert-triage 절차대로 close/escalate/respond_now 중 하나로 판정하라.",
        [("sec.trace_query", {"limit": 3})],
    ),
]


def main() -> int:
    rc = 0
    for agent_id, task, skills in DEMOS:
        w = Worker(agent_id)
        run = w.run(task, touches_l3=True, extra_skills=skills, purpose="demo")
        print(
            f"  {agent_id:<22} complete={run.complete}  "
            f"{run.model_policy}→{run.provider}/{run.model}  "
            f"tools={run.tool_calls} hitl={len(run.hitl_requests)} "
            f"tokens={run.tokens_in}/{run.tokens_out}"
        )
        if run.error:
            print(f"    error: {run.error}")
        # 스팬 트리 — 루프가 실제로 돌았는지
        names = [s.name for s in w.tracer.spans]
        print(f"    스팬: {' → '.join(names)}")
        if not run.complete:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
