"""자동 편성 — **게이트가 경계다, 모델의 의견이 아니다.**

여기서 지키는 것 하나: 모델이 무엇을 제안하든 팀 게이트 밖의 권한은 만들어지지
않는다. 편성은 권한을 만드는 행위라, 초안이 그럴듯하다고 그대로 집행하면
통제 평면이 모델의 판단에 종속된다.
"""

from __future__ import annotations

import pytest

from dawn_core import hire
from dawn_core.paths import Paths


@pytest.fixture()
def root():
    return Paths().root


# ── 팀 고르기 ────────────────────────────────────────────────────────────
def test_규칙_있는_팀만_고른다(root):
    """AGENT_TEAM.md(L2) 가 없는 팀에는 사람을 넣지 않는다."""
    team = hire.pick_team(root, "ax")
    assert team.startswith("ax-")


def test_없는_본부는_거부한다(root):
    with pytest.raises(hire.HireError):
        hire.pick_team(root, "없는본부")


# ── 도구 경계 ────────────────────────────────────────────────────────────
def test_절대_안_주는_도구는_목록에도_없다(root):
    """게이트가 이미 막지만, 목록에 떠 있으면 사람이 실수로 켠다."""
    allowed = hire.allowed_tools(root, hire.pick_team(root, "ax"))
    assert allowed and not (allowed & hire.NEVER)
    assert "sys.rm_rf_root" not in allowed
    assert "ctl.modify_gate" not in allowed


def test_초안을_손으로_고쳐도_게이트가_이긴다(root, tmp_path):
    """사람이 파일에 도구를 더 적어 넣어도 주어지지 않는다.

    초안은 파일이라 편집된다. 파일을 믿으면 경계가 파일로 옮겨 간다.
    """
    team = hire.pick_team(root, "ax")
    d = hire.Draft(
        order_id=901, title="t", division="ax", team=team,
        members=[{"role_key": "x", "name": "X", "mission": "m", "lead": True,
                  # 손으로 끼워 넣은 것들 — 팀 게이트 밖이다
                  "tools": ["eg.search", "sys.rm_rf_root", "pay.execute",
                            "ctl.modify_gate"],
                  "phase": "P1", "depends_on": [], "works": []}])
    members = hire.to_members(root, d)
    assert members[0].tools == ["eg.search"]


def test_도구가_하나도_안_남으면_최소한만_준다(root):
    team = hire.pick_team(root, "ax")
    d = hire.Draft(order_id=902, division="ax", team=team,
                   members=[{"role_key": "y", "tools": ["sys.mkfs"]}])
    assert hire.to_members(root, d)[0].tools == ["eg.search", "eg.record"]


# ── 팀장 ────────────────────────────────────────────────────────────────
def test_팀장은_한_명뿐이다():
    """둘 이상이면 '게이트 안은 auto' 가 누구 게이트인지 모호해진다."""
    d = hire.Draft(order_id=903, members=[
        {"role_key": "a", "lead": True}, {"role_key": "b", "lead": True}])
    assert d.lead["role_key"] == "a"


def test_팀장이_없을_수도_있다():
    d = hire.Draft(order_id=904, members=[{"role_key": "a"}])
    assert d.lead is None


# ── 초안 파일 ───────────────────────────────────────────────────────────
def test_저장하고_읽으면_같다(root, tmp_path, monkeypatch):
    """초안은 파일이 곧 객체다 — 왕복해서 잃는 게 없어야 사람이 고칠 수 있다."""
    monkeypatch.setattr(hire, "draft_path",
                        lambda _r, oid: tmp_path / f"wo{oid}.yaml")
    d = hire.Draft(order_id=905, title="제목", division="ax", team="ax-university",
                   members=[{"role_key": "a", "name": "가", "lead": True,
                             "tools": ["eg.search"], "dropped_tools": ["pay.execute"]}])
    p = hire.save(root, d)
    assert "본부장이 이 파일을 고치고 승인해야" in p.read_text(encoding="utf-8")

    back = hire.load(root, 905)
    assert back.order_id == 905
    assert back.title == "제목"
    assert back.lead["role_key"] == "a"
    # 뺏은 도구 기록이 살아남아야 한다 — 그게 보여야 권한 재검토가 일어난다
    assert back.members[0]["dropped_tools"] == ["pay.execute"]


def test_초안이_없으면_읽기가_실패한다(root):
    with pytest.raises(hire.HireError):
        hire.load(root, 999999)


# ── 모델 출력 파싱 ──────────────────────────────────────────────────────
def test_코드펜스로_감싸_와도_읽는다():
    txt = '설명\n```json\n{"members": [{"role_key": "a"}]}\n```\n끝'
    assert hire._parse(txt)["members"][0]["role_key"] == "a"


def test_JSON_이_아니면_거부한다():
    with pytest.raises(hire.HireError):
        hire._parse("그냥 문장입니다")


# ── 승인 ────────────────────────────────────────────────────────────────
def test_승인자_없이는_권한을_만들지_않는다(root, monkeypatch):
    monkeypatch.setattr(hire, "load", lambda *a, **k: hire.Draft(order_id=906))
    with pytest.raises(hire.HireError):
        hire.approve(root, 906, by="", approved=True)
