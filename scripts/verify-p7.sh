#!/usr/bin/env bash
# P7 자기검증 — docs/instructions/P7_work_orders.md 의 DoD.
#
#   ./scripts/verify-p7.sh          # 구조 검증 + E2E (모델 호출 없이)
#
# **자기 흔적을 지운다.** 검증이 만든 작업 지시·편성·할당은 끝나면 되돌린다 —
# 안 그러면 검증할 때마다 결재함과 레지스트리가 부푼다.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
BIZ="$PY -m dawn_biz.cli"

if [[ -t 1 ]]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; Z=$'\033[0m'
else G=""; R=""; Y=""; B=""; Z=""; fi

PASS=0; FAIL=0
check() { local n="$1"; shift
  printf '\n%s── %s%s\n' "$B" "$n" "$Z"
  if "$@" 2>&1 | sed 's/^/   /'; then printf '   %s✔ PASS%s\n' "$G" "$Z"; PASS=$((PASS+1))
  else printf '   %s✘ FAIL%s\n' "$R" "$Z"; FAIL=$((FAIL+1)); fi; }

echo "════════════════════════════════════════════════════════════════"
echo "  P7 자기검증 — 작업 지시 파이프라인"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "════════════════════════════════════════════════════════════════"

# ── DoD-1·2 접수와 결재 라인 ────────────────────────────────────────────

check "DoD-1·2  접수 규칙과 결재 라인이 매니페스트에서 파생한다" \
  "$PY" - <<'EOF'
from dawn_core import workintake
from dawn_core.paths import Paths
root = Paths().root
cs = workintake.choices(root, include_planned=True)
assert cs, "사업 선택지가 비었다"
print(f"사업 {len(cs)}개 · 등급은 매니페스트가 정한다")
for c in cs:
    print(f"  {c.id:<18} {c.tiers}")

# 등급이 결재 라인을 올린다
one = workintake.approval_chain(root, business="aoc-platform", infra_tier="container",
                                division="aoc", origin="internal")
two = workintake.approval_chain(root, business="aoc-platform", infra_tier="server",
                                division="aoc", origin="internal")
assert len(one) == 1 and len(two) == 2, f"등급이 결재를 안 올린다: {len(one)} → {len(two)}"
print(f"container → {[c['role'] for c in one]}")
print(f"server    → {[c['role'] for c in two]}  ← 외부 자원 점유")

# 사업 없는 내부 지원 업무 (Q12)
d, t = workintake.validate(root, business="", infra_tier="none", division="corp")
print(f"내부 지원  → {d} / {t}")
EOF

# ── DoD-3 인프라 ────────────────────────────────────────────────────────

check "DoD-3  할당은 결재 뒤에만 · 풀이 비면 대기 · 규칙 위반은 거부" \
  "$PY" - <<'EOF'
from dawn_core.infrapool import PoolError, allocate, ledger, summary
from dawn_core.paths import Paths
root = Paths().root

try:
    allocate(root, order_id=999001, tier="server", zone="dmz", approved=False)
    raise SystemExit("결재 없이 할당됐다")
except PoolError as e:
    print(f"미승인 거부: {e}")
assert not [a for a in ledger(root) if a.order_id == 999001], "거부됐는데 원장에 남았다"

try:
    allocate(root, order_id=999002, tier="container", zone="없는존", approved=True)
    raise SystemExit("없는 존에 할당됐다")
except PoolError as e:
    print(f"존 거부: {e}")

d = summary(root)
print(f"풀: 장비 {d['hosts_total']}대 · 컨테이너 {d['container_used']}/{d['container_max']} "
      f"· 도커 접근 {'가능' if d['docker'] else '불가(사람이 집행)'}")
EOF

# ── DoD-4 편성 ──────────────────────────────────────────────────────────

check "DoD-4  편성은 팀 경계를 못 넓히고 · 끝나면 흔적이 없다" \
  "$PY" - <<'EOF'
from dawn_core import Registry
from dawn_core.control_plane import compile_agent
from dawn_core.crew import CrewError, Member, disband, form, formed
from dawn_core.paths import Paths
root = Paths().root
OID = 999003

m = Member(role_key="verify", name="[검증] P7", team="corp-cs", persona="corporate",
           works=["corporate/crm-inquiry"],
           tools=["eg.search", "eg.record", "doc.search"], zone="dmz")
try:
    form(root, order_id=OID, members=[m], approved=False)
    raise SystemExit("결재 없이 편성됐다")
except CrewError as e:
    print(f"미승인 거부: {e}")

