"""조직·사업·에이전트·업무 레지스트리.

`org/` 아래의 YAML 매니페스트와 `work/` 아래의 *_WORK.md 프런트매터를 읽어
하나의 조회 가능한 레지스트리로 만든다.

설계 원칙 (COMPANY.md §4-4 "사업은 플러그인"):
  - 사업/조직/에이전트는 **코드가 아니라 매니페스트로 존재한다**.
  - 새 사업 편입 = YAML 파일 추가. 이 모듈은 수정되지 않는다.
  - 참조 무결성은 로드 시점에 강제한다 — 깨진 참조로는 아무것도 기동하지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from .paths import Paths

SCHEMA_DIR = Path(__file__).parent / "schemas"

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class RegistryError(Exception):
    """레지스트리 로드·검증 실패. 이게 나면 아무 에이전트도 기동하지 않는다."""


@cache
def _schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _validate(doc: dict[str, Any], schema_name: str, source: Path) -> None:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover
        raise RegistryError(
            "jsonschema 가 필요하다. `make setup` 또는 `pip install jsonschema`"
        ) from exc
    validator = jsonschema.Draft202012Validator(_schema(schema_name))
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        lines = [f"{source}: 스키마({schema_name}) 위반"]
        for e in errors:
            loc = "/".join(str(p) for p in e.path) or "(root)"
            lines.append(f"  - {loc}: {e.message}")
        raise RegistryError("\n".join(lines))


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: YAML 파싱 실패 — {exc}") from exc
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        raise RegistryError(f"{path}: 최상위가 매핑이어야 한다")
    return doc


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """마크다운의 YAML 프런트매터와 본문을 분리한다."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: 프런트매터 파싱 실패 — {exc}") from exc
    if not isinstance(meta, dict):
        raise RegistryError(f"{path}: 프런트매터가 매핑이어야 한다")
    return meta, text[m.end() :]


