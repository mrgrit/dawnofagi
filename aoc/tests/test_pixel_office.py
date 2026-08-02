"""픽셀 오피스 — **텔레메트리 바인딩** 검증.

브라우저가 없는 환경에서도 지킬 수 있는 것만 지킨다. 그림이 예쁜지는 못 보지만,
"임의 데이터 아님"은 정적으로 증명할 수 있다:

1. HTML 이 읽는 상태 필드가 `build_state()` 출력에 **전부 실재**하는가.
2. HTML 안에 **에이전트·케이스·run 데이터 리터럴이 없는가** (하드코딩 픽스처 금지).
3. 외부 의존(CDN·npm·폰트)이 없는가 — fresh linux 에 그대로 배포돼야 한다.
4. 서버가 상태 API 와 트레이스 API 를 실제로 준다 (리플레이 가능).
5. **에이전트의 위치가 스팬에서 나오는가** — 사람이 섹터 안에 서 있다는 건
   "그 시각 거기서 실제로 도구를 썼다"는 뜻이어야 한다. 여기가 무너지면
   오피스는 그럴듯한 그림일 뿐 관제가 아니다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from dawn_aoc.collect import TraceLake
from dawn_aoc.console import build_state
from dawn_core.paths import Paths


@pytest.fixture(scope="module")
def root():
    return Paths().root


@pytest.fixture(scope="module")
def html(root):
    p = root / "apps" / "pixel-office" / "index.html"
    if not p.is_file():
        pytest.fail("apps/pixel-office/index.html 이 없다")
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def state(root):
    return build_state(root, limit=50)


# ── 1. 바인딩 ────────────────────────────────────────────────────────────


def test_html_reads_only_existing_state_keys(html, state):
    """`S.state.foo` 가 상태에 없으면 그 화면은 영원히 빈다 — 조용한 거짓말이다."""
    used = set(re.findall(r"\bS\.state\.([A-Za-z_][A-Za-z0-9_]*)", html))
    assert used, "상태를 하나도 안 읽는다면 그건 바인딩이 아니다"
    missing = used - set(state)
    assert not missing, f"상태에 없는 키를 읽는다: {sorted(missing)}"


AGENT_FIELDS = {
    "agent_id", "name", "team", "eg_org", "persona", "zone", "room", "division",
    "division_color", "badge", "hat", "effect", "autonomy", "autonomy_declared",
    "control_state", "credentials_revoked", "blocked_tools", "runs", "complete",
    "tokens", "cases", "last_model", "eg_refs", "last_trace", "busy_ms", "sectors",
}


def test_agent_avatar_fields_all_exist(html, state):
    """아바타 인코딩(몸색·배지·모자·이펙트·EG 아이콘)이 실제 필드에 붙어 있다."""
    assert state["agents"], "에이전트가 없으면 검증할 수 없다"
    have = set(state["agents"][0])
    assert have >= AGENT_FIELDS, f"상태에 없는 아바타 필드: {sorted(AGENT_FIELDS - have)}"
    for f in ("division_color", "badge", "hat", "effect", "eg_refs", "last_trace"):
        assert f"a.{f}" in html, f"아바타가 {f} 를 안 쓴다면 인코딩이 데이터에 안 붙은 것"


def test_zone_fields_come_from_eg(html, state):
    z = state["zones"][0]
    for f in ("pixel_room", "cidr", "sensitivity", "is_gate", "security_level", "assets"):
        assert f in z, f"Zone 상태에 {f} 가 없다"
        assert f"z.{f}" in html, f"플로어 뷰가 z.{f} 를 안 그린다"


def test_three_tier_views_exist(html):
    for fn in ("drawBuilding", "drawFloor", "drawDesk"):
        assert f"function {fn}" in html, f"3계층 뷰 중 {fn} 이 없다"
    assert 'S.view="floor"' in html and 'S.view="desk"' in html, "드릴다운 경로가 없다"


def test_timeline_scrubber_replays_spans(html):
    """타임라인은 /api/trace 로 받은 스팬만 되감는다 — 합성 프레임이 아니다."""
    assert "/api/trace/" in html
    assert "S.spans.filter(s => s.start_ns <= S.clock)" in html, \
        "스크러버가 스팬 타임스탬프로 자르지 않으면 리플레이가 아니다"
    assert "SPEEDS[S.speed]" in html, "재생 속도 설정이 재생 루프에 안 걸려 있다"
    assert "function buildEvents" in html, \
        "눈금이 스팬 시작·끝 시각에서 만들어지지 않으면 임의 시간축이다"


# ── 5. 위치 = 스팬 ───────────────────────────────────────────────────────


OCC_FIELDS = {"agent_id", "trace_id", "zone", "kind", "span", "tool", "asset",
              "gate", "severity", "status", "start_ns", "end_ns"}


def test_occupancy_is_one_row_per_span(state, root):
    """점유 구간은 스팬에서만 온다 — 한 줄이라도 만들어내면 오피스는 거짓이 된다."""
    from dawn_aoc.collect import TraceLake

    occ = state["occupancy"]
    assert occ, "점유 구간이 없으면 아무도 못 움직인다"
    assert set(occ[0]) >= OCC_FIELDS, f"빠진 필드: {sorted(OCC_FIELDS - set(occ[0]))}"

    runs = TraceLake(root).all_runs(limit=50)
    spans = sum(len([s for s in r.spans if s["name"] != "invoke_agent"])
                for r in runs if not r.is_orchestrator)
    assert len(occ) == spans, f"스팬 {spans}개인데 점유 {len(occ)}개 — 지어냈거나 흘렸다"
    for s in occ:
        assert s["end_ns"] >= s["start_ns"]
        assert s["kind"] in ("work", "desk")


def test_work_segments_land_in_the_zone_that_owns_the_asset(state):
    """`work` 구간의 존은 EG `Asset -LOCATED_IN-> Zone` 이 정한 것과 같아야 한다."""
    owner = {a["id"]: z["short"] for z in state["zones"] for a in z["assets"]}
    work = [s for s in state["occupancy"] if s["kind"] == "work"]
    assert work, "자산을 건드린 스팬이 하나도 없다"
    for s in work:
        assert s["asset"] in owner, f"EG 에 없는 자산: {s['asset']}"
        assert owner[s["asset"]] == s["zone"], f"{s['asset']} 는 {owner[s['asset']]} 에 있다"


def test_idle_means_no_span_not_an_empty_desk(html, state):
    """대기실은 '스팬이 없다'는 상태 자체다 — 그리려면 그 규칙이 코드에 있어야 한다."""
    assert state["floorplan"]["lounge"]["id"] == "lounge"
    assert "loungeSlot" in html and "segAt" in html
    assert "if(!seg) return Object.assign(loungeSlot(a)" in html, \
        "스팬이 없을 때 대기실로 보내는 분기가 없다"


def test_every_zone_gets_a_room_on_every_floor(state):
    """존을 빼면 화면에서 사라져 '없는 구역'처럼 보인다 — 안 쓴다는 사실이 봐야 할 정보다."""
    fp = state["floorplan"]
    assert fp["floors"], "층이 없다"
    rooms = {z["short"] for z in state["zones"] if not z["is_gate"]}
    for f in fp["floors"]:
        got = {z["short"] for z in f["sectors"]}
        assert got == rooms, f"{f['name']} 에 빠진 존: {sorted(rooms - got)}"
        for z in f["sectors"]:
            assert not z["is_gate"], "pipe 는 방이 아니라 문이다"
            assert isinstance(z["used"], bool), "쓰는 존인지 표시가 없다"
            assert z["used"] or (not z["entries"] and not z["homed"]), \
                f"{f['name']}/{z['short']} 이 미사용인데 진입·상주 기록이 있다"
        ranks = [int(str(z["security_level"]).split("L")[-1]) for z in f["sectors"]]
        assert ranks == sorted(ranks), f"{f['name']} 섹터가 리스크 순이 아니다"
    assert [g["short"] for g in fp["gates"]] == ["pipe"]


def test_sector_work_separates_telemetry_from_declaration(state):
    """방의 '업무'는 선언, '도구·사람'은 텔레메트리다 — 섞으면 어느 쪽이 사실인지 모른다."""
    for f in state["floorplan"]["floors"]:
        ids = set(f["agents"])
        for z in f["sectors"]:
            segs = [s for s in state["occupancy"]
                    if s["zone"] == z["short"] and s["agent_id"] in ids]
            work = [s for s in segs if s["kind"] == "work"]
            desk = [s for s in segs if s["kind"] == "desk"]
            assert z["entries"] == len(work), f"{f['name']}/{z['short']} 진입 수가 안 맞는다"
            assert z["desk_spans"] == len(desk)
            assert sum(t["calls"] for t in z["tools"]) == \
                sum(1 for s in work if s["tool"]), f"{f['name']}/{z['short']} 방 도구 집계"
            assert sum(t["calls"] for t in z["desk_tools"]) == \
                sum(1 for s in desk if s["tool"]), f"{f['name']}/{z['short']} 자리 도구 집계"
            assert {v["agent_id"] for v in z["visitors"]} == {s["agent_id"] for s in work}
            # 업무는 들어온·상주 에이전트가 **선언한** SOP 여야 한다 (지어내지 않는다)
            declared = {w for a in state["agents"] if a["agent_id"] in ids
                        for w in a["authority"]["works"]}
            assert set(z["works"]) <= declared, f"{z['short']} 에 없는 업무가 붙었다"


def test_desk_spans_are_never_counted_as_room_entries(state):
    """자기 자리 = 자산을 안 건드린 스팬이다. 진입으로 세면 없던 일이 생긴다."""
    seen_desk_only = False
    for f in state["floorplan"]["floors"]:
        for z in f["sectors"]:
            if z["desk_spans"] and not z["entries"]:
                seen_desk_only = True
                assert not z["visitors"], f"{z['short']}: 아무도 안 들어왔는데 방문자가 있다"
                assert not z["tools"], f"{z['short']}: 진입 0 인데 방 안 도구가 있다"
    assert seen_desk_only, "자기 자리 스팬만 있는 존이 없다 — 이 검증이 무의미하다"


AUTH_FIELDS = {"allow", "deny", "effective", "declared", "autonomy", "hitl_require_on",
               "budget", "model_policy", "sources", "layers", "works", "role"}


def test_authority_is_the_compiled_gate_not_prose(state, root):
    """권한 표시는 **컴파일된 실효 경계**여야 한다 — 문서에서 유추하면 아무도 안 본다."""
    from dawn_core import Registry
    from dawn_core.control_plane import compile_agent

    reg = Registry.load(root)
    for a in state["agents"]:
        au = a["authority"]
        assert not au.get("error"), f"{a['agent_id']} 권한 컴파일 실패: {au.get('error')}"
        assert set(au) >= AUTH_FIELDS, f"빠진 필드: {sorted(AUTH_FIELDS - set(au))}"
        want = compile_agent(reg, a["agent_id"])
        gate = want.gate.to_dict(want.declared_tools)
        assert au["effective"] == gate["tools"]["effective"]
        assert au["deny"] == gate["tools"]["deny_patterns"]
        assert au["autonomy"] == gate["autonomy"]
        # L1~L4 가 다 있어야 통제 평면이 온전히 겹쳐진 것이다 (L3 은 수행 업무 수만큼)
        levels = [ly["level"] for ly in au["layers"]]
        assert au["works"], f"{a['agent_id']} 에 L3 업무가 없다"
        assert levels == ["L1", "L2"] + ["L3"] * len(au["works"]) + ["L4"], \
            f"{a['agent_id']} 계층: {levels}"
        # 실효 도구는 allow 안 · deny 밖이어야 한다 (단조 축소의 결과)
        assert set(au["effective"]) <= set(au["declared"])


def test_floor_work_counts_come_from_telemetry(state):
    """층의 업무 집계가 점유(=스팬)와 어긋나면 화면 숫자를 믿을 수 없다."""
    for f in state["floorplan"]["floors"]:
        ids = set(f["agents"])
        calls = sum(1 for s in state["occupancy"] if s["agent_id"] in ids and s["tool"])
        assert sum(t["calls"] for t in f["work"]["tools"]) == calls, f["name"]
        for t in f["work"]["tools"]:
            assert sum(t["gate"].values()) <= t["calls"]
        # 차단 목록과 게이트 판정이 어긋나면 둘 중 하나는 거짓이다
        blocked_by_gate = {t["tool"] for t in f["work"]["tools"] if t["gate"].get("block")}
        assert set(f["work"]["blocked"]) == blocked_by_gate, f["name"]
        # 권한 카드는 그 층 인원 수와 같아야 한다 — 빠지면 무권한처럼 보인다
        assert {x["agent_id"] for x in f["authority"]["agents"]} == ids, f["name"]


def test_blocked_tools_are_actually_denied_in_the_gate(state):
    """게이트가 막은 도구는 권한에도 없어야 한다 — 아니면 통제 평면이 새는 것이다."""
    import fnmatch

    for f in state["floorplan"]["floors"]:
        for tool in f["work"]["blocked"]:
            for a in f["authority"]["agents"]:
                assert tool not in a["effective"], f"{a['agent_id']} 는 {tool} 이 실효 도구인데 차단됐다"
                assert any(fnmatch.fnmatchcase(tool, p) for p in a["deny"]), \
                    f"{tool} 이 {a['agent_id']} 의 deny 패턴에 없다"


def test_uniform_and_face_are_deterministic_encodings(html):
    """같은 에이전트는 늘 같은 얼굴이어야 한다 — 난수를 쓰면 신원 표시가 아니다."""
    assert "function hash32" in html, "id → 외모 결정론 해시가 없다"
    assert "Math.random" not in html
    for field in ("division_color", "a.hat", "a.badge", "a.team"):
        assert field in html, f"유니폼이 {field} 에 안 붙어 있다"


# ── 2. 하드코딩 픽스처 금지 ──────────────────────────────────────────────


def test_no_synthetic_agent_or_case_data(html, state):
    """데모용 가짜 데이터가 섞이면 대시보드 전체가 못 믿을 것이 된다."""
    body = html.split("<script>", 1)[-1]
    for name in (a["agent_id"] for a in state["agents"]):
        assert name not in body, f"에이전트 id '{name}' 가 HTML 에 박혀 있다"
    for pat in (r"const\s+AGENTS\s*=", r"const\s+RUNS\s*=", r"const\s+CASES\s*=",
                r"const\s+DEMO\s*=", r"Math\.random\s*\("):
        assert not re.search(pat, body), f"합성 데이터 흔적: {pat}"


def test_only_encoding_tables_are_hardcoded(html):
    """상수로 둬도 되는 건 **인코딩 표**뿐 (색↔등급 매핑). 데이터는 아니다."""
    body = html.split("<script>", 1)[-1]
    consts = set(re.findall(r"const\s+([A-Z][A-Z0-9_]*)\s*=", body))
    assert consts <= {"G", "ZONE_TINT", "SEV_COLOR", "EFFECT", "HAT", "MODEL_C",
                      "IRR_C", "GATE_C", "KIND_FURN", "HAIR", "SKIN", "ACCENT",
                      "KIND_LABEL", "SPEEDS", "SPEED_NAME", "S"}, \
        f"인코딩 표가 아닌 상수: {sorted(consts)}"
    # S 는 런타임 홀더다 — **비어서 시작해야** 한다. 초기값에 데이터가 있으면 픽스처다.
    m = re.search(r"const\s+S\s*=\s*\{(.*?)\};", body, re.S)
    assert m and "state:null" in m.group(1).replace(" ", ""), \
        "S.state 는 null 로 시작해야 한다 — 초기값이 있으면 서버 없이도 그림이 나온다"


# ── 3. 자립성 ────────────────────────────────────────────────────────────


def test_no_external_dependencies(html):
    """fresh linux 에 그대로 떨어져도 떠야 한다 — CDN·npm·외부 폰트 금지."""
    for pat in ("http://", "https://", "cdn.", "unpkg", "jsdelivr", "googleapis",
                "@import", "import "):
        assert pat not in html, f"외부 의존 발견: {pat}"
    assert "<script src=" not in html and "<link rel=\"stylesheet\"" not in html


def test_single_file_app(root):
    files = [p.name for p in (root / "apps" / "pixel-office").iterdir() if p.is_file()]
    assert files == ["index.html"], f"파일 하나여야 한다: {files}"


def test_balanced_braces_in_script(html):
    """파서는 없지만 최소한 블록은 맞아야 한다."""
    body = html.split("<script>", 1)[-1].split("</script>", 1)[0]
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    stripped = re.sub(r"^\s*//.*$", "", stripped, flags=re.M)
    for op, cl in (("{", "}"), ("(", ")"), ("[", "]")):
        assert stripped.count(op) == stripped.count(cl), f"{op}{cl} 짝이 안 맞는다"


# ── 4. 서버 ──────────────────────────────────────────────────────────────


# ── 6. 헤드리스 실행 — 브라우저 없이 진짜로 돌려 본다 ────────────────────
#
# 정적 검사만으로는 "문법은 맞는데 첫 프레임에서 죽는" 콘솔을 못 잡는다.
# quickjs 가 있으면 DOM·캔버스를 최소로 흉내 내 draw() 까지 돌리고, 사람이 어느
# 섹터에 서 있는지 좌표로 확인한다. 없으면 skip — 배포에 필요한 의존이 아니다.


@pytest.fixture(scope="module")
def headless(root, state):
    quickjs = pytest.importorskip(
        "quickjs", reason="quickjs 없음 — `pip install quickjs` 로 헤드리스 검증 활성화")
    harness = (root / "aoc" / "tests" / "office_harness.js").read_text(encoding="utf-8")
    body = (root / "apps" / "pixel-office" / "index.html").read_text(
        encoding="utf-8").split("<script>", 1)[1].split("</script>", 1)[0]

    tid = next((a["last_trace"] for a in state["agents"] if a["last_trace"]), "")
    spans = sorted(TraceLake(root).spans(tid), key=lambda s: s["start_ns"]) if tid else []

    src = (harness.replace("__STATE__", json.dumps(state, ensure_ascii=False))
                  .replace("__TRACE__", json.dumps(spans, ensure_ascii=False))
                  .replace("__SCRIPT__", body))
    ctx = quickjs.Context()
    ctx.set_memory_limit(512 * 1024 * 1024)
    ctx.set_time_limit(-1)
    ctx.eval(src)
    for _ in range(2000):                       # fetch 프라미스를 끝까지 돌린다
        if not ctx.execute_pending_job():
            break
    return json.loads(ctx.eval("runChecks()"))


def test_console_actually_runs_headless(headless):
    assert not headless["errors"], headless["errors"]
    for view, calls in headless["views"].items():
        assert calls > 50, f"{view} 뷰가 사실상 아무것도 안 그렸다 ({calls} 콜)"
    assert not headless["calls"]["warn"], f"NaN 좌표: {headless['calls']['warn'][:5]}"


def test_everyone_stands_where_their_spans_say(headless):
    """사람이 섹터 안에 서 있다 = 그 시각 거기서 실제로 도구를 썼다."""
    bad = [(when, r) for when, rows in headless["placements"].items()
           for r in rows if not r["ok"]]
    assert not bad, f"스팬과 다른 자리에 서 있다: {bad[:3]}"
    before = headless["placements"]["before"]
    assert all(r["place"] == "lounge" for r in before), "기록 구간 이전인데 근무 중인 사람이 있다"


def test_agents_walk_into_the_sector_and_idle_ones_do_not_twitch(headless):
    """움직임은 텔레메트리 전이의 보간일 뿐 — 장식 애니메이션이 아니다."""
    w = headless["walk"]
    assert w["moved"] > 1, "작업이 바뀌었는데 아무도 움직이지 않았다"
    assert w["arrived"] < 0.05, f"목표 섹터에 도착하지 못했다 (남은 거리 {w['arrived']})"
    assert w["inSector"], f"{w['zone']} 섹터 안으로 들어가지 못했다"
    assert w["awayFromDesk"] > 1, "근무 자리가 자기 자리와 같으면 '섹터 진입'이 아니다"
    assert w["idleDrift"] == 0, "대기 중인 에이전트가 흔들렸다 — 장식 애니메이션이다"


def test_floor_view_draws_only_that_floor(headless, state):
    """층을 고르면 그 층만 나와야 한다 — 흐리게라도 남아 있으면 클릭·판독을 방해한다."""
    lv = headless["levels"]
    assert len(lv["building"]) == len(state["floorplan"]["floors"]), "빌딩 뷰가 전 층을 안 그린다"
    assert len(set(lv["floor"])) == 1, f"층 뷰가 {sorted(set(lv['floor']))} 를 그린다"
    assert len(set(lv["desk"])) == 1, "데스크 뷰도 그 층만 배경으로 써야 한다"
    assert headless["views"]["floor"] < headless["views"]["building"], \
        "층 하나가 사옥 전체보다 무겁다면 다른 층까지 그리고 있는 것이다"


def test_clicking_a_room_or_team_actually_reaches_it(headless):
    """바닥이 방·팀 위를 덮으면 눌러도 아무 일이 안 일어난다 — 눈으로는 못 잡는 종류다."""
    c = headless["clicks"]
    for kind in ("sector", "team"):
        for row in c[kind]:
            assert row["found"], f"{kind} '{row['needle']}' 에 클릭 영역이 아예 없다"
            assert row["hit"], \
                f"{kind} '{row['needle']}' 를 눌렀는데 '{row['got']}' 가 잡힌다"
    assert c["lounge"]["found"] and c["lounge"]["hit"], "휴게실을 클릭할 수 없다"
    assert c["entered"] == {"view": "room", "kind": "sector",
                            "id": c["entered"]["id"]}, c["entered"]
    assert c["entered"]["id"], "방을 눌렀는데 룸 뷰로 안 들어간다"


def test_room_view_shows_one_room_only(headless):
    """방/부서 안으로 들어가면 층 평면은 사라지고 그 방만 남아야 한다."""
    lv, views = headless["levels"], headless["views"]
    for v in ("room", "roomTeam"):
        assert lv[v] == [], f"{v} 뷰가 층 평면({lv[v]})을 아직 그린다"
        assert views[v] > 60, f"{v} 뷰가 사실상 비었다 ({views[v]} 콜)"


def test_floor_panel_shows_work_and_authority(headless):
    """층 상세는 '무엇이 돌았나'와 '무엇을 할 수 있나'를 같이 보여줘야 한다."""
    for kind, got in headless["panel"].items():
        assert got["len"] > 1500, f"{kind} 선택 시 상세가 거의 비었다"
        assert len(got["has"]) == 4, f"{kind}: 빠진 절 — {got['has']}"


def test_roster_and_hit_targets_exist(headless, state):
    assert headless["roster"] == len(state["agents"]), "명부 초상이 인원 수와 다르다"
    assert headless["hits"]["people"] == len(state["agents"]), "클릭할 수 없는 사람이 있다"
    assert headless["hits"]["total"] > headless["hits"]["people"], "섹터를 클릭할 수 없다"


@pytest.fixture(scope="module")
def server(root):
    port = 8899
    proc = subprocess.Popen(
        [sys.executable, "-m", "dawn_aoc.cli", "serve", "--port", str(port)],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=2).read()
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("서버가 뜨지 않았다")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def test_server_serves_page_and_state(server):
    page = urllib.request.urlopen(server + "/", timeout=5).read().decode()
    assert "픽셀 오피스" in page
    st = json.loads(urllib.request.urlopen(server + "/api/state", timeout=15).read())
    assert st["agents"] and st["divisions"] and st["kpis"]


def test_server_serves_trace_for_replay(server, root):
    tids = TraceLake(root).trace_ids()
    if not tids:
        pytest.skip("트레이스 없음")
    spans = json.loads(urllib.request.urlopen(
        f"{server}/api/trace/{tids[0]}", timeout=10).read())
    assert spans and spans[0]["name"] == "invoke_agent"
    assert [s["start_ns"] for s in spans] == sorted(s["start_ns"] for s in spans)
