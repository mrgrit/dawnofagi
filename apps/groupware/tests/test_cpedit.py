"""통제 평면 웹 조정 — 여기가 경계다.

`gate.yaml` 을 웹에서 고칠 수 있다는 것은 곧 **경계를 넓힐 수 있다는 뜻**이다.
그래서 이 파일이 지키는 것은 UI 가 아니라 **무엇이 저장되지 않는가**다:

* 경계를 넓히면 저장 안 된다 (단조 축소 — 컴파일러가 잡는다)
* 목록에 없는 경로는 못 만진다 (임의 파일 쓰기 방지)
* 시크릿은 안 들어간다 (웹 입력은 git 을 안 거쳐 gitleaks 가 안 돈다)
* 사유 없이 안 바뀐다
* 실패하면 **파일이 그대로다**
"""

from __future__ import annotations

import contextlib

import pytest
from dawn_core.paths import Paths
from dawn_groupware import cpedit
from dawn_groupware.cpedit import CPEditError

ROOT = Paths().root


@pytest.fixture
def restore():
    """실물 `org/` 를 건드린다 — 끝나면 반드시 되돌린다."""
    saved: dict[tuple[str, str], str] = {}

    def keep(kind: str, ident: str) -> str:
        text = cpedit.read(ROOT, kind, ident)
        saved.setdefault((kind, ident), text)
        return text

    yield keep
    for (kind, ident), text in saved.items():
        cpedit.resolve(ROOT, kind, ident).path.write_text(text, encoding="utf-8")


# ── 무엇을 만질 수 있나 ──────────────────────────────────────────────────


def test_targets_come_from_the_registry():
    """목록을 따로 적으면 팀이 늘 때 두 곳을 고쳐야 하고, 빠뜨리면 안 보인다."""
    from dawn_core import Registry

    reg = Registry.load(ROOT)
    ts = cpedit.targets(ROOT)
    assert {t.id for t in ts if t.kind == "team"} == set(reg.teams)
    assert {t.id for t in ts if t.kind == "soul"} == set(reg.agents)
    assert {t.id for t in ts if t.kind == "work"} == set(reg.works)


def test_l2_gaps_are_visible_as_creatable():
    """L2 없는 팀이 목록에 **없으면** 웹에서 채울 방법이 없다 (TODO T10)."""
    gaps = [t for t in cpedit.targets(ROOT) if t.kind == "team" and not t.exists]
    assert gaps, "L2 공백이 없다면 이 검사는 무의미하다"
    assert all(t.path.name == "AGENT_TEAM.md" for t in gaps)


@pytest.mark.parametrize("kind,ident", [
    ("soul", "../../../etc/passwd"),
    ("../../etc", "passwd"),
    ("team", "없는팀"),
    ("secret", "x"),
])
def test_arbitrary_paths_are_refused(kind, ident):
    """경로를 직접 받으면 이 화면이 곧 원격 파일 쓰기가 된다."""
    with pytest.raises(CPEditError):
        cpedit.resolve(ROOT, kind, ident)


# ── 경계를 넓힐 수 없다 ──────────────────────────────────────────────────


def test_widening_a_gate_is_rejected_and_rolled_back(restore):
    """**이 파일에서 가장 중요한 검사.** 웹에서 게이트를 고칠 수 있다는 것은
    경계를 넓힐 수 있다는 뜻이고, 그걸 막는 것은 화면이 아니라 컴파일러여야 한다."""
    before = restore("gate", "ax-university")
    widened = before.replace("  allow:", "  allow:\n    - pay.execute")
    with pytest.raises(CPEditError):
        cpedit.save(ROOT, "gate", "ax-university", widened,
                    actor="t", reason="넓히기 시도")
    assert cpedit.read(ROOT, "gate", "ax-university") == before, "파일이 바뀐 채 남았다"


