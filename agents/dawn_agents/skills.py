"""스킬 — 에이전트가 실제로 실행하는 원자 도구.

워커 루프의 ②③ 단계:
    skill_preview  실행 전 위험도(LOW/MED/HIGH)·비가역 여부 확인
    skill_run      HIGH/destructive 면 HITL 게이트 통과 후에만

스킬은 `org/tools.yaml` 의 도구 카탈로그와 1:1 대응한다.
카탈로그에 없는 스킬은 등록될 수 없고, 게이트가 막는 스킬은 실행될 수 없다.

P2 는 **읽기 위주 스킬**만 실제 구현한다. 비가역 스킬(방화벽 변경·결제·배포)은
등록만 하고 실행부는 의도적으로 비워 둔다 — 게이트 테스트에는 쓰이되
실수로라도 실행되지 않는다.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SkillError(Exception):
    """스킬 실행 실패."""


class SkillNotImplemented(SkillError):
    """등록됐으나 실행부가 없는 스킬 (비가역 스킬은 의도적으로 미구현)."""


@dataclass
class Preview:
    """skill_preview 결과 — 실행 전에 반드시 본다."""

    skill: str
    risk: str  # LOW | MED | HIGH
    destructive: bool
    action: str  # read | write | execute | irreversible — **위험도와 다른 축**
    description: str
    args_summary: str
    touches_assets: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    implemented: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "risk": self.risk,
            "destructive": self.destructive,
            "action": self.action,
            "description": self.description,
            "args": self.args_summary,
            "touches_assets": self.touches_assets,
            "reasons": self.reasons,
            "implemented": self.implemented,
        }

    def line(self) -> str:
        mark = {"LOW": "🟢", "MED": "🟡", "HIGH": "🔴"}.get(self.risk, "⚫")
        d = " ⚠비가역" if self.destructive else ""
        return f"{mark}{self.risk}{d}  {self.skill}({self.args_summary})"


@dataclass
class SkillResult:
    ok: bool
    output: str
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skill:
    """등록된 스킬 하나."""

    name: str  # org/tools.yaml 의 도구 id
    run: Callable[..., SkillResult] | None  # None = 미구현 (비가역 스킬)
    touches: list[str] = field(default_factory=list)  # 관여하는 EG Asset id
    arg_names: list[str] = field(default_factory=list)


class SkillRegistry:
    """카탈로그에 등록된 스킬만 담는다."""

    def __init__(self, catalog) -> None:
        self.catalog = catalog  # dawn_core.gate.ToolCatalog
        self._skills: dict[str, Skill] = {}

    def register(
        self,
        name: str,
        run: Callable[..., SkillResult] | None = None,
        *,
        touches: list[str] | None = None,
        arg_names: list[str] | None = None,
    ) -> None:
        if not self.catalog.known(name):
            raise SkillError(f"카탈로그에 없는 스킬: {name} — org/tools.yaml 에 먼저 등록하라")
        # 만지는 자산은 **카탈로그가 권위**다. 등록부가 안 적으면 카탈로그에서 채운다 —
        # 여기 빠뜨리면 심각도가 0 으로 계산돼 가장 위험한 도구가 안전해 보인다.
        self._skills[name] = Skill(
            name, run, list(touches or self.catalog.touches(name)), arg_names or []
        )

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError:
            raise SkillError(f"등록되지 않은 스킬: {name}") from None

    # ── ② skill_preview ────────────────────────────────────────────────
    def preview(self, name: str, **kwargs: Any) -> Preview:
        """실행 전 위험도 확인. **이걸 건너뛴 run 은 금지다.**"""
        skill = self.get(name)
        spec = self.catalog.tools.get(name, {})
        reasons: list[str] = []
        if spec.get("destructive"):
            reasons.append("비가역 — 되돌릴 수 없다. HITL 필수")
        if spec.get("risk") == "HIGH":
            reasons.append("고위험 도구")
        if skill.run is None:
            reasons.append("실행부 미구현 — 이 스킬은 preview 전용이다")

        args = ", ".join(f"{k}={_short(v)}" for k, v in kwargs.items())
        return Preview(
            skill=name,
            risk=str(spec.get("risk", "MED")),
            destructive=bool(spec.get("destructive")),
            action=action_of(spec),
            description=str(spec.get("desc", "")),
            args_summary=args,
            touches_assets=list(skill.touches),
            reasons=reasons,
            implemented=skill.run is not None,
        )

    # ── ③ skill_run ────────────────────────────────────────────────────
    def run(self, name: str, **kwargs: Any) -> SkillResult:
        """실행. **게이트 판정은 호출자(워커)가 이미 끝냈다고 가정한다.**"""
        skill = self.get(name)
        if skill.run is None:
            raise SkillNotImplemented(
                f"{name} 은 등록만 되어 있고 실행부가 없다 "
                f"(비가역 스킬은 의도적으로 미구현 — P2 에서 실행하지 않는다)"
            )
        try:
            return skill.run(**kwargs)
        except SkillError:
            raise
        except Exception as exc:
            return SkillResult(ok=False, output="", error=f"{type(exc).__name__}: {exc}")


# 위험도 → 행동 비가역성 **폴백**. 카탈로그가 `action:` 을 선언하면 그게 이긴다.
# 둘은 다른 축이다: `fin.expense_read` 는 MED 위험이지만 read 다 —
# 위험하다고 상태가 바뀌지는 않는다. 이걸 섞으면 조회 한 번에 승인이 필요해지고,
# 그러면 자율화 사다리가 무용해진다 (P5 에서 실제로 그렇게 됐다).
RISK_TO_ACTION = {"LOW": "read", "MED": "write", "HIGH": "execute"}
VALID_ACTIONS = ("read", "write", "execute", "irreversible")


def action_of(spec: dict[str, Any]) -> str:
    """이 도구가 무엇을 하는가 — 읽나, 쓰나, 실행하나, 되돌릴 수 없나."""
    if spec.get("destructive"):
        return "irreversible"            # 비가역 선언이 언제나 이긴다
    declared = str(spec.get("action", "")).strip()
    if declared in VALID_ACTIONS:
        return declared
    return RISK_TO_ACTION.get(str(spec.get("risk", "MED")), "write")


def _short(v: Any, n: int = 60) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


# ── 기본 스킬 구현 (읽기 위주) ───────────────────────────────────────────


def build_default_registry(catalog, *, root: Path, eg_store=None,
                          agent_id: str = "") -> SkillRegistry:
    """P2 데모에 필요한 스킬을 등록한다.

    읽기 스킬은 실동작, 비가역 스킬은 등록만(게이트 테스트용).
    """
    reg = SkillRegistry(catalog)

    # ── eg.* — EG 조회·기록 ─────────────────────────────────────────────
    def eg_search(query: str, type: str | None = None, limit: int = 5) -> SkillResult:
        if eg_store is None:
            return SkillResult(False, "", "EG 스토어가 없다 — make eg-load")
        hits = eg_store.search(query, type=type, limit=limit)
        lines = [f"{h.id} [{h.type}] {h.name}" for h in hits]

        # **판례를 함께 준다.** 지금까지 여기서 나오는 것은 페르소나 — 사람이
        # 적어 둔 추상 원칙뿐이었다. 원칙은 한 번 쓰이고 낡는데, 실제로 어떻게
        # 판단해 왔는지는 볼 방법이 없었다. 그래서 "이 회사는 이런 걸 어떻게
        # 보나"에 답하려면 착수 전에 판례가 필요하다.
        #
        # `type` 을 지정한 조회에는 붙이지 않는다 — 특정 타입을 달라고 한
        # 요청에 다른 타입을 섞어 주면 호출자의 계약을 깬다.
        prec = []
        if not type:
            from dawn_core.eg import judgment
            prec = judgment.precedents(eg_store, query, limit=3)
            if prec:
                lines.append("")
                lines.append("판례 (사람이 실제로 내린 결정):")
                for j in prec:
                    c = j.content
                    lines.append(f"  {j.id} {c.get('decision','')} — "
                                 f"{c.get('reason','')[:80]}")

        # **무엇을 읽었는지**를 남긴다. 개수만 남기면 "이 판단이 무엇에 근거했나"를
        # 사후에 재구성할 수 없다 — EG 축적 루프가 감사 불가능해진다.
        return SkillResult(True, "\n".join(lines) or "(없음)",
                           meta={"hits": len(hits), "hit_ids": [h.id for h in hits],
                                 "precedents": [j.id for j in prec]})

    def eg_record(kind: str, summary: str, detail: str = "") -> SkillResult:
        if eg_store is None:
            return SkillResult(False, "", "EG 스토어가 없다")
        import hashlib
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        nid = f"{kind.lower()}:{hashlib.sha256((summary + now).encode()).hexdigest()[:12]}"
        ntype = {"task": "Task", "finding": "Finding", "observation": "Observation"}.get(
            kind.lower(), "Observation"
        )
        eg_store.upsert_node(
            nid,
            ntype,
            summary[:120],
            {"summary": summary, "detail": detail, "recorded_at": now},
            # created_by 를 'agent' 로 뭉뚱그리면 260개 노드가 전부 같은 출처가 된다 —
            # "누가 이걸 알아냈나"를 못 묻는다. 실제 에이전트 id 를 박는다.
            {"layer": "runtime", "created_by": agent_id or "agent"},
        )
        return SkillResult(True, f"기록됨: {nid} ({ntype})", meta={"node_id": nid})

    # eg.* 의 자산 선언은 **카탈로그가 정한다** (`org/tools.yaml` → asset:eg-db).
    #
    # P2 때는 여기에 자산을 달지 않았다. 이유가 둘이었는데 지금은 둘 다 풀렸다:
    #   ① "①④ 단계가 자기 인프라 때문에 HITL 로 막힌다"
    #      → 카탈로그의 `loop_instrumentation: true` 가 판정을 log_only 로 강제한다.
    #        자산·심각도는 그대로 기록하되 막지 않는다.
    #   ② "Task-TOUCHED->Asset 은 업무 자산을 뜻하지 기록 위치가 아니다"
    #      → 맞다. 그래서 관제는 eg.* 를 **존 진입으로 세지 않는다**
    #        (게이트 미통과 = 문을 지난 적 없음). 자기 자리에서의 원격 조회로 그린다.
    #
    # 선언을 되살린 이유: EG DB 접근이 로그에 아예 안 남으면 "이 판단이 무엇을
    # 읽고 나왔나"를 감사할 수 없다.
    reg.register("eg.search", eg_search, arg_names=["query", "type", "limit"])
    reg.register("eg.record", eg_record, arg_names=["kind", "summary", "detail"])

    # ── skill.* — 메타 (워커가 직접 호출) ───────────────────────────────
    reg.register("skill.preview", lambda **k: SkillResult(True, "메타 스킬"))
    reg.register("skill.run", lambda **k: SkillResult(True, "메타 스킬"))

    # ── fs.* — 저장소 파일 (루트 밖으로 못 나간다) ──────────────────────
    def fs_read(path: str, max_bytes: int = 40000) -> SkillResult:
        target = _confine(root, path)
        if not target.is_file():
            return SkillResult(False, "", f"파일 없음: {path}")
        return SkillResult(True, target.read_text(encoding="utf-8", errors="replace")[:max_bytes])

    def fs_write(path: str, content: str) -> SkillResult:
        target = _confine(root, path)
        # 산출물은 워크스페이스 안에만
        if not str(target).startswith(str((root / "var").resolve())):
            return SkillResult(False, "", "쓰기는 var/ 아래로만 허용된다")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return SkillResult(True, f"작성됨: {target.relative_to(root)} ({len(content)}자)")

    reg.register("fs.read", fs_read, arg_names=["path"], touches=["asset:source"])
    reg.register("fs.write", fs_write, arg_names=["path", "content"], touches=["asset:source"])
    reg.register("fs.delete", None, touches=["asset:source"])  # 비가역 — 미구현

    # ── sec.* — 관제 조회 (el34) ────────────────────────────────────────
    def _assessor(endpoint: str, payload: dict | None = None) -> SkillResult:
        import os
        import urllib.error
        import urllib.request

        base = os.getenv("EL34_ASSESSOR_URL", "http://10.20.32.55:8000").rstrip("/")
        key = os.getenv("EL34_API_KEY", "")
        req = urllib.request.Request(
            f"{base}{endpoint}",
            data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return SkillResult(True, r.read(8000).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return SkillResult(
                False, "", f"HTTP {e.code}: {e.read(300).decode('utf-8', 'replace')}"
            )
        except Exception as e:
            return SkillResult(False, "", f"{type(e).__name__}: {e}")

    reg.register(
        "sec.siem_query",
        lambda **k: _assessor("/activity", k),
        touches=["asset:siem"],
        arg_names=["window", "limit"],
    )
    reg.register(
        "sec.suricata_query", lambda **k: _assessor("/activity", k), touches=["asset:fw-ips"]
    )
    reg.register("sec.waf_query", lambda **k: _assessor("/activity", k), touches=["asset:web-vuln"])

    def trace_query(limit: int = 20) -> SkillResult:
        d = root / "var" / "traces"
        if not d.is_dir():
            return SkillResult(True, "(트레이스 없음)")
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        return SkillResult(True, "\n".join(f.name for f in files), meta={"count": len(files)})

    # 트레이스 레이크 = 수집 계층의 자산(asset:assessor). 자산을 선언하지 않으면
    # 관제가 이 실행을 어느 존에 그려야 할지 모른다 — 아바타가 허공에 뜬다.
    reg.register("sec.trace_query", trace_query, touches=["asset:assessor"])
    reg.register("sec.docker_inspect", None, touches=["asset:bastion"])
    # 비가역 대응 스킬 — 등록만. 실행되면 안 된다.
    for n in ("sec.container_stop", "sec.firewall_change", "sec.credential_revoke", "sec.isolate"):
        reg.register(n, None, touches=["asset:fw-ips"])

    # ── sys/dev ────────────────────────────────────────────────────────
    def run_command(command: str, timeout: int = 30) -> SkillResult:
        # 읽기 명령만. 상태 변경은 스킬 게이트가 막는다.
        banned = ("rm ", "mkfs", "dd ", ">", ">>", "shutdown", "reboot", "kill ", "docker ")
        if any(b in command for b in banned):
            return SkillResult(False, "", f"상태 변경 명령은 이 스킬로 실행할 수 없다: {command}")
        try:
            p = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=root,
            )
            return SkillResult(
                p.returncode == 0, p.stdout[:8000], p.stderr[:2000], {"returncode": p.returncode}
            )
        except subprocess.TimeoutExpired:
            return SkillResult(False, "", f"타임아웃 {timeout}s")

    reg.register("sys.run_command", run_command, arg_names=["command"])
    reg.register("sys.deploy", None)
    reg.register("sys.rm_rf_root", None)
    reg.register("sys.mkfs", None)
    reg.register("dev.git", lambda **k: run_command(f"git {k.get('args', 'status --short')}"))
    reg.register("dev.tests", lambda **k: run_command("make test"))
    reg.register("dev.dependency_add", None)

    # ── net ────────────────────────────────────────────────────────────
    reg.register("net.web_search", None)  # P2 미구현
    reg.register("net.fetch", None)

    # ── fin.* — L3. 데모용 로컬 원장 (실제 재무 시스템은 P5) ────────────
    def ledger_read(account: str = "", limit: int = 20) -> SkillResult:
        p = root / "var" / "demo" / "ledger.json"
        if not p.is_file():
            return SkillResult(True, "[]", meta={"note": "데모 원장 없음"})
        rows = json.loads(p.read_text(encoding="utf-8"))
        if account:
            rows = [r for r in rows if r.get("account") == account]
        return SkillResult(True, json.dumps(rows[:limit], ensure_ascii=False, indent=2))

    def expense_read(request_id: str = "", limit: int = 20) -> SkillResult:
        p = root / "var" / "demo" / "expenses.json"
        if not p.is_file():
            return SkillResult(True, "[]", meta={"note": "데모 경비 없음"})
        rows = json.loads(p.read_text(encoding="utf-8"))
        if request_id:
            rows = [r for r in rows if r.get("request_id") == request_id]
        return SkillResult(True, json.dumps(rows[:limit], ensure_ascii=False, indent=2))

    def expense_write(request_id: str, draft: str) -> SkillResult:
        out = root / "var" / "demo" / "drafts"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{request_id}.md").write_text(draft, encoding="utf-8")
        return SkillResult(True, f"전표 초안 저장: var/demo/drafts/{request_id}.md")

    reg.register(
        "fin.ledger_read", ledger_read, touches=["asset:ledger"], arg_names=["account", "limit"]
    )
    reg.register(
        "fin.expense_read", expense_read, touches=["asset:ledger"], arg_names=["request_id"]
    )
    reg.register(
        "fin.expense_write",
        expense_write,
        touches=["asset:ledger"],
        arg_names=["request_id", "draft"],
    )
    reg.register("fin.ledger_write", None, touches=["asset:ledger"])  # 비가역

    # ── 절대 금지 (등록만 — 게이트가 이미 deny) ─────────────────────────
    #
    # 실행부가 없어도 **자산은 선언한다.** 게이트가 막은 시도도 관제 케이스가 되고,
    # 그 심각도는 "무엇을 건드리려 했나"에서 나온다. 자산이 비면 지급 실행 시도가
    # 원장 기입 시도보다 가볍게 잡힌다 — 거꾸로다.
    for n, touches in (
        ("hr.data_read", ["asset:payroll"]),
        ("comm.external_send", ["asset:mail"]),
        ("pay.execute", ["asset:payment"]),
        ("ctl.modify_gate", []),            # 통제 평면 자체 — EG 자산이 아니다
        ("ctl.modify_kill_switch", []),
        ("ctl.cross_tenant", []),
    ):
        reg.register(n, None, touches=touches)

    return reg


def _confine(root: Path, path: str) -> Path:
    """경로가 저장소 루트를 벗어나지 못하게 한다 (traversal 방지)."""
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise SkillError(f"저장소 밖 경로 접근 금지: {path}")
    return target
