"""통제 평면 조정 — 서버에 들어가지 않고 **웹에서** 에이전트·문서를 고친다.

CLAUDE.md 규칙 7: "에이전트 행동을 바꿀 땐 코드가 아니라 EG 와 통제 평면 문서를
고친다." 그런데 지금까지 그 문서를 고치려면 SSH 로 들어가 편집기를 열어야 했다.
**고치기 어려운 통제는 안 고쳐지고, 안 고쳐지는 통제는 현실과 어긋난다.**

[`egedit.py`](egedit.py) 와 같은 규율을 쓴다. 파이프라인은 하나뿐이고 건너뛸 수 없다:

    1. 스냅샷    현재 파일을 백업한다 (되돌릴 수 없으면 아무도 안 고친다)
    2. 초안 기록  파일에 쓴다
    3. **검증**   시크릿 → 레지스트리 무결성 → 4계층 컴파일 (단조 축소)
    4. 감사      누가·무엇을·언제·diff
    실패 시      스냅샷으로 **자동 롤백**

## 왜 검증이 이 순서인가

**시크릿이 먼저다.** 웹 입력은 git 을 거치지 않으므로 pre-commit 의 gitleaks 가
안 돈다. 여기서 안 막으면 SOUL.md 에 붙여넣은 키가 그대로 저장된다.

**그다음이 컴파일이다.** `gate.yaml` 을 웹에서 고칠 수 있다는 것은 곧 **경계를
넓힐 수 있다는 뜻**이다. 통제 평면 컴파일러가 단조 축소를 강제하므로, 저장 전에
실제로 컴파일해 보고 실패하면 되돌린다. 화면에서 막는 게 아니라 **여기가 경계다.**

## 무엇을 고칠 수 있나

`EDITABLE` 에 있는 것뿐이다. 임의 경로를 받으면 이 화면이 곧 원격 파일 쓰기가 된다.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 웹에서 만질 수 있는 계층. 여기 없는 것은 UI 로 안 건드린다.
#
# `COMPANY.md`(L1)는 **일부러 뺐다.** 전사 헌법이라 한 줄이 전 에이전트를 바꾸고,
# 되돌리는 판단도 사람 여럿이 해야 한다. 그건 git 리뷰를 거치는 게 맞다.
LAYERS = {
    "team": {
        "label": "L2 — 팀 행동 규칙 (AGENT_TEAM.md)",
        "hint": "이 팀 에이전트 전체에 적용된다. 경계와 그 이유를 적는다.",
    },
    "gate": {
        "label": "L2 — 팀 경계 (gate.yaml)",
        "hint": "도구 허용·차단, 자율 등급, HITL, 예산. **넓히면 저장 안 된다** (단조 축소).",
    },
    "work": {
        "label": "L3 — 업무 SOP (*_WORK.md)",
        "hint": "절차·완료 조건·실패 시 처리.",
    },
    "soul": {
        "label": "L4 — 에이전트 자아 (SOUL.md)",
        "hint": "나는 누구인가·무엇을 안 하는가·언제 멈추는가.",
    },
    "agent": {
        "label": "L4 — 에이전트 매니페스트 (agent.yaml)",
        "hint": "소속 팀·페르소나·선언 도구·존·자율 등급.",
    },
}

MAX_BYTES = 200_000                 # 문서 하나가 이보다 크면 UI 로 다룰 것이 아니다

# 붙여넣기 사고를 막는 최소 패턴. gitleaks 가 있으면 그쪽이 본체고 이건 보조다.
_SECRET_HINTS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{16,}"),
)


class CPEditError(Exception):
    """조정 실패. 이게 나면 파일은 **바뀌지 않은 상태**다."""


@dataclass
class Target:
    """고칠 수 있는 것 하나."""

    kind: str
    id: str
    path: Path
    label: str = ""
    exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "path": str(self.path)}


@dataclass
class EditResult:
    ok: bool
    kind: str
    id: str
    diff: list[str] = field(default_factory=list)
    validation: str = ""
    snapshot: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _yaml_str(s: str) -> str:
    """사람이 친 문자열을 YAML 스칼라로. **인용 없이 넣으면 매니페스트가 깨진다.**

    실측: 이름이 `[테스트] 조정` 이면 `[` 가 플로우 시퀀스로 읽혀 파싱이 통째로
    실패했다. JSON 문자열은 YAML 이중인용 스칼라와 호환되므로 그대로 쓴다.
    """
    import json

    return json.dumps(s or "", ensure_ascii=False)


# ── 무엇이 있나 ──────────────────────────────────────────────────────────


def targets(root: Path) -> list[Target]:
    """고칠 수 있는 것 전부. **레지스트리에서 나온다** — 목록을 따로 안 적는다.

    따로 적으면 팀이 늘 때마다 두 곳을 고쳐야 하고, 한쪽을 빠뜨리면 새 팀이
    UI 에서 안 보인다.
    """
    from dawn_core import Registry

    reg = Registry.load(root)
    out: list[Target] = []
    for t in sorted(reg.teams.values(), key=lambda x: x.id):
        for kind, fname in (("team", "AGENT_TEAM.md"), ("gate", "gate.yaml")):
            p = t.dir / fname
            out.append(Target(kind=kind, id=t.id, path=p,
                              label=t.data.get("name", t.id), exists=p.is_file()))
    for a in sorted(reg.agents.values(), key=lambda x: x.id):
        for kind, fname in (("soul", "SOUL.md"), ("agent", "agent.yaml")):
            p = a.dir / fname
            out.append(Target(kind=kind, id=a.id, path=p,
                              label=a.data.get("name", a.id), exists=p.is_file()))
    for w in sorted(reg.works.values(), key=lambda x: x.id):
        out.append(Target(kind="work", id=w.id, path=w.source,
                          label=w.meta.get("name", w.id), exists=w.source.is_file()))
    return out


def resolve(root: Path, kind: str, ident: str) -> Target:
    """`kind`/`id` → 파일. **목록에 있는 것만** 돌려준다.

    경로를 직접 받지 않는 이유가 여기 있다 — 받으면 `../../.ssh/id_rsa` 가 온다.
    """
    if kind not in LAYERS:
        raise CPEditError(f"알 수 없는 계층: {kind}")
    for t in targets(root):
        if t.kind == kind and t.id == ident:
            return t
    raise CPEditError(f"없는 대상: {kind}/{ident}")


def read(root: Path, kind: str, ident: str) -> str:
    t = resolve(root, kind, ident)
    return t.path.read_text(encoding="utf-8") if t.exists else ""


# ── 검증 ─────────────────────────────────────────────────────────────────


def scan_secrets(root: Path, text: str) -> str:
    """시크릿이면 사유를 돌려준다. 통과면 빈 문자열.

    **웹 입력은 git 을 안 거친다** — pre-commit 의 gitleaks 가 안 돈다.
    여기서 안 막으면 붙여넣은 키가 그대로 저장된다.
    """
    for rx in _SECRET_HINTS:
        m = rx.search(text)
        if m:
            return f"시크릿으로 보이는 문자열 — {m.group(0)[:12]}… (환경변수/볼트를 써라)"

    gl = Path(root) / "bin" / "gitleaks"
    if not gl.is_file():
        return ""
    tmp = Path(root) / "var" / "cpedit" / "scan.txt"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        p = subprocess.run([str(gl), "detect", "--no-git", "--redact", "--no-banner",
                            "--source", str(tmp)],
                           capture_output=True, text=True, timeout=30)
        return "gitleaks 가 시크릿을 찾았다" if p.returncode != 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""                       # 스캐너가 없다고 저장을 막지는 않는다
    finally:
        tmp.unlink(missing_ok=True)


def _validate(root: Path, kind: str, ident: str) -> str:
    """저장된 상태가 성립하는가. 실패하면 사유를 예외로 올린다(→ 롤백).

    **화면에서 막는 게 아니라 여기가 경계다.** 게이트를 넓히려는 시도는 통제 평면
    컴파일러의 단조 축소가 잡는다 — 그 로직을 여기서 다시 쓰지 않는다.
    """
    from dawn_core import Registry
    from dawn_core.control_plane import compile_agent

    try:
        reg = Registry.load(root)
        reg.check_integrity()
    except Exception as e:
        raise CPEditError(f"레지스트리 검증 실패 — {e}") from e

    # 이 변경이 닿는 에이전트만 컴파일한다. 전부 돌리면 남의 문제로 내 저장이 막힌다.
    if kind in ("soul", "agent"):
        who = [ident] if ident in reg.agents else []
    elif kind in ("team", "gate"):
        who = [a.id for a in reg.agents.values() if a.data.get("team") == ident]
    else:
        who = [a.id for a in reg.agents.values()
               if ident in (a.data.get("works") or [])]

    for aid in who:
        try:
            compile_agent(reg, aid)
        except Exception as e:
            raise CPEditError(f"{aid} 컴파일 실패 — {e}") from e
    return f"레지스트리 무결성 통과 · 컴파일 {len(who)}명"


# ── 쓴다 ─────────────────────────────────────────────────────────────────


def _snapshot(root: Path, path: Path) -> Path:
    d = Path(root) / "var" / "cpedit" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    stamp = _now().replace(":", "").replace("-", "")
    dst = d / f"{stamp}-{path.name}"
    if path.is_file():
        shutil.copy2(path, dst)
    else:
        dst.write_text("", encoding="utf-8")    # 새로 만드는 경우 — 롤백은 삭제다
    return dst


def save(root: Path, kind: str, ident: str, text: str, *, actor: str,
         reason: str) -> EditResult:
    """고친다. **검증에 실패하면 되돌린다.**"""
    if not reason.strip():
        raise CPEditError("변경 사유가 필요하다 — 통제 평면 변경은 에이전트의 행동을 바꾼다")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise CPEditError(f"문서가 너무 크다 ({MAX_BYTES:,} 바이트 초과)")

    why = scan_secrets(root, text)
    if why:
        raise CPEditError(why)

    t = resolve(root, kind, ident)
    before = t.path.read_text(encoding="utf-8") if t.exists else ""
    if before == text:
        return EditResult(ok=True, kind=kind, id=ident, validation="변경 없음")

    snap = _snapshot(root, t.path)
    existed = t.path.is_file()
    t.path.parent.mkdir(parents=True, exist_ok=True)
    t.path.write_text(text, encoding="utf-8")
    try:
        validation = _validate(root, kind, ident)
    except CPEditError as e:
        if existed:
            shutil.copy2(snap, t.path)          # 되돌린다
        else:
            t.path.unlink(missing_ok=True)
        raise CPEditError(f"{e}  ← 되돌렸다") from e

    return EditResult(ok=True, kind=kind, id=ident, validation=validation,
                      snapshot=str(snap.relative_to(root)),
                      diff=list(difflib.unified_diff(
                          before.splitlines(), text.splitlines(),
                          fromfile="before", tofile="after", lineterm=""))[:400])


# ── 에이전트 추가·삭제 ───────────────────────────────────────────────────


def create_agent(root: Path, *, agent_id: str, team: str, name: str, persona: str,
                 works: list[str], tools: list[str], zone: str, autonomy: str,
                 actor: str, reason: str) -> EditResult:
    """에이전트를 만든다. **팀 명부에 등록까지 해야 끝난다** (양방향 참조).

    `crew.py` 의 편성과 다른 점: 저건 작업 지시에 딸린 **임시** 에이전트라 끝나면
    회수되고, 이건 사람이 만드는 **상시** 에이전트다. 검증은 같은 것을 쓴다.
    """
    from dawn_core import Registry
    from dawn_core.crew import _team_roster

    if not reason.strip():
        raise CPEditError("변경 사유가 필요하다")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", agent_id):
        raise CPEditError(f"에이전트 id 형식이 아니다: {agent_id} (소문자·숫자·하이픈)")

    reg = Registry.load(root)
    if agent_id in reg.agents:
        raise CPEditError(f"이미 있다: {agent_id}")
    if team not in reg.teams:
        raise CPEditError(f"없는 팀: {team}")
    if not (reg.teams[team].dir / "AGENT_TEAM.md").is_file():
        raise CPEditError(
            f"{team} 에 L2(AGENT_TEAM.md)가 없다 — 행동 규칙 없이 일하는 팀을 만들지 않는다. "
            "먼저 그 팀의 L2 를 쓴 뒤에 사람을 넣어라")
    unknown = [w for w in works if w not in reg.works]
    if unknown:
        raise CPEditError(f"없는 업무 SOP: {', '.join(unknown)}")
    if autonomy not in ("A0", "A1", "A2", "A3"):
        raise CPEditError(f"자율 등급이 아니다: {autonomy}")
    if not re.fullmatch(r"[a-z]+", zone or ""):
        raise CPEditError(f"존 형식이 아니다: {zone}")
    bad = [x for x in tools if not re.fullmatch(r"[a-z0-9_.*]+", x)]
    if bad:
        raise CPEditError(f"도구 이름 형식이 아니다: {', '.join(bad)}")

    d = Path(root) / "org" / "agents" / agent_id
    tf = reg.teams[team].source
    tf_before = tf.read_text(encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    try:
        # 사람이 친 문자열은 **인용한다.** 실측: 이름이 `[테스트] 조정` 이면
        # `[` 가 YAML 플로우 시퀀스로 읽혀 매니페스트가 통째로 깨졌다.
        (d / "agent.yaml").write_text(
            f"id: {agent_id}\nteam: {team}\nname: {_yaml_str(name)}\nrole: worker\n"
            f"persona: {_yaml_str(persona or reg.teams[team].data.get('persona_default', 'corporate'))}\n"
            f"works: [{', '.join(works)}]\n"
            f"autonomy: {autonomy}\nmodel_hint: opus\n"
            f"tools: [{', '.join(tools)}]\nstatus: active\nzone: {zone}\n"
            f"telemetry: {{emit: true, service_name: dawn.agent.{agent_id}}}\n",
            encoding="utf-8")
        (d / "SOUL.md").write_text(
            f"# SOUL.md — {agent_id}\n\n"
            f"> **통제 평면 L4.** 나 한 명에게만 적용된다.\n\n"
            f"## 나는 누구인가\n\n{name}.\n\n"
            f"> 이 문서는 그룹웨어에서 만들어졌다 ({actor}). **아직 비어 있다** —\n"
            f"> 판단 성향·내가 하지 않는 것·내가 멈추는 순간을 채워라. 비어 있으면\n"
            f"> 이 에이전트는 팀 규칙(L2)만 가지고 움직인다.\n",
            encoding="utf-8")
        _team_roster(root, tf, add=agent_id)
        validation = _validate(root, "agent", agent_id)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)     # 되돌린다
        tf.write_text(tf_before, encoding="utf-8")
        raise CPEditError(f"{e}  ← 되돌렸다") from e

    return EditResult(ok=True, kind="agent", id=agent_id, validation=validation)


def delete_agent(root: Path, agent_id: str, *, actor: str, reason: str) -> EditResult:
    """에이전트를 지운다. **명부에서도 빼야 무결성이 유지된다.**"""
    from dawn_core import Registry
    from dawn_core.crew import _team_roster

    if not reason.strip():
        raise CPEditError("변경 사유가 필요하다")
    reg = Registry.load(root)
    a = reg.agents.get(agent_id)
    if a is None:
        raise CPEditError(f"없는 에이전트: {agent_id}")
    if agent_id.startswith("wo"):
        raise CPEditError(
            f"{agent_id} 는 작업 지시에 딸린 임시 에이전트다 — "
            "여기서 지우지 말고 작업을 마감하라 (dawn-biz close)")

    team = a.data.get("team", "")
    tf = reg.teams[team].source if team in reg.teams else None
    backup = Path(root) / "var" / "cpedit" / "snapshots" / f"{_now()}-{agent_id}"
    shutil.copytree(a.dir, backup, dirs_exist_ok=True)
    tf_before = tf.read_text(encoding="utf-8") if tf else ""
    try:
        shutil.rmtree(a.dir)
        if tf:
            _team_roster(root, tf, remove=agent_id)
        Registry.load(root).check_integrity()
    except Exception as e:
        shutil.copytree(backup, a.dir, dirs_exist_ok=True)
        if tf:
            tf.write_text(tf_before, encoding="utf-8")
        raise CPEditError(f"{e}  ← 되돌렸다") from e

    return EditResult(ok=True, kind="agent", id=agent_id,
                      validation="삭제됨 · 무결성 통과",
                      snapshot=str(backup.relative_to(root)))


__all__ = [
    "LAYERS",
    "MAX_BYTES",
    "CPEditError",
    "EditResult",
    "Target",
    "create_agent",
    "delete_agent",
    "read",
    "resolve",
    "save",
    "scan_secrets",
    "targets",
]