def test_broken_yaml_is_rejected_and_rolled_back(restore):
    before = restore("gate", "ax-university")
    with pytest.raises(CPEditError):
        cpedit.save(ROOT, "gate", "ax-university", "tools: [[[\n  broken",
                    actor="t", reason="깨진 yaml")
    assert cpedit.read(ROOT, "gate", "ax-university") == before


def test_narrowing_a_gate_is_allowed(restore):
    """좁히는 것은 통과해야 한다 — 안 그러면 통제를 조일 수가 없다."""
    before = restore("gate", "ax-university")
    narrowed = before.replace("max_steps: 40", "max_steps: 20")
    assert narrowed != before, "테스트가 대상을 못 찾았다"
    res = cpedit.save(ROOT, "gate", "ax-university", narrowed,
                      actor="t", reason="예산 축소")
    assert res.ok and "컴파일" in res.validation


def test_narrowing_that_orphans_an_agent_is_refused(restore):
    """게이트를 좁혀 **이미 있는 에이전트가 경계 밖**이 되면 저장하지 않는다.
    좁히는 것도 무조건 허용이 아니다 — 기동 못 하는 에이전트를 남기면 안 된다."""
    before = restore("gate", "ax-university")
    orphaning = before.replace("    - net.fetch\n", "")   # 에이전트가 선언한 도구
    with pytest.raises(CPEditError, match=r"도구 위반|컴파일"):
        cpedit.save(ROOT, "gate", "ax-university", orphaning,
                    actor="t", reason="에이전트를 고아로")
    assert cpedit.read(ROOT, "gate", "ax-university") == before


# ── 시크릿 ───────────────────────────────────────────────────────────────