# ── 엔티티 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Business:
    data: dict[str, Any]
    source: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def is_live(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class Division:
    data: dict[str, Any]
    source: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def dir(self) -> Path:
        return self.source.parent


@dataclass(frozen=True)
class Team:
    data: dict[str, Any]
    source: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def division_id(self) -> str:
        return self.data["division"]

    @property
    def dir(self) -> Path:
        return self.source.parent

    @property
    def agent_ids(self) -> list[str]:
        return list(self.data.get("agents") or [])


@dataclass(frozen=True)
class Agent:
    data: dict[str, Any]
    source: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def team_id(self) -> str:
        return self.data["team"]

    @property
    def dir(self) -> Path:
        return self.source.parent

    @property
    def work_ids(self) -> list[str]:
        return list(self.data.get("works") or [])

    @property
    def is_active(self) -> bool:
        return self.data.get("status") == "active"


@dataclass(frozen=True)
class Work:
    """work/<domain>/<NAME>_WORK.md — L3 통제 문서."""

    meta: dict[str, Any]
    body: str
    source: Path

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def risk(self) -> str:
        return self.meta.get("risk", "MED")


# ── 레지스트리 ──────────────────────────────────────────────────────────

_WORK_REQUIRED_META = ("id", "name", "domain", "owner_team", "risk")


@dataclass
class Registry:
    paths: Paths
    businesses: dict[str, Business] = field(default_factory=dict)
    divisions: dict[str, Division] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    agents: dict[str, Agent] = field(default_factory=dict)
    works: dict[str, Work] = field(default_factory=dict)
    tool_catalog: Any = None  # gate.ToolCatalog — 순환 임포트 회피용 Any

    # ── 로드 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, root: Path | str | None = None, *, strict: bool = True) -> Registry:
        reg = cls(paths=Paths(root))
        reg._load_tool_catalog()
        reg._load_businesses()
        reg._load_divisions_and_teams()
        reg._load_agents()
        reg._load_works()
        if strict:
            reg.check_integrity()
        return reg

    def _load_tool_catalog(self) -> None:
        from .gate import GateError, ToolCatalog

        path = self.paths.org / "tools.yaml"
        if not path.is_file():
            raise RegistryError(f"도구 카탈로그가 없다: {path}")
        try:
            self.tool_catalog = ToolCatalog.load(path)
        except GateError as exc:
            raise RegistryError(str(exc)) from exc

    def _load_businesses(self) -> None:
        for p in sorted(self.paths.businesses.glob("*.yaml")):
            doc = _load_yaml(p)
            _validate(doc, "business", p)
            bid = doc["id"]
            if bid in self.businesses:
                raise RegistryError(f"{p}: 사업 id 중복 — {bid}")
            if p.stem != bid:
                raise RegistryError(f"{p}: 파일명({p.stem})과 id({bid})가 다르다")
            self.businesses[bid] = Business(doc, p)

    def _load_divisions_and_teams(self) -> None:
        for dp in sorted(self.paths.divisions.glob("*/division.yaml")):
            doc = _load_yaml(dp)
            _validate(doc, "division", dp)
            did = doc["id"]
            if did in self.divisions:
                raise RegistryError(f"{dp}: 본부 id 중복 — {did}")
            if dp.parent.name != did:
                raise RegistryError(f"{dp}: 디렉터리명({dp.parent.name})과 id({did})가 다르다")
            self.divisions[did] = Division(doc, dp)

        for tp in sorted(self.paths.divisions.glob("*/*/team.yaml")):
            doc = _load_yaml(tp)
            _validate(doc, "team", tp)
            tid = doc["id"]
            if tid in self.teams:
                raise RegistryError(f"{tp}: 팀 id 중복 — {tid}")
            self.teams[tid] = Team(doc, tp)

    def _load_agents(self) -> None:
        for ap in sorted(self.paths.agents.glob("*/agent.yaml")):
            doc = _load_yaml(ap)
            _validate(doc, "agent", ap)
            aid = doc["id"]
            if aid in self.agents:
                raise RegistryError(f"{ap}: 에이전트 id 중복 — {aid}")
            if ap.parent.name != aid:
                raise RegistryError(f"{ap}: 디렉터리명({ap.parent.name})과 id({aid})가 다르다")
            self.agents[aid] = Agent(doc, ap)

    def _load_works(self) -> None:
        if not self.paths.work.is_dir():
            return
        for wp in sorted(self.paths.work.glob("*/*_WORK.md")):
            meta, body = parse_frontmatter(wp)
            missing = [k for k in _WORK_REQUIRED_META if k not in meta]
            if missing:
                raise RegistryError(f"{wp}: 프런트매터 필수 항목 누락 — {', '.join(missing)}")
            wid = meta["id"]
            if wid in self.works:
                raise RegistryError(f"{wp}: 업무 id 중복 — {wid}")
            expected_domain = wp.parent.name
            if meta["domain"] != expected_domain:
                raise RegistryError(
                    f"{wp}: domain({meta['domain']}) 이 디렉터리({expected_domain})와 다르다"
                )
            if not wid.startswith(f"{expected_domain}/"):
                raise RegistryError(f"{wp}: id 는 '{expected_domain}/...' 형식이어야 한다 — {wid}")
            self.works[wid] = Work(meta, body, wp)

    # ── 무결성 ──────────────────────────────────────────────────────────
    def check_integrity(self) -> None:
        """참조 무결성. 하나라도 깨지면 RegistryError."""
        errs: list[str] = []

        for b in self.businesses.values():
            for did in b.data["owning_divisions"]:
                if did not in self.divisions:
                    errs.append(f"{b.source.name}: 없는 본부 참조 — {did}")

        for d in self.divisions.values():
            for bid in d.data.get("businesses", []):
                if bid not in self.businesses:
                    errs.append(f"{d.source}: 없는 사업 참조 — {bid}")
            for tid in d.data.get("teams", []):
                if tid not in self.teams:
                    errs.append(f"{d.source}: 없는 팀 참조 — {tid}")
                elif self.teams[tid].division_id != d.id:
                    errs.append(
                        f"{d.source}: 팀 {tid} 의 division({self.teams[tid].division_id}) 불일치"
                    )

        for t in self.teams.values():
            if t.division_id not in self.divisions:
                errs.append(f"{t.source}: 없는 본부 참조 — {t.division_id}")
            elif t.id not in self.divisions[t.division_id].data.get("teams", []):
                errs.append(f"{t.source}: 본부 {t.division_id} 의 teams 목록에 {t.id} 가 없다")
            for aid in t.agent_ids:
                if aid not in self.agents:
                    errs.append(f"{t.source}: 없는 에이전트 참조 — {aid}")
            esc = t.data.get("escalation_to")
            if esc and esc not in self.teams:
                errs.append(f"{t.source}: 없는 에스컬레이션 대상 — {esc}")

        for a in self.agents.values():
            if a.team_id not in self.teams:
                errs.append(f"{a.source}: 없는 팀 참조 — {a.team_id}")
            elif a.id not in self.teams[a.team_id].agent_ids:
                errs.append(f"{a.source}: 팀 {a.team_id} 의 agents 목록에 {a.id} 가 없다")
            for wid in a.work_ids:
                if wid not in self.works:
                    errs.append(f"{a.source}: 없는 업무 참조 — {wid}")

        for w in self.works.values():
            owner = w.meta.get("owner_team")
            if owner and owner not in self.teams:
                errs.append(f"{w.source}: 없는 소유 팀 — {owner}")

        errs.extend(self._check_tools())

        if errs:
            raise RegistryError("참조 무결성 실패:\n" + "\n".join(f"  - {e}" for e in errs))

    def _check_tools(self) -> list[str]:
        """매니페스트·게이트가 카탈로그(org/tools.yaml)에 없는 도구를 쓰는지 검사."""
        cat = self.tool_catalog
        if cat is None:
            return []
        errs: list[str] = []

        for a in self.agents.values():
            unknown = cat.unknown(a.data.get("tools") or [])
            if unknown:
                errs.append(
                    f"{a.source}: 카탈로그에 없는 도구 — {', '.join(unknown)} "
                    f"(org/tools.yaml 에 등록하라)"
                )

        for gp in sorted(self.paths.org.rglob("gate.yaml")):
            doc = _load_yaml(gp)
            for kind in ("allow", "deny"):
                for pattern in (doc.get("tools") or {}).get(kind) or []:
                    if cat.pattern_covers_nothing(pattern):
                        errs.append(
                            f"{gp}: {kind} 패턴 '{pattern}' 이 카탈로그의 어떤 도구도 매치하지 않는다 "
                            f"(오타이거나 죽은 규칙)"
                        )
        return errs

    # ── 조회 ────────────────────────────────────────────────────────────
    def agent(self, agent_id: str) -> Agent:
        try:
            return self.agents[agent_id]
        except KeyError:
            raise RegistryError(
                f"에이전트 없음: {agent_id} (등록됨: {', '.join(sorted(self.agents)) or '없음'})"
            ) from None

    def team_of(self, agent_id: str) -> Team:
        return self.teams[self.agent(agent_id).team_id]

    def division_of(self, agent_id: str) -> Division:
        return self.divisions[self.team_of(agent_id).division_id]

    def businesses_of(self, agent_id: str) -> list[Business]:
        div = self.division_of(agent_id)
        return [self.businesses[b] for b in div.data.get("businesses", []) if b in self.businesses]

    def works_of(self, agent_id: str) -> list[Work]:
        return [self.works[w] for w in self.agent(agent_id).work_ids]

    def active_agents(self) -> Iterator[Agent]:
        return (a for a in self.agents.values() if a.is_active)

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for b in self.businesses.values():
            by_status[b.status] = by_status.get(b.status, 0) + 1
        return {
            "businesses": len(self.businesses),
            "businesses_by_status": by_status,
            "divisions": len(self.divisions),
            "teams": len(self.teams),
            "agents": len(self.agents),
            "agents_active": sum(1 for _ in self.active_agents()),
            "works": len(self.works),
        }