wide = Member(**{**m.__dict__, "tools": ["eg.search", "pay.execute"]})
try:
    form(root, order_id=OID, members=[wide], approved=True)
    raise SystemExit("팀 경계를 넘었다")
except CrewError as e:
    print(f"경계 초과 거부: {e}")

try:
    made = form(root, order_id=OID, members=[m], approved=True)
    print(f"편성: {made}")
    c = compile_agent(Registry.load(root), made[0])
    print(f"  계층 {[x.level for x in c.layers]} · 자율 {c.autonomy}")
    assert [x.level for x in c.layers] == ["L1", "L2", "L3", "L4"]
finally:
    gone = disband(root, order_id=OID)
    print(f"회수: {gone}")
assert formed(root, order_id=OID) == []
Registry.load(root).check_integrity()
print("무결성 통과 — 양방향 참조가 안 깨졌다")
EOF

# ── DoD-5 검수 ──────────────────────────────────────────────────────────

check "DoD-5  검수를 통과 못 한 산출물로 다음 단계가 시작되지 않는다" \
  "$PY" - <<'EOF'
from dawn_biz.execute import machine_review, review
from dawn_core.paths import Paths


class Run:
    agent_id = "verify"; trace_id = ""; task = ""; model_policy = ""
    def __init__(self, **kw):
        self.steps = []; self.tools_used = []; self.blocked = []
        self.complete = True; self.output = "결과"
        self.__dict__.update(kw)


ok = Run(tools_used=["eg.search"])
assert review(ok, with_judge=False).passed, "정상 산출물이 막혔다"
print("정상 → 통과")

for name, run in (("근거 없이 착수", Run(tools_used=[])),
                  ("게이트에 막힘", Run(tools_used=["eg.search"], blocked=["x"])),
                  ("산출물 없음", Run(tools_used=["eg.search"], output="")),
                  ("루프 미완결", Run(tools_used=["eg.search"], complete=False))):
    v = review(run, with_judge=False)
    assert not v.passed, f"{name} 인데 통과했다"
    print(f"{name} → 막힘: {v.reasons[0][:50]}")

# 같은 실행을 두 시점에서 봐도 판정이 같아야 한다
class Step:
    kind = "eg_search"
a = machine_review(Run(steps=[Step()], tools_used=[]))
b = machine_review(Run(tools_used=["eg.search"]))
assert a == b, f"같은 실행인데 판정이 다르다: {a} vs {b}"
print("WorkerRun ↔ 정규화 Run 판정 일치")
EOF

# ── DoD-6 상시 작업 ─────────────────────────────────────────────────────

check "DoD-6  상시 작업은 결재 전에 안 돌고 · 등록된 동작만 실행한다" \
  "$PY" - <<'EOF'
from dawn_biz import standing
from dawn_biz.store import BizStore
from dawn_core.paths import Paths
root = Paths().root

items = standing.load(root)
assert items, "상시 작업 선언이 비었다"
print(f"선언 {len(items)}건 — 모두 등록된 동작:")
for i in items:
    assert i.action in standing.ACTIONS
    print(f"  {i.id:<14} {i.every:>5}분  {i.action}")

s = BizStore(root, tenant=0)
ok = standing.approved_orders(root, s)
last = standing.state(root)
print(f"결재 완료 {len(ok)}/{len(items)} · 실행 이력 {len(last)}건")
dead = [i.id for i in items if (last.get(i.id) or {}).get("ok") is False]
assert not dead, f"최근 회차가 실패로 남아 있다: {dead}"
EOF

# ── DoD-7 기록·정산 ─────────────────────────────────────────────────────

check "DoD-7  원가는 청구액이 아니고 · 미정을 0 으로 만들지 않는다" \
  "$PY" - <<'EOF'
from dawn_biz.records import Usage, rates, settle
from dawn_core.paths import Paths
root = Paths().root

rc = rates(root)
assert "청구" not in str(rc), "원가표에 청구 단가가 섞였다"
print(f"환율 {rc['usd_krw']} · 단가 있는 모델 {len(rc['model'])}종")

cloud = Usage(order_id=0, by_model={"claude-opus-5":
              {"in": 1_000_000, "out": 200_000, "runs": 1, "local": 0}})
s = settle(root, cloud)
assert s.complete and s.model_cost == round(10.0 * rc["usd_krw"])
print(f"클라우드 100만/20만 토큰 → {s.model_cost:,} KRW (완결)")

local = Usage(order_id=0, by_model={"gpt-oss:120b":
              {"in": 5_000_000, "out": 1_000_000, "runs": 1, "local": 1}})
