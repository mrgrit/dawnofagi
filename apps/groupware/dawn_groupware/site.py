"""공개 홈페이지 — L0. **내부 데이터에 손이 닿지 않는다.**

이 모듈은 `store`(업무 DB)·`egedit`(EG 조정)·`auth`(계정)를 **임포트하지 않는다.**
공개 프로세스가 내부 자산에 도달할 코드 경로가 아예 없어야 dmz 앞단에 놓을 수 있다.
읽는 것은 `org/` 레지스트리(YAML)와 헌장 요약뿐이고, 쓰는 것은 문의 접수 파일 하나다.

테스트(`test_site_cannot_reach_internals`)가 이 격리를 고정한다.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

NAV = [("/", "홈"), ("/business", "사업"), ("/org", "조직"), ("/contact", "문의")]

# ── 문의 폼 방어 ─────────────────────────────────────────────────────────
MAX_LEN = {"name": 80, "email": 160, "org": 120, "message": 4000}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,}$")
LINK_RE = re.compile(r"https?://", re.I)
RATE_WINDOW_S = 60
RATE_MAX = 3
_recent: dict[str, list[float]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


async def contact_post(request: Request) -> HTMLResponse:
    form_data = await request.form()
    values = {k: str(form_data.get(k, ""))[:MAX_LEN.get(k, 200)]
              for k in ("name", "email", "org", "message")}
    ip = request.client.host if request.client else "-"

    err = _validate_contact(form_data, values, ip)
    if err:
        return _shell("문의", _contact_body(error=err, values=values), "/contact")

    path = Path(request.app.state.root) / "var" / "website" / "inquiries.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"at": _now(), "ip": ip, **values}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
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
    Route("/contact", contact, methods=["GET"]),
    Route("/contact", contact_post, methods=["POST"]),
    Route("/healthz", health),
    Route("/robots.txt", robots),
]

__all__ = ["MISSION", "routes"]
