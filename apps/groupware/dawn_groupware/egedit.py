"""EG 조정 — **사람이 에이전트에 개입하는 주 통로**.

COMPANY.md 핵심 원리 #2: "사람은 코드·프롬프트를 직접 고치지 않고 EG(규정·페르소나·
정책)를 수정하여 개입한다. 그 변경은 eg_search 를 통해 다음 작업부터 전 에이전트에
전파된다."

이 모듈이 그 문장의 실물이다. 파이프라인은 하나뿐이고 건너뛸 수 없다:

    1. 스냅샷      현재 시드를 백업한다 (되돌릴 수 없으면 아무도 안 고친다)
    2. 초안 기록   시드 파일에 쓴다
    3. **검증**    eg/validate.py — 오류 1개라도 나오면 여기서 멈춘다
    4. 재주입      dawn_core.cli eg load — DB 갱신
    5. 감사        누가·무엇을·언제·검증결과·diff
    실패 시        1번 스냅샷으로 **자동 롤백**. DB 는 손도 안 댄다.

**검증 실패가 곧 롤백**인 게 중요하다. "일단 저장하고 나중에 고치자"가 되면
EG 는 회사의 뇌가 아니라 메모장이 된다.
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 사람이 그룹웨어에서 고칠 수 있는 것 — 여기 없는 파일은 UI 로 안 건드린다.
# 조직도·자산·존은 인프라의 사실이지 정책이 아니다. 그건 레지스트리/시드에서 사람이
# 직접 고치고 리뷰를 받는다.
EDITABLE = {
    "persona": {
        "file": "eg/seed/03_personas.json",
        "collection": "Persona",
        "label": "페르소나 — 에이전트의 행동 원칙",
        "fields": {
            "role": ("역할", "text"),
            "tone": ("어조", "text"),
            "principles": ("원칙 (한 줄에 하나)", "lines"),
            "prohibited": ("금지 (한 줄에 하나)", "lines"),
            "escalation_rule": ("에스컬레이션 규칙", "text"),
        },
    },
    "policy": {
        "file": "eg/seed/02_policies.json",
        "collection": "Policy",
        "label": "정책 — 게이트가 실제로 평가하는 규칙",
        "fields": {
            "statement": ("진술", "text"),
            "rule": ("규칙식 (게이트가 평가한다)", "text"),
            "category": ("분류", "text"),
            "severity": ("심각도", "choice:low|medium|high|critical"),
            "enforcement": ("집행", "choice:log_only|warn|require_hitl|block"),
            "source_ref": ("근거", "text"),
        },
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EGEditError(Exception):
    """조정 실패. 이게 나면 시드도 DB 도 **바뀌지 않은 상태**다."""


@dataclass
class ChangeResult:
    ok: bool
    kind: str
    node_id: str
    diff: list[str] = field(default_factory=list)
    validation: str = ""
    reload: str = ""
    snapshot: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class EGEditor:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.backup_dir = self.root / "var" / "groupware" / "eg-backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # ── 조회 ────────────────────────────────────────────────────────────
    def _seed_path(self, kind: str) -> Path:
        spec = EDITABLE.get(kind)
        if spec is None:
            raise EGEditError(f"편집 대상이 아니다: {kind}")
        return self.root / spec["file"]

    def load(self, kind: str) -> list[dict[str, Any]]:
        spec = EDITABLE[kind]
        doc = json.loads(self._seed_path(kind).read_text(encoding="utf-8"))
        return list(doc.get(spec["collection"], []))

    def get(self, kind: str, node_id: str) -> dict[str, Any] | None:
        return next((x for x in self.load(kind) if x.get("id") == node_id), None)

    # ── 수정 ────────────────────────────────────────────────────────────
    def update(self, kind: str, node_id: str, changes: dict[str, Any], *,
               actor: str, reason: str = "") -> ChangeResult:
        spec = EDITABLE.get(kind)
        if spec is None:
            raise EGEditError(f"편집 대상이 아니다: {kind}")
        allowed = set(spec["fields"])
        unknown = [k for k in changes if k not in allowed]
        if unknown:
            raise EGEditError(f"편집할 수 없는 필드: {', '.join(unknown)}")

        path = self._seed_path(kind)
        before_text = path.read_text(encoding="utf-8")
        doc = json.loads(before_text)
        items = doc.get(spec["collection"], [])
        idx = next((i for i, x in enumerate(items) if x.get("id") == node_id), -1)
        if idx < 0:
            raise EGEditError(f"{spec['collection']} 에 없는 id: {node_id}")

        # 1. 스냅샷
        stamp = _now().replace(":", "").replace("-", "")
        snap = self.backup_dir / f"{stamp}_{kind}_{node_id.replace(':', '_')}.json"
        shutil.copy2(path, snap)

        # 2. 초안 기록
        before_node = json.loads(json.dumps(items[idx], ensure_ascii=False))
        items[idx] = {**items[idx], **changes}
        after_text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        path.write_text(after_text, encoding="utf-8")

        diff = list(difflib.unified_diff(
            json.dumps(before_node, ensure_ascii=False, indent=2).splitlines(),
            json.dumps(items[idx], ensure_ascii=False, indent=2).splitlines(),
            fromfile=f"{node_id} (전)", tofile=f"{node_id} (후)", lineterm="", n=1,
        ))

        res = ChangeResult(ok=False, kind=kind, node_id=node_id, diff=diff,
                           snapshot=str(snap.relative_to(self.root)))

        # 3. 검증 — 여기서 걸리면 DB 는 손도 안 댄다
        ok, out = self._run([sys.executable, "eg/validate.py", "--no-demo"])
        res.validation = out.strip()[-1500:]
        if not ok:
            path.write_text(before_text, encoding="utf-8")     # 자동 롤백
            res.error = "EG 검증 실패 — 시드를 원래대로 되돌렸다. DB 는 바뀌지 않았다."
            return res

        # 4. 재주입
        ok, out = self._run([sys.executable, "-m", "dawn_core.cli", "eg", "load"])
        res.reload = out.strip()[-1000:]
        if not ok:
            path.write_text(before_text, encoding="utf-8")
            self._run([sys.executable, "-m", "dawn_core.cli", "eg", "load"])  # DB 복구
            res.error = "EG 재주입 실패 — 시드와 DB 를 모두 되돌렸다."
            return res

        res.ok = True
        return res

    def restore(self, kind: str, snapshot_rel: str, *, actor: str) -> ChangeResult:
        """스냅샷으로 되돌린다. 되돌리기도 같은 파이프라인(검증 → 재주입)을 탄다."""
        snap = self.root / snapshot_rel
        if not snap.is_file() or self.backup_dir not in snap.parents:
            raise EGEditError(f"스냅샷이 없다: {snapshot_rel}")
        path = self._seed_path(kind)
        before_text = path.read_text(encoding="utf-8")
        shutil.copy2(snap, path)
        res = ChangeResult(ok=False, kind=kind, node_id="(복원)", snapshot=snapshot_rel)
        ok, out = self._run([sys.executable, "eg/validate.py", "--no-demo"])
        res.validation = out.strip()[-1500:]
        if not ok:
            path.write_text(before_text, encoding="utf-8")
            res.error = "복원본이 검증을 통과하지 못했다 — 되돌렸다."
            return res
        ok, out = self._run([sys.executable, "-m", "dawn_core.cli", "eg", "load"])
        res.reload = out.strip()[-1000:]
        res.ok = ok
        if not ok:
            path.write_text(before_text, encoding="utf-8")
            self._run([sys.executable, "-m", "dawn_core.cli", "eg", "load"])
            res.error = "재주입 실패 — 되돌렸다."
        return res

    def snapshots(self, limit: int = 30) -> list[dict[str, str]]:
        out = []
        for p in sorted(self.backup_dir.glob("*.json"), reverse=True)[:limit]:
            out.append({"path": str(p.relative_to(self.root)), "name": p.name,
                        "size": str(p.stat().st_size)})
        return out

    def _run(self, cmd: list[str]) -> tuple[bool, str]:
        try:
            p = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                               timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")


__all__ = ["EDITABLE", "ChangeResult", "EGEditError", "EGEditor"]