s2 = settle(root, local)
assert s2.model_cost == 0 and not s2.complete, "로컬을 공짜 0 으로 처리했다"
print(f"로컬 → 미정 {len(s2.unpriced)}건: {s2.unpriced[0][:60]}")
print("  0(판단함)과 미정(안 정함)이 구별된다")
EOF

# ── E2E ─────────────────────────────────────────────────────────────────

check "E2E  접수 → 결재 → 인프라 → 편성 → 착수차단 → 마감·기록" \
  "$PY" - <<'EOF'
from datetime import datetime, timezone

from dawn_biz.execute import can_start
from dawn_biz.provision import provision
from dawn_biz.records import close, settle, usage
from dawn_biz.store import BizStore
from dawn_core import workintake
from dawn_core.crew import Member, disband, form
from dawn_core.paths import Paths

root = Paths().root
s = BizStore(root, tenant=0)
wid = 0
try:
    # ① 접수 — 규칙이 담당 본부와 등급을 확정한다
    div, tier = workintake.validate(root, business="ax-consulting",
                                    infra_tier="none")
    wid = s.add_work_order(title="[P7검증] 대학 AX 진단", body="전공별 접목 방향",
                           origin="external", requester="검증",
                           business="ax-consulting", division=div, infra_tier=tier)
    s.set_work_order_status(wid, "pending_approval")
    print(f"① 접수 #{wid} → {div} / {tier}")

    # ② 결재 — 외부 고객이라 대표이사까지
    chain = workintake.approval_chain(root, business="ax-consulting", infra_tier=tier,
                                      division=div, origin="external")
    print(f"② 결재 라인 {[c['role'] for c in chain]}")
    assert not can_start(s, wid)[0], "결재 전인데 착수 가능하다"
    decided = []
    while (nxt := workintake.next_approver(chain, decided)) is not None:
        d = workintake.decide(chain, decided, actor=nxt["portal_user"], approve=True,
                              at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        decided.append(d)
        done = workintake.next_approver(chain, decided) is None
        s.append_work_order_approval(wid, d,
                                     status="approved" if done else "pending_approval")
    print(f"   승인 완료 → {s.work_order(wid)['status']}")

    # ③ 인프라 — none 이라 잡을 것이 없다
    a = provision(s, root, wid)
    print(f"③ 인프라 {a.state} — {a.reason}")

    # ④ 편성 — 대학사업부(Q11 에서 L2 를 만든 팀)
    ok, why = can_start(s, wid)
    assert not ok and "편성" in why, f"편성 전인데 착수 가능: {why}"
    made = form(root, order_id=wid, members=[Member(
        role_key="diag", name="[P7검증] 진단", team="ax-university",
        persona="consulting", works=["consulting/ax-diagnosis"],
        tools=["eg.search", "eg.record", "doc.search", "net.web_search"],
        zone="dmz")], approved=True)
    print(f"④ 편성 {made}")

    # ⑤ 착수 가능 — 세 관문을 다 지났다
    ok, why = can_start(s, wid)
    assert ok, f"세 관문을 지났는데 착수 불가: {why}"
    print("⑤ 착수 가능 — 결재·인프라·편성 세 관문 통과")

    # ⑥ 마감 — 일지·보고서·원가
    u = usage(root, wid)
    st = settle(root, u)
    out = close(s, root, wid)
    print(f"⑥ 일지 #{out['worklog_id']} · 보고서 #{out['report_id']} · "
          f"원가 {st.total:,} KRW{'' if st.complete else ' (하한)'}")
    print(f"   편성 회수 {len(out['disbanded'])}명 · 상태 {s.work_order(wid)['status']}")
    doc = s.document(out["report_id"])
    assert "청구액이 아니다" in doc["body"]
    assert s.work_order(wid)["status"] == "done"
finally:
    if wid:
        disband(root, order_id=wid)
        for d in s.documents(limit=200):
            if f"wo{wid}" in (d["tags"] or ""):
                s.db.execute("DELETE FROM document WHERE id=?", (d["id"],))
        s.db.execute("DELETE FROM work_order WHERE id=?", (wid,))
        s.db.commit()
        print(f"   (검증 흔적 정리 — #{wid})")
EOF

echo
echo "════════════════════════════════════════════════════════════════"
printf '  %s통과 %d%s  ·  %s실패 %d%s\n' "$G" "$PASS" "$Z" "$R" "$FAIL" "$Z"
echo "════════════════════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]]
