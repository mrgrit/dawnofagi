"""작업 지시 접수 규칙 — 홈페이지·그룹웨어·CLI 가 함께 쓴다.

**여기는 레지스트리(`org/`)만 읽는다. 업무 DB 를 건드리지 않는다.**
그래서 `biz` 가 아니라 `dawn_core` 에 있다 — 공개 홈페이지(zone:ext)가 업무
DB(dmz/int)로 가는 경로를 갖지 않는다는 P4 격리 불변식 때문이다
(`biz/tests/test_biz.py::test_public_site_does_not_import_business_store`).
규칙과 저장이 갈라져 있어야 그 경계가 유지된다.

접수 경로는 여럿이지만 **규칙은 하나다.** 화면마다 규칙을 두면 갈라진다.

  · 인프라 등급 선택지는 **사업 매니페스트**가 정한다 (`org/businesses/*.yaml` 의 `infra`)
  · 담당 본부는 사업의 `owning_divisions` 에서 나온다
  · 결재 라인은 등급·민감도·출처에서 파생한다 (P7 DoD-2)

저장은 경로마다 다르다:

  · 홈페이지(외부) → `var/website/work_requests.jsonl` → `dawn-biz intake` 가 승격
  · 그룹웨어(내부) → `BizStore.add_work_order` 직접
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INFRA_TIERS = ["none", "container", "vm", "server"]

# 등급별 설명 — 폼에 그대로 뜬다. 요청자가 고르는 것이므로 사람 말로 적는다.
TIER_LABEL = {
    "none": ("환경 불필요", "진단·검토·문서 작업. 기존 환경에서 수행한다"),
    "container": ("컨테이너", "데모·PoC·단기 실험. 사내 인프라에 바로 올라간다"),
    "vm": ("가상머신", "프로덕트 구축·이관. 외부 시스템에서 할당받는다"),
    "server": ("전용 서버", "상시 운영·대규모. 외부 시스템에서 할당받는다"),
}

# vm 이상은 외부 시스템 자원을 점유한다 → 대표이사 결재가 붙는다 (P7 DoD-2).
TIER_NEEDS_CEO = {"vm", "server"}


@dataclass
class BusinessChoice:
    """접수 폼이 보여줄 사업 하나."""

    id: str
    name: str
    status: str
    divisions: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    default_tier: str = "none"
    data_sensitivity: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__,
                "tier_labels": [(t, *TIER_LABEL.get(t, (t, ""))) for t in self.tiers]}


def choices(root: Path, *, include_planned: bool = False) -> list[BusinessChoice]:
    """접수 폼에 띄울 사업 목록. **매니페스트가 권위다** — 하드코딩하지 않는다."""
    from . import Registry

    reg = Registry.load(root)
    out: list[BusinessChoice] = []
    for bid, biz in sorted(reg.businesses.items()):
        d = biz.data
        if d.get("status") != "active" and not include_planned:
            continue
        infra = d.get("infra") or {}
        allowed = [t for t in infra.get("allowed", ["none"]) if t in INFRA_TIERS]
        default = infra.get("default", "none")
        out.append(BusinessChoice(
            id=bid,
            name=d.get("name", bid),
            status=d.get("status", ""),
            divisions=list(d.get("owning_divisions", [])),
            tiers=allowed or ["none"],
            default_tier=default if default in allowed else (allowed[0] if allowed else "none"),
            data_sensitivity=d.get("data_sensitivity", ""),
            notes=infra.get("notes", ""),
        ))
    return out


def division_choices(root: Path) -> list[tuple[str, str]]:
    """사업 없이 접수할 때 고를 본부 목록 — `[(id, 이름)]`.

    사업은 **수익 단위**, 본부는 **일하는 단위**다. 내부 지원 업무는 수익 단위가
    없으니 일하는 단위를 직접 고른다 (QUESTIONS Q12).
    """
    from . import Registry

    return [(did, d.data.get("name", did))
            for did, d in sorted(Registry.load(root).divisions.items())]


def validate(root: Path, *, business: str, infra_tier: str,
             division: str = "") -> tuple[str, str]:
    """(담당 본부, 인프라 등급) 을 확정한다. 규칙을 어기면 예외.

    **사업이 비어 있으면 내부 지원 업무다.** 경리 처리·문의 응대·시스템 운영은
    돈을 버는 사업이 아니라 회사가 굴러가려고 하는 일이다. 사업은 **수익 단위**이고
    본부는 **일하는 단위**라 원래 1:1 이 아닌데, 사업을 필수로 두면 경영관리부처럼
    어느 사업의 소관도 아닌 본부는 작업 지시를 아예 못 만든다 (QUESTIONS Q12).

    Returns:
        (division, infra_tier)

    Raises:
        ValueError: 없는 사업 · 그 사업이 허용하지 않은 등급 · 그 사업 소관이 아닌 본부
            · 내부 업무인데 본부를 안 골랐거나 없는 본부
    """
    from . import Registry

    if not business:
        if infra_tier not in INFRA_TIERS:
            raise ValueError(f"알 수 없는 인프라 등급: {infra_tier}")
        if infra_tier in TIER_NEEDS_CEO:
            # 사업 없는 내부 업무가 외부 시스템 자원을 점유할 이유가 없다.
            # 필요하면 사업을 붙여서 올려라 — 그래야 비용이 어디로 가는지 남는다.
            raise ValueError(
                f"내부 지원 업무는 '{infra_tier}' 등급을 쓸 수 없다 "
                "(사업을 지정하라 — 외부 자원 점유는 비용 귀속처가 있어야 한다)")
        if not division:
            raise ValueError("내부 지원 업무는 담당 본부를 골라야 한다")
        if division not in Registry.load(root).divisions:
            raise ValueError(f"없는 본부: {division}")
        return division, infra_tier

    by_id = {c.id: c for c in choices(root, include_planned=True)}
    c = by_id.get(business)
    if c is None:
        raise ValueError(f"알 수 없는 사업: {business} (org/businesses/ 확인)")
    if infra_tier not in c.tiers:
        raise ValueError(
            f"{c.name} 은 '{infra_tier}' 등급을 허용하지 않는다 "
            f"(허용: {', '.join(c.tiers)})"
        )
    if division and division not in c.divisions:
        raise ValueError(
            f"{c.name} 의 소관 본부가 아니다: {division} (소관: {', '.join(c.divisions)})"
        )
    return (division or (c.divisions[0] if c.divisions else "")), infra_tier


def approval_chain(root: Path, *, business: str, infra_tier: str,
                   division: str, origin: str = "internal") -> list[dict[str, str]]:
    """이 작업 지시의 결재 라인. **규칙에서 파생한다** — 사람이 매번 정하지 않는다.

    기본은 담당 본부장 1단계. 아래 중 하나라도 걸리면 대표이사가 붙는다:

      · `vm` 이상 인프라   외부 시스템 자원 점유
      · L3 데이터          인사·재무·개인정보
      · 외부 고객 요청     계약·대외 약속이 생긴다

    Returns:
        [{role, portal_user, reason}] — 순서가 곧 결재 순서다.
    """
    import yaml

    from . import Registry

    reg = Registry.load(root)
    chain: list[dict[str, str]] = []

    div = reg.divisions.get(division)
    lead = (div.data.get("lead") if div else None) or {}
    if lead.get("portal_user"):
        chain.append({"role": lead.get("role", "본부장"),
                      "portal_user": lead["portal_user"],
                      "reason": "담당 본부 승인"})

    reasons = []
    if infra_tier in TIER_NEEDS_CEO:
        reasons.append(f"{infra_tier} 자원 점유")
    biz = reg.businesses.get(business) if business else None
    if biz and biz.data.get("data_sensitivity") == "L3":
        reasons.append("L3 데이터")
    if origin == "external":
        reasons.append("외부 고객 요청")

    if reasons:
        f = Path(root) / "org" / "company.yaml"
        ceo = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("ceo", {}) \
            if f.is_file() else {}
        if ceo.get("portal_user"):
            chain.append({"role": ceo.get("role", "대표이사"),
                          "portal_user": ceo["portal_user"],
                          "reason": " · ".join(reasons)})
    return chain


def next_approver(chain: list[dict[str, str]],
                  decided: list[dict[str, Any]]) -> dict[str, str] | None:
    """지금 결재할 차례인 사람. 없으면 None (완료됐거나 반려됐다).

    **순차다.** 1단계가 승인해야 2단계가 열린다 — 동시에 올리면 아래 단계가
    위 단계를 우회할 수 있고, 그 순간 결재 라인은 형식만 남는다.
    """
    if any(d.get("decision") == "rejected" for d in decided):
        return None
    approved = [d for d in decided if d.get("decision") == "approved"]
    return chain[len(approved)] if len(approved) < len(chain) else None


def decide(chain: list[dict[str, str]], decided: list[dict[str, Any]], *,
           actor: str, approve: bool, note: str = "", at: str = "") -> dict[str, Any]:
    """결재 1건. 규칙을 어기면 예외 — 화면이 아니라 여기가 경계다.

    Raises:
        PermissionError: 차례가 아닌 사람 · 라인에 없는 사람
        ValueError: 이미 끝난 결재 (재판정 불가 — 감사 추적)
    """
    nxt = next_approver(chain, decided)
    if nxt is None:
        raise ValueError("이미 끝난 결재다 — 재판정할 수 없다 (감사 추적)")
    if actor != nxt["portal_user"]:
        in_line = any(c["portal_user"] == actor for c in chain)
        raise PermissionError(
            f"지금 차례는 {nxt['role']}({nxt['portal_user']}) 다"
            + ("" if in_line else " — 이 작업의 결재 라인에 없다")
        )
    return {"step": len(decided) + 1, "role": nxt["role"], "actor": actor,
            "decision": "approved" if approve else "rejected",
            "reason": nxt.get("reason", ""), "note": note[:500], "at": at}


__all__ = [
    "INFRA_TIERS",
    "TIER_LABEL",
    "TIER_NEEDS_CEO",
    "BusinessChoice",
    "approval_chain",
    "choices",
    "decide",
    "next_approver",
    "validate",
]
