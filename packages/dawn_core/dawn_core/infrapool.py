"""인프라 풀 — 작업 지시가 자원을 **할당받는다** (P7 DoD-3).

**프로비저닝은 "만드는 것"이 아니라 "준비된 것을 꺼내 쓰는 것"이다.**
하드웨어·OS 설치는 사람이 미리 해 두고 `infra/pool.yaml` 에 등록한다. 그래서:

  · 에이전트가 **클라우드 크레덴셜을 쥘 필요가 없다** — 과금 발생 행동이 없다
  · 실패 모드가 "생성 실패"가 아니라 **"가용 자원 없음"** — 훨씬 다루기 쉽다
  · 회수 = **반납**이지 삭제가 아니다

## 재고와 원장을 갈라 놓는다

`infra/pool.yaml` 은 **사람이 쓰는 재고 목록**이다. 장비를 추가하거나 고장 나서
`maintenance` 로 내리는 것은 사람의 판단이다. 할당 상태를 여기에 쓰면 작업이
돌 때마다 사람의 파일이 덮어써진다 — 주석과 포맷이 날아가는 것을 이미 한 번
겪었다(`crew.py` 의 팀 명부). 그래서 **할당은 원장(`var/infra/allocations.json`)에만**
쌓는다. 재고는 사람 것, 원장은 기계 것.

## 실패가 아니라 대기다

풀이 비면 예외가 아니라 `waiting` 이다. 장비를 추가하거나 다른 작업이 반납하면
이어서 돈다. **거부(예외)는 규칙 위반일 때만** — 미승인·없는 존·사업이 허용하지
않은 등급.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .workintake import INFRA_TIERS

# 컨테이너가 붙는 도커 네트워크 — el34 의 compose 가 만든 이름 그대로.
# 존 이름을 우리가 새로 짓지 않는다. 이름이 갈라지면 존 경계가 장식이 된다.
NET_PREFIX = "el34-"

# 이 호스트의 컨테이너 존. 외부 호스트(vm/server)는 물리 LAN 이라 여기 없다 (Q10).
CONTAINER_ZONES = ("ext", "pipe", "dmz", "user", "int")

LEDGER = Path("var") / "infra" / "allocations.json"

STATES = ("none", "ready", "waiting", "released")


class PoolError(Exception):
    """규칙 위반. 자원이 없는 것과는 다르다 — 그건 `waiting` 이다."""


@dataclass
class Host:
    """풀에 등록된 외부 장비 하나."""

    id: str
    kind: str                       # vm | server
    zone: str = ""
    address: str = ""
    cpu: int = 0
    memory_gb: int = 0
    disk_gb: int = 0
    os: str = ""
    status: str = "available"       # available | maintenance  (사람이 정한다)
    notes: str = ""


@dataclass
class Allocation:
    """작업 지시 하나에 배정된 자원."""

    order_id: int
    tier: str
    zone: str = ""
    state: str = "none"
    host_id: str = ""               # vm/server 일 때
    container: str = ""             # container 일 때
    network: str = ""
    reason: str = ""                # waiting 이면 왜 기다리는지
    command: str = ""               # 사람이 집행해야 하면 그 명령
    # **어느 장비에 떴나.** 이걸 안 남기면 화면이 "컨테이너 ready" 라고만 말하고
    # 사람은 이 호스트에 있는 줄 안다 — 찾으러 갔는데 없다.
    placed_on: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def line(self) -> str:
        icon = {"ready": "✔", "waiting": "…", "none": "·", "released": "↩"}.get(
            self.state, "?")
        what = self.host_id or self.container or "환경 불필요"
        where = f"@{self.placed_on} " if self.placed_on else ""
        return f"  {icon} #{self.order_id} {self.tier:<10} {what:<24} {where}{self.reason}"


# ── 재고 (사람이 쓴다) ────────────────────────────────────────────────────


def pool_doc(root: Path) -> dict[str, Any]:
    """`infra/pool.yaml` 원문. **읽기만 한다.**

    `load_pool` 은 한도·장비만 돌려주므로 `routing` 같은 최상위 키를 보려면
    여기를 쓴다 — 실측으로 `load_pool(root)[0].get("routing")` 이 **항상 빈 값**이라
    경로를 열어도 인정되지 않는 버그가 있었다(limits 딕셔너리를 뒤지고 있었다).
    """
    import yaml

    f = Path(root) / "infra" / "pool.yaml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else {}) or {}


@dataclass
class ContainerTarget:
    """작업 컨테이너를 띄울 곳. 비어 있으면 이 호스트의 로컬 도커."""

    docker_host: str = ""
    host_name: str = "이 호스트"
    zone_networks: bool = True

    @property
    def remote(self) -> bool:
        return bool(self.docker_host)

    def env(self) -> dict[str, str]:
        """docker 명령에 씌울 환경. 로컬이면 아무것도 안 바꾼다."""
        import os

        e = dict(os.environ)
        if self.docker_host:
            e["DOCKER_HOST"] = self.docker_host
        return e


def container_target(root: Path) -> ContainerTarget:
    """`infra/pool.yaml` 의 `container:` — 어디에 띄우나."""
    d = (pool_doc(root).get("container") or {})
    return ContainerTarget(
        docker_host=str(d.get("docker_host", "") or ""),
        host_name=str(d.get("host_name", "") or "이 호스트"),
        zone_networks=bool(d.get("zone_networks", True)),
    )


def load_pool(root: Path) -> tuple[dict[str, Any], list[Host]]:
    """`infra/pool.yaml` — 한도와 장비 목록. **읽기만 한다.**"""
    doc = pool_doc(root)
    hosts = []
    for h in doc.get("hosts") or []:
        known = {k: v for k, v in h.items() if k in Host.__dataclass_fields__}
        hosts.append(Host(**known))
    return (doc.get("limits") or {}), hosts


# ── 원장 (기계가 쓴다) ────────────────────────────────────────────────────


def _ledger_path(root: Path) -> Path:
    return Path(root) / LEDGER


def ledger(root: Path) -> list[Allocation]:
    f = _ledger_path(root)
    if not f.is_file():
        return []
    rows = json.loads(f.read_text(encoding="utf-8") or "[]")
    return [Allocation(**{k: v for k, v in r.items()
                          if k in Allocation.__dataclass_fields__}) for r in rows]


def _write_ledger(root: Path, rows: list[Allocation]) -> None:
    f = _ledger_path(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps([r.to_dict() for r in rows], ensure_ascii=False,
                              indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)                                  # 중간에 죽어도 반쪽 원장이 남지 않는다


def allocation_of(root: Path, order_id: int) -> Allocation | None:
    for a in ledger(root):
        if a.order_id == order_id and a.state != "released":
            return a
    return None


def available(root: Path, kind: str) -> list[Host]:
    """지금 꺼내 쓸 수 있는 장비. 원장에 잡혀 있으면 빠진다."""
    _limits, hosts = load_pool(root)
    taken = {a.host_id for a in ledger(root) if a.state == "ready" and a.host_id}
    return [h for h in hosts
            if h.kind == kind and h.status == "available" and h.id not in taken]


# ── 존 경유 (Q10) ────────────────────────────────────────────────────────


def transit_open(root: Path, zone: str) -> bool:
    """외부 장비(물리 LAN)가 이 컨테이너 존에 닿을 수 있나.

    **결정 (Q10): 이 호스트를 게이트웨이로 쓰되 pipe 존을 거친다.**

    다른 안은 각각 대가가 있었다. 오버레이(swarm/VXLAN)로 같은 L2 를 만들면
    깔끔하지만 el34 랩 구성을 건드려야 하고, 존을 아예 분리하면 hand-off 가
    파일 교환으로 제한된다. 호스트 라우팅은 **pipe 존이 PEP 라는 통제 평면의
    전제를 그대로 유지한다** — 존을 넘는 트래픽이 검사 지점을 지난다는 것.

    다만 **경로를 여는 것은 방화벽 변경이라 사람이 한다** (05_conventions 규칙 2).
    여기서는 "열렸다고 적혀 있는가"만 본다. 적혀 있는데 실제로 안 열려 있으면
    작업이 실패하지만, 그건 **시끄러운 실패**다 — 조용히 안 닿는 것보다 낫다.
    """
    routing = pool_doc(root).get("routing") or {}
    if not routing.get("enabled"):
        return False
    return zone in (routing.get("zones") or [])


# ── 능력 탐지 ────────────────────────────────────────────────────────────


def docker_reachable(target: "ContainerTarget | None" = None) -> tuple[bool, str]:
    """도커를 만질 수 있나. `target` 을 주면 **그쪽** 도커를 본다.

    만질 수 **없는 것이 기본이고 그게 맞다** — 에이전트가 도커 소켓을 쥐면
    호스트 루트와 다름없다(최소권한 위반). 못 만지면 명령서를 내고 사람이 집행한다.

    원격은 SSH 라 로컬보다 느리고 더 많이 깨진다(호스트 키·네트워크·키 권한).
    타임아웃을 넉넉히 주되 무한정 기다리지는 않는다 — 여기서 멈추면 할당
    전체가 멈춘다.
    """
    if not shutil.which("docker"):
        return False, "docker 명령이 없다"
    env = target.env() if target else None
    timeout = 30 if (target and target.remote) else 10
    try:
        p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        where = target.host_name if target else "이 호스트"
        return False, f"{where} 도커가 {timeout}초 안에 응답하지 않았다"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"docker 실행 실패: {e}"
    if p.returncode != 0:
        first = (p.stderr or "").strip().splitlines()
        return False, (first[0] if first else "docker 접근 거부")
    return True, ""


# ── 할당 ─────────────────────────────────────────────────────────────────


def _check_rules(root: Path, *, tier: str, zone: str, business: str,
                 approved: bool) -> None:
    """규칙 위반이면 예외. 자원 부족은 여기서 안 본다 — 그건 대기지 위반이 아니다."""
    if not approved:
        raise PoolError("결재가 끝나지 않았다 — 할당은 비가역 행동이다")
    if tier not in INFRA_TIERS:
        raise PoolError(f"알 수 없는 인프라 등급: {tier}")
    if tier == "container":
        ct = container_target(root)
        if not ct.zone_networks:
            # 존 브리지가 없는 장비다. 존을 선언했으면 못 지킨다 — 붙여 놓고
            # "존에 있다"고 기록하면 게이트도 심각도도 거짓이 된다.
            if zone:
                raise PoolError(
                    f"{ct.host_name} 에는 존 브리지가 없다 — {zone} 를 줄 수 없다. "
                    "존 없이 돌리거나(작업 지시의 zone 을 비운다), "
                    "pipe 경유 경로를 먼저 연다 (infra/el34/route-external.sh · Q10)")
        elif zone not in CONTAINER_ZONES:
            raise PoolError(
                f"컨테이너 존이 아니다: {zone} (가능: {', '.join(CONTAINER_ZONES)})")
    elif tier != "none" and not zone:
        raise PoolError("존을 정하지 않았다 — 존 없이는 게이트도 심각도도 계산이 안 된다")

    if tier in ("vm", "server"):
        _limits, hosts = load_pool(root)
        zones = {h.zone for h in hosts if h.kind == tier}
        if zones and zone not in zones and not transit_open(root, zone):
            # 외부 장비는 물리 LAN, 컨테이너 존은 도커 브리지다. 이 호스트를
            # 경유하는 경로가 **실제로 열려 있어야** 닿는다 (QUESTIONS Q10).
            raise PoolError(
                f"풀에 {zone} 존의 {tier} 장비가 없다 (있는 존: {', '.join(sorted(zones))}). "
                f"외부 장비가 {zone}(도커 브리지)에 닿으려면 pipe 경유 경로가 먼저다 — "
                "infra/el34/route-external.sh 로 열고 pool.yaml 의 routing 에 적는다 (Q10)")

    if business:
        from .workintake import validate

        validate(root, business=business, infra_tier=tier)   # 사업이 허용한 등급인가


def plan(root: Path, *, order_id: int, tier: str, zone: str = "",
         business: str = "") -> Allocation:
    """무엇을 가져올지 **집행 없이** 계산한다. 화면에 미리 보여줄 때 쓴다."""
    a = Allocation(order_id=order_id, tier=tier, zone=zone)
    if tier == "none":
        a.state = "none"
        a.reason = "환경 불필요 — 기존 환경에서 수행한다"
        return a

    if tier == "container":
        ct = container_target(root)
        a.container = f"wo{order_id}"
        a.placed_on = ct.host_name
        a.network = f"{NET_PREFIX}{zone}" if (ct.zone_networks and zone) else ""
        limits, _hosts = load_pool(root)
        cap = int(limits.get("container_max", 0) or 0)
        used = sum(1 for x in ledger(root) if x.state == "ready" and x.container)
        if cap and used >= cap:
            a.state, a.reason = "waiting", f"컨테이너 한도 초과 ({used}/{cap})"
            return a
        ok, why = docker_reachable(ct)
        net = f"--network {a.network} " if a.network else ""
        host = f"DOCKER_HOST={ct.docker_host} " if ct.remote else ""
        a.command = (f"{host}docker run -d --name {a.container} {net}"
                     f"--restart unless-stopped <image>")
        if not ok:
            a.state = "waiting"
            a.reason = f"{ct.host_name} 도커에 닿지 않는다 ({why}) — 사람이 집행한다"
            return a
        a.state = "ready"
        # 배치는 line() 이 앞에 붙인다 — 여기서 또 적으면 두 번 나온다.
        a.reason = "" if a.network else "존 브리지 없음 (논리 존 미부여)"
        return a

    free = available(root, tier)
    matching = [h for h in free if h.zone == zone] if zone else free
    via = ""
    if not matching and zone and transit_open(root, zone):
        # 존이 달라도 **경로가 열려 있다고 선언돼 있으면** 쓸 수 있다 (Q10).
        # 열렸다는 선언과 실제로 열린 것은 다르다 — 아니면 여기서 잡고 나중에
        # 시끄럽게 실패한다. 조용히 안 닿는 것보다 낫다.
        matching = free
        via = " · pipe 경유"
    if not matching:
        a.state = "waiting"
        a.reason = (f"풀에 가용한 {tier} 가 없다 — infra/pool.yaml 에 장비를 등록하거나 "
                    "다른 작업이 반납할 때까지 기다린다")
        return a
    h = matching[0]
    a.host_id, a.state = h.id, "ready"
    a.reason = f"{h.address or h.id} · {h.cpu}vCPU/{h.memory_gb}GB{via}"
    return a


def allocate(root: Path, *, order_id: int, tier: str, zone: str = "",
             business: str = "", approved: bool = False) -> Allocation:
    """실제로 잡는다. 규칙 위반이면 예외, 자원 부족이면 `waiting`.

    이미 잡혀 있으면 **그대로 돌려준다** — 두 번 눌러도 자원이 두 개 나가지 않는다.
    """
    _check_rules(root, tier=tier, zone=zone, business=business, approved=approved)

    have = allocation_of(root, order_id)
    if have is not None and have.state == "ready":
        return have

    a = plan(root, order_id=order_id, tier=tier, zone=zone, business=business)
    rows = [x for x in ledger(root) if x.order_id != order_id or x.state == "released"]
    rows.append(a)
    _write_ledger(root, rows)
    return a


def confirm(root: Path, order_id: int, *, by: str) -> Allocation:
    """사람이 집행했다고 **선언한다.**

    도커를 만질 권한이 없으니 우리는 컨테이너가 진짜 떴는지 **확인할 수 없다.**
    확인할 수 없는 것을 확인한 척하지 않는다 — 대신 누가 그렇게 말했는지 남긴다.
    권한이 있는 환경에서는 `allocate` 가 알아서 `ready` 로 가므로 이 함수가 필요 없다.

    Raises:
        PoolError: 잡힌 자원이 없거나, 대기 중이 아닌 것을 확인하려 할 때
    """
    rows = ledger(root)
    hit = next((a for a in rows
                if a.order_id == order_id and a.state == "waiting"), None)
    if hit is None:
        raise PoolError(f"#{order_id} 에 준비대기 중인 할당이 없다")
    if not by:
        raise PoolError("누가 집행했는지 없이는 확인으로 치지 않는다")
    hit.state = "ready"
    hit.reason = f"사람이 집행했다고 확인 — {by} (시스템이 검증한 것은 아니다)"
    _write_ledger(root, rows)
    return hit


def release(root: Path, order_id: int) -> Allocation | None:
    """반납. **삭제가 아니다** — 장비는 그대로 있고 원장에서 놓아줄 뿐이다.

    컨테이너는 실물이 남는다. 정리 명령을 `command` 로 돌려주고,
    집행은 할당 때와 같은 손(사람 또는 권한 있는 프로세스)이 한다.
    """
    rows, hit = ledger(root), None
    for a in rows:
        if a.order_id == order_id and a.state != "released":
            hit = a
            break
    if hit is None:
        return None
    if hit.container:
        hit.command = f"docker rm -f {hit.container}"
    hit.state = "released"
    hit.reason = "반납됨"
    _write_ledger(root, rows)
    return hit


def summary(root: Path) -> dict[str, Any]:
    """`make ops-status` 용 한 줄 요약."""
    limits, hosts = load_pool(root)
    rows = ledger(root)
    ready = [a for a in rows if a.state == "ready"]
    return {
        "hosts_total": len(hosts),
        "hosts_by_kind": {k: sum(1 for h in hosts if h.kind == k)
                          for k in ("vm", "server")},
        "hosts_free": {k: len(available(root, k)) for k in ("vm", "server")},
        "container_max": int(limits.get("container_max", 0) or 0),
        "container_used": sum(1 for a in ready if a.container),
        "allocated": len(ready),
        "waiting": [a.to_dict() for a in rows if a.state == "waiting"],
        "docker": docker_reachable()[0],
    }



__all__ = [
    "CONTAINER_ZONES",
    "NET_PREFIX",
    "STATES",
    "Allocation",
    "Host",
    "PoolError",
    "allocate",
    "allocation_of",
    "available",
    "docker_reachable",
    "ledger",
    "load_pool",
    "plan",
    "release",
    "summary",
]
