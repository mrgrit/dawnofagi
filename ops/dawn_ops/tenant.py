"""멀티테넌트 준비 점검 — 자사(#0)가 정말 "테넌트 하나"인가.

헌장 원리 #3: "자사 = 테넌트 #0 = 레퍼런스 구현."
그 말이 사실이려면 고객 테넌트를 **하나 더 붙일 수 있어야** 한다.

여기서 검사하는 것:

    격리 구조   조회 함수가 tenant 를 인자로 받지 않는가 (받으면 언젠가 틀린 값이 온다)
    데이터 경계 다른 테넌트 행이 조회에 섞이지 않는가
    정책        pol:no-cross-tenant 가 실제로 block 을 내는가
    EG 네임스페이스  고객 조직·자산이 `org:` `asset:` 충돌 없이 들어갈 수 있는가
    계정        사람 계정도 테넌트로 갈리는가

**실제로 테넌트 #7 을 만들어 넣어 보고 지운다.** 문서로만 "격리됩니다"라고
쓰는 것과 다르다.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

CUSTOMER_TENANT = 7          # 점검용. 실제 온보딩은 1부터 순차 배정한다.


@dataclass
class Check:
    name: str
    ok: bool = False
    detail: str = ""
    fix: str = ""

    def line(self) -> str:
        return f"  {'✔' if self.ok else '✘'} {self.name:<34} {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TenantReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.to_dict() for c in self.checks]}


def run(root, *, tmp_dir=None) -> TenantReport:
    from pathlib import Path

    from dawn_biz.store import BizStore
    from dawn_groupware.auth import UserStore
    from dawn_groupware.store import Store as PortalStore

    rep = TenantReport()
    root = Path(root)
    sandbox = Path(tmp_dir) if tmp_dir else root

    # 1. 조회 함수가 tenant 를 인자로 받지 않는다
    c = Check("조회 API 에 tenant 인자 없음")
    leaky = []
    for cls in (BizStore, PortalStore):
        for name, fn in vars(cls).items():
            if name.startswith("_") or not callable(fn):
                continue
            if "tenant" in inspect.signature(fn).parameters and name != "__init__":
                leaky.append(f"{cls.__name__}.{name}")
    c.ok = not leaky
    c.detail = "커넥션에 묶여 있다" if c.ok else f"인자로 받는다: {', '.join(leaky)}"
    c.fix = "Store(root, tenant=N) 로만 접근하게 하라"
    rep.checks.append(c)

    # 2. 실제로 고객 테넌트를 하나 만들어 격리를 확인한다
    c = Check(f"데이터 격리 (테넌트 #{CUSTOMER_TENANT} 실증)")
    a = BizStore(sandbox, tenant=0)
    b = BizStore(sandbox, tenant=CUSTOMER_TENANT)
    probe = "온보딩 점검 고객 (임시)"
    b.add_customer(name=probe, segment="university",
                   note="멀티테넌트 점검용 — 점검 후 지운다")
    names_self = {r["name"] for r in a.customers(limit=500)}
    names_cust = {r["name"] for r in b.customers(limit=500)}
    c.ok = probe in names_cust and probe not in names_self
    c.detail = (f"자사 {len(names_self)}건 / 고객 {len(names_cust)}건 — 섞이지 않음"
                if c.ok else "테넌트 경계를 넘어 보인다")
    rep.checks.append(c)

    # 3. 크로스테넌트 정책이 실제로 block 을 낸다
    c = Check("pol:no-cross-tenant 가 block 을 낸다")
    from dawn_agents.policy import Facts, evaluate_rule

    fired, unknown, why = evaluate_rule(
        "task.tenant != asset.tenant => block",
        Facts(task_tenant="0", asset_tenant=str(CUSTOMER_TENANT)))
    same, _, _ = evaluate_rule(
        "task.tenant != asset.tenant => block", Facts(task_tenant="0", asset_tenant="0"))
    c.ok = fired and not unknown and not same
    c.detail = f"다른 테넌트 → 발동({why[:30]}) · 같은 테넌트 → 미발동"
    rep.checks.append(c)

    # 4. EG 네임스페이스에 고객 조직·자산이 충돌 없이 들어가나
    c = Check("EG 네임스페이스 충돌 여지")
    from dawn_core import Registry
    from dawn_core.eg.cli import db_path
    from dawn_core.eg.store import EGStore

    eg = EGStore(db_path(Registry.load(root).paths))
    orgs = {n.id for n in eg.nodes(type="OrgUnit")}
    assets = {n.id for n in eg.nodes(type="Asset")}
    # 고객 테넌트는 `org:t7-*` `asset:t7-*` 처럼 접두사를 쓴다 — 충돌하면 안 된다
    prefix = f"t{CUSTOMER_TENANT}-"
    collide = [x for x in orgs | assets if x.split(":", 1)[-1].startswith(prefix)]
    c.ok = not collide
    c.detail = (f"조직 {len(orgs)} · 자산 {len(assets)} · "
                f"`{prefix}` 접두사 비어 있음" if c.ok
                else f"이미 쓰이는 접두사: {collide[:3]}")
    c.fix = "고객 노드는 org:t<N>-* / asset:t<N>-* 로 네임스페이스를 나눈다"
    rep.checks.append(c)

    # 5. 사람 계정도 테넌트로 갈린다
    c = Check("사람 계정 테넌트 격리")
    users = UserStore(root)
    self_users = {u.username for u in users.list(tenant=0)}
    cust_users = {u.username for u in users.list(tenant=CUSTOMER_TENANT)}
    c.ok = not (self_users & cust_users) and "tenant" in inspect.signature(
        UserStore.list).parameters
    c.detail = f"자사 {len(self_users)}명 / 고객 {len(cust_users)}명"
    rep.checks.append(c)

    # 6. 업무 데이터가 자산을 선언하지 않으면 고객 테넌트에서 관제가 안 된다
    c = Check("모든 업무 종류가 EG 자산을 선언")
    from dawn_biz.store import KIND_ASSET

    missing = [k for k, v in KIND_ASSET.items() if not v]
    c.ok = not missing
    c.detail = f"{len(KIND_ASSET)}종 전부 선언" if c.ok else f"미선언: {missing}"
    rep.checks.append(c)

    # 정리 — 점검 흔적을 남기지 않는다
    b.db.execute("DELETE FROM customer WHERE tenant=? AND name=?",
                 (CUSTOMER_TENANT, probe))
    b.db.commit()
    return rep


ONBOARDING_STEPS = [
    ("1. 테넌트 번호 배정", "자사=0. 고객은 1부터 순차. 번호는 재사용하지 않는다."),
    ("2. EG 네임스페이스 생성",
     "`org:t<N>-*` `asset:t<N>-*` 로 고객 조직·자산을 넣는다. "
     "기존 노드와 절대 섞지 않는다."),
    ("3. 고객 규정 → EG",
     "고객의 보안등급·존·정책을 같은 스키마로 채운다 (= P1 의 고객 버전). "
     "`eg/validate.py` 오류 0 이어야 주입한다."),
    ("4. 게이트 정의",
     "고객 조직별 `gate.yaml`. 전사 게이트를 **좁히기만** 한다 — "
     "단조 축소 규칙은 고객에게도 같다."),
    ("5. 에이전트 배치",
     "고객 조직에 에이전트 매니페스트 + SOUL.md. 자율화는 A0/A1 에서 시작."),
    ("6. 관제 편입",
     "에이전트가 스팬을 뱉으면 자동으로 AOC 대상이 된다. "
     "픽셀 오피스에 고객 층이 생기는지 확인."),
    ("7. 사람 계정",
     "`dawn-web useradd --tenant <N>`. 승인 권한은 고객 조직 트리 안에서만."),
    ("8. 격리 검증",
     "`dawn-ops tenant` 로 점검. 크로스테넌트 조회가 0 인지 확인한 뒤 개시."),
]


__all__ = ["CUSTOMER_TENANT", "ONBOARDING_STEPS", "Check", "TenantReport", "run"]