# 가짜 키를 **조립해서** 만든다. 문자열 그대로 두면 이 파일 자체가 pre-commit
# gitleaks 에 걸려 커밋이 안 된다 (실측 — 훅이 제 일을 했다).
_FAKE = [
    "sk-" + "ant-api03-" + "A" * 16,
    "ghp" + "_" + "B" * 30,
    "api_key = '" + "AKIA" + "C" * 16 + "'",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("bad", _FAKE)
def test_secrets_are_refused(bad):
    """웹 입력은 git 을 안 거친다 — pre-commit 의 gitleaks 가 안 돈다.
    여기서 안 막으면 붙여넣은 키가 그대로 저장된다."""
    assert cpedit.scan_secrets(ROOT, f"# 문서\n\n{bad}\n")


def test_ordinary_text_passes():
    assert cpedit.scan_secrets(ROOT, "# SOUL.md\n\n나는 무엇을 하지 않는가.\n") == ""


def test_secret_never_reaches_the_file(restore):
    before = restore("soul", "ax-univ-diag-01")
    with pytest.raises(CPEditError, match="시크릿"):
        cpedit.save(ROOT, "soul", "ax-univ-diag-01",
                    before + "\n" + _FAKE[0] + "\n", actor="t", reason="사고")
    assert cpedit.read(ROOT, "soul", "ax-univ-diag-01") == before


# ── 사유 ─────────────────────────────────────────────────────────────────


def test_reason_is_required(restore):
    """통제 평면 변경은 에이전트의 행동을 바꾼다. 왜 바꾸는지 없이 반영하지 않는다."""
    before = restore("soul", "ax-univ-diag-01")
    with pytest.raises(CPEditError, match="사유"):
        cpedit.save(ROOT, "soul", "ax-univ-diag-01", before + "\n한 줄\n",
                    actor="t", reason="   ")


def test_editing_a_doc_succeeds_and_keeps_a_snapshot(restore):
    before = restore("soul", "ax-univ-diag-01")
    res = cpedit.save(ROOT, "soul", "ax-univ-diag-01", before + "\n## 추가\n",
                      actor="t", reason="테스트")
    assert res.ok and res.snapshot and res.diff
    assert (ROOT / res.snapshot).read_text(encoding="utf-8") == before, \
        "되돌릴 수 없으면 아무도 안 고친다"


# ── 에이전트 추가·삭제 ───────────────────────────────────────────────────


@pytest.fixture
def scratch_agent():
    yield "test-cpedit-01"
    with contextlib.suppress(CPEditError):          # 이미 지워졌으면 그만이다
        cpedit.delete_agent(ROOT, "test-cpedit-01", actor="t", reason="정리")


def test_create_then_delete_leaves_no_trace(scratch_agent):
    """양방향 참조 — 만들 때 명부에 넣고 지울 때 뺀다. 안 그러면 무결성이 깨진다."""
    from dawn_core import Registry

    res = cpedit.create_agent(
        ROOT, agent_id=scratch_agent, team="corp-cs", name="[테스트] 조정",
        persona="corporate", works=["corporate/crm-inquiry"],
        tools=["eg.search", "eg.record", "doc.search"], zone="dmz",
        autonomy="A1", actor="t", reason="테스트")
    assert res.ok
    reg = Registry.load(ROOT)
    assert scratch_agent in reg.agents
    assert scratch_agent in (reg.teams["corp-cs"].data.get("agents") or [])

    cpedit.delete_agent(ROOT, scratch_agent, actor="t", reason="정리")
    reg2 = Registry.load(ROOT)
    assert scratch_agent not in reg2.agents
    reg2.check_integrity()


def test_create_refuses_a_team_without_l2(scratch_agent):
    """규칙 없이 일하는 팀에 사람을 넣지 않는다 (crew.py 와 같은 판단)."""
    from dawn_core import Registry

    reg = Registry.load(ROOT)
    dormant = next(t.id for t in reg.teams.values()
                   if not (t.dir / "AGENT_TEAM.md").is_file())
    with pytest.raises(CPEditError, match=r"AGENT_TEAM\.md"):
        cpedit.create_agent(ROOT, agent_id=scratch_agent, team=dormant, name="x",
                            persona="", works=[], tools=["eg.search"], zone="dmz",
                            autonomy="A1", actor="t", reason="테스트")


def test_create_refuses_tools_outside_the_team_gate(scratch_agent):
    """웹에서 만들어도 팀 경계를 못 넘는다 — 컴파일이 잡고 되돌린다."""
    with pytest.raises(CPEditError):
        cpedit.create_agent(
            ROOT, agent_id=scratch_agent, team="corp-cs", name="x", persona="corporate",
            works=["corporate/crm-inquiry"], tools=["eg.search", "pay.execute"],
            zone="dmz", autonomy="A1", actor="t", reason="테스트")
    from dawn_core import Registry

    assert scratch_agent not in Registry.load(ROOT).agents, "실패했는데 남았다"


def test_create_refuses_unknown_work(scratch_agent):
    with pytest.raises(CPEditError, match="없는 업무 SOP"):
        cpedit.create_agent(ROOT, agent_id=scratch_agent, team="corp-cs", name="x",
                            persona="corporate", works=["nope/none"],
                            tools=["eg.search"], zone="dmz", autonomy="A1",
                            actor="t", reason="테스트")


def test_create_refuses_a_bad_id(scratch_agent):
    with pytest.raises(CPEditError, match="형식"):
        cpedit.create_agent(ROOT, agent_id="../evil", team="corp-cs", name="x",
                            persona="corporate", works=[], tools=["eg.search"],
                            zone="dmz", autonomy="A1", actor="t", reason="테스트")


def test_work_order_agents_are_not_deletable_here():
    """작업 지시에 딸린 임시 에이전트는 마감으로 회수한다 — 여기서 지우면
    작업 지시와 편성이 어긋난다."""
    from dawn_core.crew import Member, disband, form

    made = form(ROOT, order_id=9905, members=[Member(
        role_key="tmp", name="[테스트]", team="corp-cs", persona="corporate",
        works=["corporate/crm-inquiry"], tools=["eg.search", "eg.record"],
        zone="dmz")], approved=True)
    try:
        with pytest.raises(CPEditError, match="임시 에이전트"):
            cpedit.delete_agent(ROOT, made[0], actor="t", reason="지워보기")
    finally:
        disband(ROOT, order_id=9905)
