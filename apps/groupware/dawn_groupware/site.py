"""공개 홈페이지 — L0. **내부 데이터에 손이 닿지 않는다.**

이 모듈은 `store`(업무 DB)·`egedit`(EG 조정)·`auth`(계정)를 **임포트하지 않는다.**
공개 프로세스가 내부 자산에 도달할 코드 경로가 아예 없어야 dmz 앞단에 놓을 수 있다.
읽는 것은 `org/` 레지스트리(YAML)와 헌장 요약뿐이고, 쓰는 것은 문의 접수 파일 하나다.

테스트(`test_site_cannot_reach_internals`)가 이 격리를 고정한다.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dawn_core import jsonl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from .render import (
    Safe,
    a,
    div,
    h,
    h1,
    h2,
    h3,
    input_,
    join,
    li,
    navbar,
    p,
    page,
    small,
    span,
    strong,
    textarea,
    ul,
)

MISSION = "AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다."

PRINCIPLES = [
    ("에이전트가 돌린다", "관제·업무의 기본 수행자는 에이전트다. 사람 개입은 최소화한다."),
    ("사람의 개입 = EG 조정", "코드·프롬프트를 직접 고치지 않는다. 회사의 뇌(Experience Graph)를 "
                          "수정하면 다음 작업부터 전 에이전트에 전파된다."),
    ("우리가 먼저 AX 된다", "모든 제품은 자사 운영에서 먼저 검증한다. 자사 = 테넌트 #0 = 레퍼런스."),
    ("보안 위에 짓는다", "4-tier 세그먼트 보안 인프라 위에 구축해 어떤 고객사든 테넌트로 붙인다."),
    ("인시던트를 통합한다", "보안 침해뿐 아니라 품질·정합성 실패(할루시네이션·포기·요구미달)도 "
                        "인시던트로 관제한다."),
]

AUTONOMY = [
    ("A0", "전건 인간 승인"),
    ("A1", "에이전트 제안 + 원클릭 승인"),
    ("A2", "자율 실행 + 샘플 검토"),
    ("A3", "완전 자율 (비가역·고심각만 인간 게이트)"),
]

SITE_CSS = """
.hero{padding:56px 0 30px;border-bottom:1px solid var(--line)}
.hero .mission{font-size:clamp(22px,3.4vw,34px);line-height:1.42;font-weight:700;
  letter-spacing:-.5px;margin:0 0 14px;max-width:22em}
.hero .mission em{font-style:normal;color:var(--acc)}
.hero .sub{color:var(--dim);max-width:44em;margin:0}
.biz{border:1px solid var(--line);border-radius:var(--radius);padding:18px;
  background:var(--panel);display:flex;flex-direction:column;gap:8px}
