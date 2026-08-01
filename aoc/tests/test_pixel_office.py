"""픽셀 오피스 — **텔레메트리 바인딩** 검증.

브라우저가 없는 환경에서도 지킬 수 있는 것만 지킨다. 그림이 예쁜지는 못 보지만,
"임의 데이터 아님"은 정적으로 증명할 수 있다:

1. HTML 이 읽는 상태 필드가 `build_state()` 출력에 **전부 실재**하는가.
2. HTML 안에 **에이전트·케이스·run 데이터 리터럴이 없는가** (하드코딩 픽스처 금지).
3. 외부 의존(CDN·npm·폰트)이 없는가 — fresh linux 에 그대로 배포돼야 한다.
4. 서버가 상태 API 와 트레이스 API 를 실제로 준다 (리플레이 가능).
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
    "tokens", "cases", "last_model", "eg_refs", "last_trace",
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
    assert "S.spans.slice(0, S.tl)" in html, "스크러버가 스팬을 자르지 않으면 리플레이가 아니다"
    assert "renderTimeline" in html


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
    assert consts <= {"ZONE_TINT", "SEV_COLOR", "EFFECT", "S"}, \
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