.biz h3{margin:0}
.biz .mission{color:var(--dim);font-size:14px;margin:0}
.road{list-style:none;padding:0;margin:6px 0 0;font-size:13px}
.road li{padding:3px 0;color:var(--dim)}
.road li b{color:var(--ink);font-weight:600}
.st{display:inline-block;min-width:64px;font-size:11px}
.st.in_progress{color:var(--acc)} .st.planned{color:var(--dim)} .st.done{color:var(--ok)}
.org{display:grid;gap:10px}
.org .u{border-left:3px solid var(--acc);padding:6px 0 6px 12px}
.org .u .teams{color:var(--dim);font-size:13px}
.ladder{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.ladder .s{border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px}
.ladder .s b{color:var(--acc)}
"""

# 인프라 등급 설명 — 요청자가 고르는 것이라 사람 말로 적는다.
# 목록 자체는 dawn_core.workintake.TIER_LABEL 이 권위다 (여기는 화면 표기).
TIER_LABELS = {
    "none": ("환경 불필요", "진단·검토·문서 작업"),
    "container": ("컨테이너", "데모·PoC·단기 실험"),
    "vm": ("가상머신", "프로덕트 구축·이관"),
    "server": ("전용 서버", "상시 운영·대규모"),
}

NAV = [("/", "홈"), ("/business", "사업"), ("/org", "조직"),
       ("/request", "작업 요청"), ("/contact", "문의")]

# ── 문의 폼 방어 ─────────────────────────────────────────────────────────
MAX_LEN = {"name": 80, "email": 160, "org": 120, "message": 4000}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,}$")
LINK_RE = re.compile(r"https?://", re.I)
RATE_WINDOW_S = 60
RATE_MAX = 3
_recent: dict[str, list[float]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utf8(value: str) -> str:
    """latin-1 로 잘못 디코딩된 UTF-8 을 되살린다.

    브라우저는 폼 값을 퍼센트 인코딩해 보내므로 보통은 문제가 없다. 그런데
    `curl -d` 같은 클라이언트가 **원문 UTF-8 바이트**를 그대로 보내면 ASGI 층이
    latin-1 로 디코딩해 `김도입` 이 `ê¹€ë„ìž…` 이 된다.

    되살릴 수 있을 때만 되살린다 — latin-1 로 다시 인코딩해서 UTF-8 로 디코딩이
    되면 그건 깨진 것이고, 안 되면 원래 정상이던 값이다.

    이걸 방치하면 한글 문의가 통째로 못 쓰게 되고, 깨진 바이트에 섞인 `\x85` 가
    JSONL 을 쪼갠다 (dawn_core.jsonl 참조).
    """
    if value.isascii():
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired


def _footer() -> Safe:
    return join([
        p("the dawn of AGI (다노파기) — ", MISSION),
        p(small("이 홈페이지는 공개(L0) 자산이다. 사내 시스템·관제 데이터에 접근하지 않는다.")),
    ])


def _shell(title: str, body: Safe, current: str) -> HTMLResponse:
    return HTMLResponse(page(
        f"{title} — the dawn of AGI", body,
        css=SITE_CSS, nav=navbar(NAV, current), footer=_footer(),
    ))


# ── 페이지 ───────────────────────────────────────────────────────────────


def _registry(request: Request):
    return request.app.state.registry


async def home(request: Request) -> HTMLResponse:
    reg = _registry(request)
    active = [b for b in reg.businesses.values() if b.data.get("status") == "active"]
    body = join([
        Safe('<div class="hero">'),
        Safe('<p class="mission">AI 역사가 <em>AGI</em>로 가는 길에 필요한 것들을 '
             '만들어 공급하고 확산한다.</p>'),
        p("에이전트가 스스로 일하고, 그 일을 사람이 관제한다. "
          "우리는 그 관제 체계를 만들고 — 우리 회사에 먼저 적용한다.", class_="sub"),
        Safe("</div>"),

        h2("우리가 만드는 것"),
        div(
            div(h3("에이전트 하네스·루프"),
                p("에이전트가 무엇을 참조하고, 무엇을 실행하고, 무엇을 기록하는지 "
                  "구조로 강제하는 실행 계층.", class_="dim")),
            div(h3("AOC — 에이전트 관제"),
                p("수집·탐지·트리아지·대응. 보안 침해와 품질 실패를 같은 인시던트 "
                  "체계로 다룬다.", class_="dim")),
            div(h3("업무 시스템"),
                p("홈페이지·그룹웨어·업무 시스템. 사람이 에이전트에 개입하는 통로까지 "
                  "포함한다.", class_="dim")),
            class_="grid c3",
        ),

        h2("일하는 방식"),
        div(*[
            div(h3(t), p(d, class_="dim"), class_="card") for t, d in PRINCIPLES
        ], class_="grid c2"),

        h2("자율화 단계"),
        p("에이전트에게 권한을 한 번에 주지 않는다. KPI(개입률·오탐율·할루시네이션율)를 "
          "충족할 때만 한 칸 올린다. 인시던트가 나면 즉시 A0 로 내린다.", class_="dim"),
        Safe('<div class="ladder">' + "".join(
            f'<div class="s"><b>{k}</b><br><span class="dim small">{h(v)}</span></div>'
            for k, v in AUTONOMY
        ) + "</div>"),

        h2("사업"),
        div(*[_biz_card(b.data) for b in sorted(active, key=lambda x: x.data["id"])],
            class_="grid c2"),
        p(a("전체 사업 보기 →", href="/business")),
    ])
    return _shell("홈", body, "/")


def _biz_card(d: dict[str, Any]) -> Safe:
    road = d.get("roadmap") or []
    return div(
        Safe(f'<h3>{h(d.get("name", d["id"]))}</h3>'),
        p(d.get("mission", ""), class_="mission"),
        Safe('<ul class="road">' + "".join(
            f'<li><span class="st {h(r.get("status", ""))}">'
            f'{h(_status_label(r.get("status", "")))}</span> <b>{h(r.get("phase", ""))}</b>'
            f' — {h(r.get("goal", ""))}</li>'
            for r in road
        ) + "</ul>") if road else None,
        class_="biz",
    )


def _status_label(s: str) -> str:
    return {"in_progress": "진행 중", "planned": "예정", "done": "완료"}.get(s, s or "-")


async def business(request: Request) -> HTMLResponse:
    reg = _registry(request)
    items = sorted(reg.businesses.values(), key=lambda b: (b.data.get("status") != "active",
                                                           b.data["id"]))
    body = join([
        h1("사업"),
        p("사업은 플러그인이다. 새 사업은 조직 레지스트리에 YAML 한 장을 더하면 "
          "조직·에이전트·관제가 따라 붙는다.", class_="lede"),
        div(*[_biz_full(b.data, reg) for b in items], class_="grid"),
    ])
    return _shell("사업", body, "/business")


def _biz_full(d: dict[str, Any], reg) -> Safe:
    divs = [reg.divisions[x].data["name"] for x in (d.get("owning_divisions") or [])
            if x in reg.divisions]
    status = d.get("status", "")
    return div(
        div(
            Safe(f'<h3 style="display:inline">{h(d.get("name", d["id"]))}</h3> '),
            span(_status_label(status),
                 class_="tag " + ("acc" if status == "active" else "")),
            class_="row",
        ),
        p(d.get("mission", ""), class_="dim"),
        Safe("<table>"
             f"<tr><th>수익 모델</th><td>{h(', '.join(d.get('revenue_model') or []))}</td></tr>"
             f"<tr><th>대상</th><td>{h(', '.join(d.get('target_segments') or []))}</td></tr>"
             f"<tr><th>담당 본부</th><td>{h(', '.join(divs) or '-')}</td></tr>"
             f"<tr><th>테넌트</th><td>{h(d.get('tenant_model', '-'))}</td></tr>"
             f"<tr><th>데이터 등급</th><td>{h(d.get('data_sensitivity', '-'))}</td></tr>"
             "</table>"),
        Safe('<ul class="road">' + "".join(
            f'<li><span class="st {h(r.get("status", ""))}">'
            f'{h(_status_label(r.get("status", "")))}</span> <b>{h(r.get("phase", ""))}</b>'
            f' — {h(r.get("goal", ""))}</li>'
            for r in (d.get("roadmap") or [])
        ) + "</ul>"),
        class_="card",
    )


async def org(request: Request) -> HTMLResponse:
    reg = _registry(request)
    units = []
    for dv in sorted(reg.divisions.values(), key=lambda d: d.data['id']):
        teams = [reg.teams[t].data["name"] for t in dv.data.get("teams", [])
                 if t in reg.teams]
        units.append(div(
            Safe(f'<b>{h(dv.data["name"])}</b>'),
            p(dv.data.get("mission", ""), class_="dim small"),
            div(f"팀 {len(teams)} · " + ", ".join(teams), class_="teams"),
            class_="u",
        ))
    body = join([
        h1("조직"),
        p("4본부. 조직은 사업을 담는 그릇이고, 에이전트의 권한 경계이기도 하다 — "
          "승인 권한은 이 조직 트리를 탄다.", class_="lede"),
        div(*units, class_="org"),
        h2("이 회사에서 조직이 하는 일"),
        ul(
            li("에이전트의 ", strong("권한 범위"), "를 정한다 (게이트는 조직 단위로 좁아진다)"),
            li("에이전트의 ", strong("모델"), "을 정한다 (민감 데이터를 다루는 조직은 로컬 모델)"),
            li("사람의 ", strong("승인 권한"), "을 정한다 (자기 조직과 하위 조직만 승인할 수 있다)"),
        ),
    ])
    return _shell("조직", body, "/org")


async def contact(request: Request) -> HTMLResponse:
    return _shell("문의", _contact_body(), "/contact")


def _contact_body(*, error: str = "", ok: bool = False,
                  values: dict[str, str] | None = None) -> Safe:
    v = values or {}
    if ok:
        return join([
            h1("문의"),
            div("접수했다. 담당자가 확인 후 회신한다.", class_="flash ok"),
            p(a("← 홈으로", href="/")),
        ])
    return join([
        h1("문의"),
        p("사업 협의·도입 문의. 접수 내용은 사내 담당자에게만 전달된다.", class_="lede"),
        div(error, class_="flash bad") if error else None,
        Safe('<form class="stack" method="post" action="/contact">'),
        Safe('<div><label for="c-name">이름 *</label>')
        + input_(type="text", id="c-name", name="name", required=True,
                 maxlength=MAX_LEN["name"], value=v.get("name", "")) + Safe("</div>"),
        Safe('<div><label for="c-email">이메일 *</label>')
        + input_(type="email", id="c-email", name="email", required=True,
                 maxlength=MAX_LEN["email"], value=v.get("email", "")) + Safe("</div>"),
        Safe('<div><label for="c-org">소속</label>')
        + input_(type="text", id="c-org", name="org", maxlength=MAX_LEN["org"],
                 value=v.get("org", "")) + Safe("</div>"),
        Safe('<div><label for="c-msg">내용 *</label>')
        + textarea("message", v.get("message", ""), id="c-msg", required=True,
                   maxlength=MAX_LEN["message"]) + Safe("</div>"),
        # 허니팟 — 사람 눈에는 안 보이고 스크립트는 채운다
        Safe('<div style="position:absolute;left:-9999px" aria-hidden="true">')
        + Safe('<label for="c-web">website</label>')
        + input_(type="text", id="c-web", name="website", tabindex="-1",
                 autocomplete="off") + Safe("</div>"),
        input_(type="hidden", name="ts", value=str(int(time.time()))),
        Safe('<div><button type="submit">보내기</button></div>'),
        Safe("</form>"),
        p(small("스팸 방지를 위해 짧은 시간에 여러 번 보내면 잠시 차단된다."), class_="dim"),
    ])


# ── 작업 요청 (P7) ───────────────────────────────────────────────────────
#
# 문의와 **다르다.** 문의는 "물어봄"이고 작업 요청은 "해달라"다. 그래서 접수 즉시
# 작업 지시가 만들어지고 결재 라인이 붙는다.
#
# 선택지(사업·인프라 등급)는 화면이 정하지 않는다 — `dawn_core.workintake` 가 매니페스트에서
# 뽑아 준다. 접수 경로가 늘어나도(그룹웨어·API·챗봇) 규칙이 한 곳에 있게.


def _biz_choices(root):
    # 레지스트리(org/)만 읽는다 — 업무 DB 로 가는 경로를 만들지 않는다 (P4 격리).
    from dawn_core import workintake

    return workintake.choices(Path(root))


def _request_body(root, *, error: str = "", ok: bool = False, order_id: int = 0,
                  chain=None, values: dict[str, str] | None = None) -> Safe:
    v = values or {}
    if ok:
        steps = " → ".join(f"{c['role']}" for c in (chain or [])) or "담당 부서"
        _ = order_id
        return join([
            h1("작업 요청"),
            div("접수했다. 담당 부서가 확인 후 결재를 올린다.", class_="flash ok"),
            p(f"결재 순서: {steps}"),
            p("승인이 끝나면 작업 환경이 준비되고 담당 에이전트가 착수한다.", class_="dim"),
            p(a("← 홈으로", href="/")),
        ])
    bcs = _biz_choices(root)
    opts = [Safe('<option value="">— 선택 —</option>')]
    for c in bcs:
        sel = " selected" if v.get("business") == c.id else ""
        opts.append(Safe(f'<option value="{c.id}"{sel}>') + c.name +
                    Safe(f' ({", ".join(c.tiers)})</option>'))
    tier_rows = []
    for label, desc in TIER_LABELS.values():
        tier_rows.append(Safe('<div style="margin:2px 0">') +
                         small(f"{label} — {desc}") + Safe("</div>"))
    return join([
        h1("작업 요청"),
        p("도입·구축·개선 요청을 접수한다. 문의(질문)와 달리 접수 즉시 "
          "담당 본부의 결재 라인이 붙는다.", class_="lede"),
        div(error, class_="flash bad") if error else None,
        Safe('<form class="stack" method="post" action="/request">'),
        Safe('<div><label for="r-name">이름 *</label>')
        + input_(type="text", id="r-name", name="name", required=True,
                 maxlength=MAX_LEN["name"], value=v.get("name", "")) + Safe("</div>"),
        Safe('<div><label for="r-email">이메일 *</label>')
        + input_(type="email", id="r-email", name="email", required=True,
                 maxlength=MAX_LEN["email"], value=v.get("email", "")) + Safe("</div>"),
        Safe('<div><label for="r-org">소속</label>')
        + input_(type="text", id="r-org", name="org", maxlength=MAX_LEN["org"],
                 value=v.get("org", "")) + Safe("</div>"),
        Safe('<div><label for="r-biz">사업 *</label><select id="r-biz" name="business" required>')
        + join(opts) + Safe("</select></div>"),
        Safe('<div><label for="r-tier">필요한 환경 *</label>')
        + Safe('<select id="r-tier" name="infra_tier" required>')
        + join([Safe(f'<option value="{t}"'
                     f'{" selected" if v.get("infra_tier") == t else ""}>')
                + TIER_LABELS[t][0] + Safe("</option>")
                for t in ("none", "container", "vm", "server")])
        + Safe("</select></div>"),
        join(tier_rows),
        Safe('<div><label for="r-title">제목 *</label>')
        + input_(type="text", id="r-title", name="title", required=True,
                 maxlength=MAX_LEN["name"], value=v.get("title", "")) + Safe("</div>"),
        Safe('<div><label for="r-msg">내용 *</label>')
        + textarea("message", v.get("message", ""), id="r-msg", required=True,
                   maxlength=MAX_LEN["message"]) + Safe("</div>"),
        Safe('<div style="position:absolute;left:-9999px" aria-hidden="true">')
        + Safe('<label for="r-web">website</label>')
        + input_(type="text", id="r-web", name="website", tabindex="-1",
                 autocomplete="off") + Safe("</div>"),
        input_(type="hidden", name="ts", value=str(int(time.time()))),
        Safe('<div><button type="submit">요청하기</button></div>'),
        Safe("</form>"),
        p(small("사업마다 선택 가능한 환경이 다르다. 허용되지 않은 조합은 거부된다."),
          class_="dim"),
    ])


async def work_request(request: Request) -> HTMLResponse:
    return _shell("작업 요청", _request_body(request.app.state.root), "/request")


async def work_request_post(request: Request) -> HTMLResponse:
    from dawn_core import workintake

    root = Path(request.app.state.root)
    form_data = await request.form()
    keys = ("name", "email", "org", "message", "title", "business", "infra_tier")
    values = {k: _utf8(str(form_data.get(k, "")))[:MAX_LEN.get(k, 200)] for k in keys}
    ip = request.client.host if request.client else "-"

    err = _validate_contact(form_data, values, ip)
    if not err and not values["title"].strip():
        err = "제목을 입력하라."
    if not err:
        try:
            division, tier = workintake.validate(
                root, business=values["business"], infra_tier=values["infra_tier"])
        except ValueError as e:
            err = str(e)
    if err:
        return _shell("작업 요청", _request_body(root, error=err, values=values), "/request")

    # **접수함에 떨군다. 업무 DB 에 직접 쓰지 않는다.**
    # 공개 사이트는 zone:ext 이고 업무 DB 는 dmz/int 다 — 여기서 DB 로 직접 가는
    # 경로를 만들면 공개면 취약점 하나가 내부 데이터까지 닿는다. 문의와 같은 방식으로
    # 파일에 떨구고 `dawn-biz intake` 가 가져가 작업 지시로 승격한다.
    path = Path(root) / "var" / "website" / "work_requests.jsonl"
    jsonl.append(path, {"at": _now(), "ip": ip, "origin": "external",
                        "division": division, "infra_tier": tier, **values})
    chain = workintake.approval_chain(root, business=values["business"], infra_tier=tier,
                                      division=division, origin="external")
    _recent.setdefault(ip, []).append(time.time())
    return _shell("작업 요청", _request_body(root, ok=True, chain=chain), "/request")


async def contact_post(request: Request) -> HTMLResponse:
    form_data = await request.form()
    values = {k: _utf8(str(form_data.get(k, "")))[:MAX_LEN.get(k, 200)]
              for k in ("name", "email", "org", "message")}
    ip = request.client.host if request.client else "-"

    err = _validate_contact(form_data, values, ip)
    if err:
        return _shell("문의", _contact_body(error=err, values=values), "/contact")

    path = Path(request.app.state.root) / "var" / "website" / "inquiries.jsonl"
    jsonl.append(path, {"at": _now(), "ip": ip, **values})
    _recent.setdefault(ip, []).append(time.time())
    return _shell("문의", _contact_body(ok=True), "/contact")


def _validate_contact(form_data, values: dict[str, str], ip: str) -> str:
    if str(form_data.get("website", "")):
        return "전송에 실패했다. 잠시 후 다시 시도하라."     # 허니팟 — 이유를 알려주지 않는다
    try:
        ts = int(str(form_data.get("ts", "0")))
    except ValueError:
        ts = 0
    if ts and time.time() - ts < 2:
        return "너무 빨리 전송됐다. 다시 시도하라."           # 사람은 2초 안에 못 채운다

    now = time.time()
    hits = [t for t in _recent.get(ip, []) if now - t < RATE_WINDOW_S]
    _recent[ip] = hits
    if len(hits) >= RATE_MAX:
        return f"{RATE_WINDOW_S}초 안에 {RATE_MAX}건까지만 보낼 수 있다. 잠시 후 다시 시도하라."

    if not values["name"].strip():
        return "이름을 입력하라."
    if not EMAIL_RE.match(values["email"].strip()):
        return "이메일 형식이 올바르지 않다."
    msg = values["message"].strip()
    if len(msg) < 10:
        return "내용을 10자 이상 입력하라."
    if len(LINK_RE.findall(msg)) > 2:
        return "링크가 너무 많다. 내용을 정리해 다시 보내라."
    return ""


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "app": "website", "level": "L0"})


async def robots(request: Request) -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /contact\n")


routes = [
    Route("/", home),
    Route("/business", business),
    Route("/org", org),
    Route("/request", work_request, methods=["GET"]),
    Route("/request", work_request_post, methods=["POST"]),
    Route("/contact", contact, methods=["GET"]),
    Route("/contact", contact_post, methods=["POST"]),
    Route("/healthz", health),
    Route("/robots.txt", robots),
]

__all__ = ["MISSION", "routes"]
