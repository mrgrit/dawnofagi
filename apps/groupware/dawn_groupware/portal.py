"""사내 그룹웨어 — 사람이 에이전트에 개입하는 관문.

화면 순서가 곧 우선순위다:

    승인 큐   에이전트가 멈춰서 사람을 기다리는 곳. **가장 먼저 본다.**
    EG 조정   에이전트 행동을 바꾸는 유일한 정식 경로
    관제      지금 무슨 일이 일어나고 있나 (P3 콘솔로 연결)
    공지·문서·일정·디렉터리   그다음

모든 POST 는 CSRF 토큰을 요구하고, 모든 권한 판정은 `auth.can*` 하나만 거친다.
권한 검사를 화면 렌더 코드에 흩뿌리지 않는다 — 흩뿌리면 언젠가 한 군데가 빠진다.
"""

from __future__ import annotations

import functools
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dawn_agents.hitl import ApprovalQueue
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .audit import AuditLog
from .auth import CAPABILITIES, CRITICAL_SEVERITY, User, UserStore, can_approve, org_chain
from .egedit import EDITABLE, EGEditError, EGEditor
from .render import (
    Safe,
    a,
    button,
    code,
    diff_block,
    div,
    h,
    h1,
    h2,
    h3,
    input_,
    join,
    li,
    markdownish,
    p,
    page,
    pre,
    select,
    small,
    span,
    table,
    td,
    textarea,
    th,
    tr,
    ul,
)
from .store import LEVEL_RANK, SECURITY_LEVELS, Store

PORTAL_CSS = """
.side{display:grid;grid-template-columns:210px 1fr;gap:26px;align-items:start}
@media(max-width:820px){.side{grid-template-columns:1fr}}
.side nav{display:grid;gap:2px;position:sticky;top:72px}
.side nav a{color:var(--dim);text-decoration:none;padding:6px 10px;border-radius:var(--radius);
  font-size:14px;display:flex;justify-content:space-between;gap:8px}
.side nav a:hover{background:var(--panel);color:var(--ink)}
.side nav a.on{background:var(--panel);color:var(--ink);box-shadow:inset 2px 0 0 var(--acc)}
.side nav .n{background:var(--bad);color:#fff;border-radius:99px;padding:0 7px;font-size:11px}
.side nav .sep{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.6px;
  margin:14px 0 4px;padding:0 10px}
.kv{display:grid;grid-template-columns:120px 1fr;gap:3px 12px;font-size:14px}
.kv .k{color:var(--dim)}
.sev{font-weight:700}
.sev.s6,.sev.s5{color:var(--bad)} .sev.s4,.sev.s3{color:var(--warn)} .sev.s2,.sev.s1,.sev.s0{color:var(--dim)}
.why{border-left:3px solid var(--warn);padding:8px 12px;background:var(--panel);
  border-radius:0 var(--radius) var(--radius) 0;margin:10px 0;font-size:14px}
.why.no{border-left-color:var(--bad)}
.chk{display:flex;gap:7px;align-items:flex-start;font-size:13px;padding:3px 0}
.chk input{width:auto;margin-top:3px}
"""

NAV_PUBLIC = [("/", "대시보드")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 요청 컨텍스트 ────────────────────────────────────────────────────────


def current_user(request: Request) -> User | None:
    username = request.session.get("u")
    if not username:
        return None
    u: UserStore = request.app.state.users
    user = u.get(username)
    if user is None or user.disabled:
        request.session.clear()
        return None
    return user


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def csrf_field(request: Request) -> Safe:
    return input_(type="hidden", name="_csrf", value=csrf_token(request))


def check_csrf(request: Request, form_data) -> bool:
    sent = str(form_data.get("_csrf", ""))
    have = request.session.get("csrf", "")
    return bool(have) and secrets.compare_digest(sent, have)


def _root(request: Request) -> Path:
    return Path(request.app.state.root)


def _audit(request: Request) -> AuditLog:
    return request.app.state.audit


def _ip(request: Request) -> str:
    return request.client.host if request.client else "-"


# ── 권한 데코레이터 ──────────────────────────────────────────────────────


def require(capability: str | None = None) -> Callable:
    """로그인 + (선택) 능력 요구. 권한 판정은 여기 한 곳에서만 한다."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)          # __wrapped__ 를 남긴다 — 트레이스백·검사가 원본을 본다
        async def wrapper(request: Request) -> Response:
            user = current_user(request)
            if user is None:
                return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
            if capability and not user.can(capability):
                _audit(request).write("access.denied", actor=user.username,
                                      target=request.url.path, result="denied",
                                      ip=_ip(request), capability=capability)
                return _forbidden(request, user, capability)
            request.state.user = user
            return await fn(request)

        return wrapper

    return deco


def _forbidden(request: Request, user: User, capability: str) -> HTMLResponse:
    body = join([
        h1("접근 권한이 없다"),
        div(f"이 화면은 '{capability}' 권한이 필요하다. "
            f"{CAPABILITIES.get(capability, '')}", class_="flash bad"),
        div(
            Safe('<div class="kv">'),
            span("계정", class_="k"), span(user.username),
            span("조직", class_="k"), span(user.org),
            span("가진 권한", class_="k"), span(", ".join(user.capabilities) or "(없음)"),
            Safe("</div>"),
            class_="card",
        ),
        p("권한이 필요하면 관리자에게 요청하라. 이 시도는 감사 로그에 남았다.", class_="dim"),
        p(a("← 대시보드", href="/")),
    ])
    return _shell(request, user, "접근 거부", body, status=403)


# ── 껍데기 ───────────────────────────────────────────────────────────────


def _sidebar(request: Request, user: User, current: str) -> Safe:
    q: ApprovalQueue = request.app.state.queue
    pending = len(q.pending(purpose="work"))   # 훈련은 뱃지를 울리지 않는다
    items: list[tuple[str, str, str, str | None]] = [
        ("개입", "", "", None),
        ("/approvals", "승인 큐", "hitl.view", str(pending) if pending else None),
        ("/eg", "EG 조정", "eg.view", None),
        ("/control", "통제 평면", "control.view", None),
        ("/aoc", "관제", "aoc.view", None),
        ("업무", "", "", None),
        ("/", "대시보드", "portal.view", None),
        ("/orders", "작업 지시", "portal.view", None),
        ("/notices", "공지", "portal.view", None),
        ("/documents", "문서", "portal.view", None),
        ("/calendar", "일정", "portal.view", None),
        ("/directory", "구성원", "portal.view", None),
        ("관리", "", "", None),
        ("/audit", "감사 로그", "portal.view", None),
        ("/judgments", "내 판단", "portal.view", None),
        ("/admin/users", "계정", "admin", None),
    ]
    out = []
    for href, text, cap, badge in items:
        if not text:
            out.append(f'<div class="sep">{h(href)}</div>')
            continue
        if cap and not user.can(cap):
            continue
        n = f'<span class="n">{h(badge)}</span>' if badge else ""
        cls = " on" if href == current else ""
        out.append(f'<a class="side-link{cls}" href="{h(href)}">'
                   f'<span>{h(text)}</span>{n}</a>')
    return Safe("<nav>" + "".join(out) + "</nav>")


def _who(user: User) -> Safe:
    return Safe(
        f'<span class="who"><b>{h(user.name)}</b> '
        f'<span class="dim">{h(user.org)}</span> · '
        f'<a href="/logout">로그아웃</a></span>'
    )


def _shell(request: Request, user: User | None, title: str, body: Safe,
           *, current: str = "", status: int = 200) -> HTMLResponse:
    if user is None:
        return HTMLResponse(page(f"{title} — 그룹웨어", body, css=PORTAL_CSS),
                            status_code=status)
    inner = Safe(f'<div class="side">{_sidebar(request, user, current or request.url.path)}'
                 f'<div>{body}</div></div>')
    return HTMLResponse(
        page(f"{title} — 그룹웨어", inner, css=PORTAL_CSS, who=_who(user),
             footer=Safe('<span class="dim">the dawn of AGI 사내 그룹웨어 — '
                         '모든 승인·EG 변경은 감사 로그에 남는다.</span>')),
        status_code=status,
    )


# ── 로그인 ───────────────────────────────────────────────────────────────


async def login(request: Request) -> Response:
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    nxt = request.query_params.get("next", "/")
    return _login_page(request, next_url=nxt)


def _login_page(request: Request, *, error: str = "", next_url: str = "/",
                status: int = 200) -> HTMLResponse:
    body = join([
        Safe('<div style="max-width:380px;margin:8vh auto">'),
        h1("그룹웨어"),
        p("사람이 에이전트에 개입하는 통로다.", class_="lede"),
        div(error, class_="flash bad") if error else None,
        Safe('<form class="stack" method="post" action="/login">'),
        csrf_field(request),
        input_(type="hidden", name="next", value=next_url),
        Safe('<div><label for="u">계정</label>')
        + input_(type="text", id="u", name="username", required=True,
                 autocomplete="username", autofocus=True) + Safe("</div>"),
        Safe('<div><label for="pw">비밀번호</label>')
        + input_(type="password", id="pw", name="password", required=True,
                 autocomplete="current-password") + Safe("</div>"),
        Safe('<div><button type="submit">로그인</button></div>'),
        Safe("</form>"),
        p(small("계정이 없으면 관리자에게 요청하라. "
                "초기 계정은 `dawn-web useradd` 로 만든다."), class_="dim"),
        Safe("</div>"),
    ])
    return HTMLResponse(page("로그인 — 그룹웨어", body, css=PORTAL_CSS),
                        status_code=status)


async def login_post(request: Request) -> Response:
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return _login_page(request, error="세션이 만료됐다. 다시 시도하라.", status=400)
    username = str(form_data.get("username", ""))[:64]
    password = str(form_data.get("password", ""))[:256]
    nxt = str(form_data.get("next", "/")) or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"                                    # 오픈 리다이렉트 차단

    users: UserStore = request.app.state.users
    user = users.authenticate(username, password)
    if user is None:
        _audit(request).write("auth.login", actor=username, result="fail", ip=_ip(request))
        return _login_page(request, error="계정 또는 비밀번호가 맞지 않다.",
                           next_url=nxt, status=401)

    request.session.clear()                          # 세션 고정 공격 방지
    request.session["u"] = user.username
    csrf_token(request)
    users.touch_login(user.username)
    _audit(request).write("auth.login", actor=user.username, result="ok", ip=_ip(request),
                          org=user.org)
    return RedirectResponse(nxt, status_code=303)


async def logout(request: Request) -> Response:
    user = current_user(request)
    if user:
        _audit(request).write("auth.logout", actor=user.username, ip=_ip(request))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── 대시보드 ─────────────────────────────────────────────────────────────


@require("portal.view")
async def dashboard(request: Request) -> Response:
    user: User = request.state.user
    q: ApprovalQueue = request.app.state.queue
    store: Store = request.app.state.store
    reg = request.app.state.registry
    eg = request.app.state.eg

    pending = q.pending(purpose="work")
    mine = [ap for ap in pending
            if can_approve(user, agent_org=_agent_org(reg, ap.agent_id),
                           severity=ap.severity, eg_store=eg)[0]]
    notices = store.notices(limit=5)
    events = store.events(since=_now()[:10], limit=5)

    body = join([
        h1(f"{user.name} 님"),
        p(f"{_org_name(reg, eg, user.org)} · {user.title or '—'}", class_="lede"),

        div(
            _stat("내가 승인할 수 있는 요청", len(mine),
                  "/approvals", bad=bool(mine)),
            _stat("전체 승인 대기", len(pending), "/approvals"),
            _stat("공지", store.counts()["notice"], "/notices"),
            _stat("문서", store.counts()["document"], "/documents"),
            class_="grid c3",
        ),

        h2("나를 기다리는 승인") if mine else None,
        _approval_table(request, mine[:6], reg, eg) if mine else None,
        p("지금 당신을 기다리는 요청은 없다.", class_="empty") if not mine else None,

        h2("공지"),
        join([div(
            Safe(f'<b>{h(n["title"])}</b> ')
            + span("고정", class_="tag acc") if n["pinned"] else Safe(f'<b>{h(n["title"])}</b>'),
            p(n["body"][:180] + ("…" if len(n["body"]) > 180 else ""), class_="dim small"),
            small(f'{n["author"]} · {n["created_at"][:16].replace("T", " ")}', class_="dim"),
            class_="card",
        ) for n in notices]) if notices else p("공지가 없다.", class_="empty"),

        h2("다가오는 일정"),
        table(
            tr(th("시작"), th("일정"), th("장소")),
            *[tr(td(e["starts_at"][:16].replace("T", " ")), td(e["title"]),
                 td(e["location"] or "-")) for e in events],
        ) if events else p("등록된 일정이 없다.", class_="empty"),
    ])
    return _shell(request, user, "대시보드", body, current="/")


def _stat(label: str, value: Any, href: str, *, bad: bool = False) -> Safe:
    return Safe(
        f'<a class="card" style="text-decoration:none;display:block" href="{h(href)}">'
        f'<div class="dim small">{h(label)}</div>'
        f'<div style="font-size:26px;font-weight:700;'
        f'color:{"var(--bad)" if bad else "var(--ink)"}">{h(value)}</div></a>'
    )


# ── 승인 큐 ──────────────────────────────────────────────────────────────


def _agent_org(registry, agent_id: str) -> str:
    ag = registry.agents.get(agent_id)
    if ag is None:
        return ""
    team = registry.teams.get(ag.team_id)
    return (team.data.get("eg_org", "") if team else "")


def _org_name(registry, eg, org_id: str) -> str:
    if eg is not None:
        n = eg.node(org_id)
        if n is not None:
            return f"{n.name} ({org_id})"
    return org_id


def _approval_table(request: Request, approvals, reg, eg) -> Safe:
    rows = [tr(th("요청"), th("에이전트"), th("도구"), th("심각도"), th("자산"), th(""))]
    for ap in approvals:
        rows.append(tr(
            td(small(ap.id, class_="mono")),
            td(ap.agent_id, Safe("<br>"), small(_agent_org(reg, ap.agent_id), class_="dim")),
            td(code(ap.skill)),
            td(span(f"{ap.severity_label}/{ap.severity}", class_=f"sev s{ap.severity}")),
            td(small(", ".join(ap.assets) or "-", class_="dim")),
            td(a("보기 →", href=f"/approvals/{ap.id}")),
        ))
    return table(*rows)


@require("hitl.view")
async def approvals(request: Request) -> Response:
    user: User = request.state.user
    q: ApprovalQueue = request.app.state.queue
    reg, eg = request.app.state.registry, request.app.state.eg
    status = request.query_params.get("status", "pending")
    # **기본은 실업무만.** 훈련(리허설·레드팀)을 섞으면 사람이 볼 것을 못 찾는다 —
    # 실측으로 대기 2633건 중 2303건이 인시던트 리허설이었다. 지우지는 않는다:
    # 리허설이 게이트를 제대로 때렸다는 사실 자체가 증거다.
    scope = request.query_params.get("scope", "work")
    items = q.list(None if status == "all" else status,
                   purpose=None if scope == "all" else scope)
    counts = q.counts()
    drills = sum(v for k, v in counts.items() if k != "work")

    mine, others = [], []
    for ap in items:
        ok, _ = can_approve(user, agent_org=_agent_org(reg, ap.agent_id),
                            severity=ap.severity, eg_store=eg)
        (mine if ok else others).append(ap)

    body = join([
        h1("승인 큐"),
        p("에이전트가 비가역·고심각 행동 앞에서 멈춰 사람을 기다리는 곳이다. "
          "여기서 누르기 전에는 그 행동이 실행되지 않는다.", class_="lede"),
        div(*[a(t, href=f"/approvals?status={s}&scope={scope}",
                class_="btn ghost" if status != s else "btn")
              for s, t in [("pending", "대기"), ("approved", "승인됨"),
                           ("denied", "거부됨"), ("all", "전체")]], class_="row"),
        div(*[a(t, href=f"/approvals?status={status}&scope={s}",
                class_="btn ghost" if scope != s else "btn")
              for s, t in [("work", f'실업무 {counts.get("work", 0)}'),
                           ("all", f"전체 {sum(counts.values())}")]], class_="row"),
        (small(f"훈련·미분류 {drills}건은 기본 화면에서 숨긴다 — "
               "지우지는 않는다(게이트가 제대로 막았다는 증거다).", class_="dim")
         if drills else None),

        h2(f"내가 승인할 수 있는 것 ({len(mine)})"),
        _approval_table(request, mine, reg, eg) if mine
        else p("없다.", class_="empty"),

        h2(f"다른 조직 소관 ({len(others)})"),
        p("승인은 그 일을 아는 조직이 한다. 목록은 보이지만 누를 수 없다.", class_="dim small"),
        _approval_table(request, others, reg, eg) if others
        else p("없다.", class_="empty"),
    ])
    return _shell(request, user, "승인 큐", body, current="/approvals")


@require("hitl.view")
async def approval_detail(request: Request) -> Response:
    user: User = request.state.user
    q: ApprovalQueue = request.app.state.queue
    reg, eg = request.app.state.registry, request.app.state.eg
    aid = request.path_params["aid"]
    try:
        ap = q.get(aid)
    except KeyError:
        return _shell(request, user, "없음",
                      join([h1("승인 요청이 없다"), p(a("← 승인 큐", href="/approvals"))]),
                      status=404)

    agent_org = _agent_org(reg, ap.agent_id)
    allowed, why = can_approve(user, agent_org=agent_org, severity=ap.severity, eg_store=eg)
    chain = org_chain(eg, agent_org)

    decided = ap.status != "pending"
    body = join([
        h1(f"{ap.skill}"),
        p(f"{ap.agent_id} · {_org_name(reg, eg, agent_org)}", class_="lede"),

        div(
            Safe('<div class="kv">'),
            span("요청 id", class_="k"), span(code(ap.id)),
            span("판정", class_="k"), span(ap.decision),
            span("심각도", class_="k"),
            span(span(f"{ap.severity_label}/{ap.severity}", class_=f"sev s{ap.severity}"),
                 Safe(" ") + span("최고 — 비가역·L3", class_="tag bad")
                 if ap.severity >= CRITICAL_SEVERITY else Safe("")),
            span("자산", class_="k"), span(", ".join(ap.assets) or "-"),
            span("정책", class_="k"), span(", ".join(ap.policies) or "-"),
            span("트레이스", class_="k"), span(code(ap.trace_id or "-")),
            span("요청 시각", class_="k"), span(ap.requested_at.replace("T", " ")),
            span("목적", class_="k"),
            span(ap.purpose + ("" if ap.is_work else "  — 훈련이다")),
            span("상태", class_="k"),
            span(span(ap.status, class_="tag " + {"pending": "warn", "approved": "ok",
                                                  "denied": "bad"}.get(ap.status, ""))),
            Safe("</div>"),
            class_="card",
        ),

        h3("게이트가 이걸 올린 이유"),
        ul(*[li(r) for r in ap.reasons]) if ap.reasons else p("(근거 없음)", class_="empty"),

        h3("인자"),
        pre(_fmt_args(ap.args)),

        # **승인이 집행으로 이어지는지 먼저 말한다.** 이 시스템의 HITL 은
        # "멈추고 기록"이지 "기다렸다 재개"가 아니다 — 그걸 화면이 안 말하면
        # 사람이 눌러 놓고 집행됐다고 믿는다.
        (div(Safe("<b>이 요청을 낸 실행은 이미 끝났다.</b> 지금 승인해도 "
                  "<b>그 행동은 집행되지 않는다</b> — 이 시스템의 HITL 은 멈추고 "
                  "기록하는 구조이지 기다렸다 재개하는 구조가 아니다.<br>"
                  "<span class='dim small'>여기서 누르는 것은 <b>판단 기록</b>이다: "
                  "같은 상황을 다음에 허용할지에 대한 근거로 남는다. 지금 그 행동이 "
                  "필요하면 작업을 다시 돌려라.</span>"), class_="flash warn")
         if ap.run_ended and not decided else None),
        (div("이 요청은 훈련(리허설·레드팀)에서 나왔다. 게이트가 제대로 막았다는 "
             "증거이지 사람이 판단할 일이 아니다.", class_="flash warn")
         if not ap.is_work and not decided else None),

        h3("승인 권한"),
        div(
            Safe("승인할 수 있다. ") if allowed else Safe(h(why)),
            Safe(f"<br><span class='dim small'>이 요청의 승인 가능 조직: "
                 f"{h(' → '.join(chain) or '-')}</span>"),
            class_="why" if allowed else "why no",
        ),

        _decision_form(request, ap) if (allowed and not decided) else None,
        _decided_block(ap) if decided else None,

        p(a("← 승인 큐", href="/approvals")),
    ])
    return _shell(request, user, "승인", body, current="/approvals")


def _fmt_args(args: dict[str, Any]) -> str:
    import json as _json

    if not args:
        return "(없음)"
    return _json.dumps(args, ensure_ascii=False, indent=2)


def _decision_form(request: Request, ap) -> Safe:
    critical = ap.severity >= CRITICAL_SEVERITY
    return join([
        h3("판정"),
        Safe(f'<form class="stack" method="post" action="/approvals/{h(ap.id)}">'),
        csrf_field(request),
        Safe('<div><label for="note">사유 (감사 로그에 남는다)</label>')
        + textarea("note", "", id="note", rows="3",
                   placeholder="왜 승인/거부하는지. 다음 사람이 읽는다.") + Safe("</div>"),
        Safe('<div class="chk"><input type="checkbox" id="ack" name="ack" value="1" required>'
             '<label for="ack" style="margin:0">'
             + ("이 행동이 <b>되돌릴 수 없다</b>는 것을 이해했고, 인자를 확인했다."
                if critical else
                "인자와 게이트 근거를 확인했다.")
             + "</label></div>"),
        Safe('<div class="row">'),
        button("승인", type="submit", name="decision", value="approve"),
        button("거부", type="submit", name="decision", value="deny", class_="bad"),
        Safe("</div></form>"),
        p(small("승인은 되돌릴 수 없다. 한 번 판정된 요청은 재판정할 수 없다 (감사 추적)."),
          class_="dim"),
    ])


def _decided_block(ap) -> Safe:
    cls = "ok" if ap.status == "approved" else "bad"
    return div(
        Safe(f'<b>{h(ap.status)}</b> — {h(ap.decided_by)} · '
             f'{h(ap.decided_at.replace("T", " "))}'),
        p(ap.note or "(사유 없음)", class_="dim"),
        class_=f"flash {cls}",
    )


@require("hitl.approve")
async def approval_decide(request: Request) -> Response:
    user: User = request.state.user
    q: ApprovalQueue = request.app.state.queue
    reg, eg = request.app.state.registry, request.app.state.eg
    aid = request.path_params["aid"]
    form_data = await request.form()

    if not check_csrf(request, form_data):
        return _shell(request, user, "오류",
                      join([h1("세션이 만료됐다"), p(a("← 승인 큐", href="/approvals"))]),
                      status=400)
    try:
        ap = q.get(aid)
    except KeyError:
        return RedirectResponse("/approvals", status_code=303)

    agent_org = _agent_org(reg, ap.agent_id)
    allowed, why = can_approve(user, agent_org=agent_org, severity=ap.severity, eg_store=eg)
    if not allowed:
        _audit(request).write("hitl.decide", actor=user.username, target=aid,
                              result="denied", ip=_ip(request), reason=why,
                              agent_org=agent_org, severity=ap.severity)
        return _shell(request, user, "권한 없음",
                      join([h1("이 요청은 승인할 수 없다"),
                            div(why, class_="flash bad"),
                            p(a("← 승인 큐", href="/approvals"))]), status=403)

    if not form_data.get("ack"):
        return RedirectResponse(f"/approvals/{aid}", status_code=303)

    approve = str(form_data.get("decision", "")) == "approve"
    note = str(form_data.get("note", ""))[:2000]
    try:
        ap = q.decide(aid, approve=approve,
                      by=f"human:{user.username}", note=note)
    except ValueError as exc:
        return _shell(request, user, "이미 판정됨",
                      join([h1("이미 판정된 요청이다"), div(str(exc), class_="flash warn"),
                            p(a("← 승인 큐", href="/approvals"))]), status=409)

    # `decision` 은 위쪽 "권한 없음" 감사 줄과 이 줄을 가르는 표시다. 거기엔
    # 사유가 있어도 사람이 누른 적이 없다 — 그걸 판단으로 세면 트윈은
    # "이 사람은 거부한다"를 배운다(누를 기회조차 없었는데).
    _audit(request).write("hitl.decide", actor=user.username, target=aid,
                          result=ap.status, ip=_ip(request), skill=ap.skill,
                          agent_id=ap.agent_id, agent_org=agent_org,
                          severity=ap.severity, assets=ap.assets, note=note,
                          decision=ap.status)
    return RedirectResponse(f"/approvals/{aid}", status_code=303)


# ── 통제 평면 조정 ────────────────────────────────────────────────────────
#
# **서버에 안 들어가고 웹에서 고친다.** 고치기 어려운 통제는 안 고쳐지고,
# 안 고쳐지는 통제는 현실과 어긋난다 (CLAUDE.md 규칙 7).
#
# 규칙과 롤백은 `cpedit.py` 에 있다. 여기는 화면과 감사만 한다 —
# 검증을 화면에서 하면 CLI 로 부를 때 그 검증이 빠진다.


def _cp_targets(request: Request):
    from . import cpedit

    return cpedit.targets(_root(request))


@require("control.view")
async def control_index(request: Request) -> Response:
    from . import cpedit

    user: User = request.state.user
    ts = _cp_targets(request)
    by_kind: dict[str, list] = {}
    for t in ts:
        by_kind.setdefault(t.kind, []).append(t)

    cards = []
    for kind, spec in cpedit.LAYERS.items():
        items = by_kind.get(kind, [])
        missing = [x for x in items if not x.exists]
        rows = []
        for x in items:
            mark = (Safe(" ") + span("없음", class_="tag")) if not x.exists else Safe("")
            rows.append(li(a(x.id, href=f"/control/{kind}/{h(x.id)}"), " ",
                           small(x.label, class_="dim"), mark))
        cards.append(div(
            h3(spec["label"]),
            p(spec["hint"], class_="dim small"),
            p(f"{len(items)}건" + (f" · 미작성 {len(missing)}" if missing else ""),
              class_="dim small"),
            ul(*rows),
            class_="card"))

    body = join([
        h1("통제 평면"),
        p("에이전트의 행동 규칙과 경계를 여기서 고친다. 서버에 들어갈 필요가 없다.",
          class_="lede"),
        div(Safe("<b>저장 전에 컴파일한다.</b> 레지스트리 무결성과 4계층 컴파일을 "
                 "통과해야 남는다 — <b>경계를 넓히려 하면 단조 축소에 걸려 되돌아간다.</b> "
                 "L1(COMPANY.md)은 여기서 못 고친다. 전사 헌법은 git 리뷰를 거친다."),
            class_="flash warn"),
        _agent_new_form(request) if user.can("control.edit") else None,
        div(*cards, class_="grid c2"),
    ])
    return _shell(request, user, "통제 평면", body, current="/control")


def _agent_new_form(request: Request) -> Safe:
    from dawn_core import Registry

    reg = Registry.load(_root(request))
    # L2 가 있는 팀만 고를 수 있다 — 규칙 없이 일하는 팀에 사람을 넣지 않는다.
    teams = [(t.id, f'{t.data.get("name", t.id)} ({t.id})')
             for t in sorted(reg.teams.values(), key=lambda x: x.id)
             if (t.dir / "AGENT_TEAM.md").is_file()]
    works = [(w, w) for w in sorted(reg.works)]
    return Safe('<details class="card"><summary>에이전트 추가</summary>'
                '<form class="stack" method="post" action="/control/agents" '
                'style="margin-top:10px">') + join([
        csrf_field(request),
        Safe('<div><label for="a-id">id</label>')
        + input_(type="text", id="a-id", name="agent_id", required=True,
                 placeholder="corp-hr-clerk-01", pattern="[a-z0-9]+(-[a-z0-9]+)*")
        + Safe("</div>"),
        Safe('<div><label for="a-name">이름</label>')
        + input_(type="text", id="a-name", name="name", required=True) + Safe("</div>"),
        Safe('<div><label for="a-team">팀</label>')
        + select("team", teams, "", id="a-team", required=True)
        + Safe('<small class="dim">L2(AGENT_TEAM.md)가 있는 팀만 나온다</small></div>'),
        Safe('<div><label for="a-work">업무 SOP</label>')
        + select("works", works, "", id="a-work", multiple=True, size="4")
        + Safe("</div>"),
        Safe('<div><label for="a-tools">선언 도구 (쉼표)</label>')
        + input_(type="text", id="a-tools", name="tools",
                 value="eg.search, eg.record, skill.preview, doc.search")
        + Safe('<small class="dim">팀 경계를 넘는 도구는 저장되지 않는다</small></div>'),
        Safe('<div><label for="a-zone">존</label>')
        + select("zone", [(z, z) for z in ("dmz", "user", "int", "ext")], "dmz",
                 id="a-zone") + Safe("</div>"),
        Safe('<div><label for="a-auto">자율 등급</label>')
        + select("autonomy", [(x, x) for x in ("A0", "A1", "A2")], "A1", id="a-auto")
        + Safe("</div>"),
        Safe('<div><label for="a-why">사유 *</label>')
        + input_(type="text", id="a-why", name="_reason", required=True,
                 maxlength="500", placeholder="왜 이 에이전트가 필요한가")
        + Safe("</div>"),
        Safe('<div><button type="submit" name="op" value="create">만들기</button></div>'),
    ]) + Safe("</form></details>")


@require("control.view")
async def control_edit(request: Request) -> Response:
    from . import cpedit

    user: User = request.state.user
    kind = request.path_params["kind"]
    ident = request.path_params["ident"]
    try:
        t = cpedit.resolve(_root(request), kind, ident)
        text = cpedit.read(_root(request), kind, ident)
    except cpedit.CPEditError as e:
        return _shell(request, user, "없음",
                      join([h1("대상이 없다"), div(str(e), class_="flash bad"),
                            p(a("← 통제 평면", href="/control"))]), status=404)

    spec = cpedit.LAYERS[kind]
    can = user.can("control.edit")
    form = Safe("")
    if can:
        form = (Safe('<form class="stack" method="post" '
                     f'action="/control/{h(kind)}/{h(ident)}">')
                + join([
                    csrf_field(request),
                    textarea("text", text, rows="28", spellcheck="false",
                             style="font-family:ui-monospace,monospace;font-size:13px"),
                    Safe('<div><label for="c-why">변경 사유 *</label>')
                    + input_(type="text", id="c-why", name="_reason", required=True,
                             maxlength="500",
                             placeholder="무엇을 왜 바꾸는가 — 감사 로그에 남는다")
                    + Safe("</div>"),
                    Safe('<div><button type="submit">저장</button> '
                         '<a href="/control">취소</a></div>'),
                ]) + Safe("</form>"))
    else:
        form = Safe('<pre class="card" style="overflow:auto">') \
            + Safe(h(text or "(비어 있다)")) + Safe("</pre>")

    danger = Safe("")
    if can and kind == "agent" and t.exists:
        danger = div(join([
            Safe("<b>에이전트 삭제</b>"),
            small("매니페스트·SOUL 을 지우고 팀 명부에서도 뺀다. 스냅샷은 남는다.",
                  class_="dim"),
            Safe(f'<form method="post" action="/control/agents" '
                 f'onsubmit="return confirm(\'{h(ident)} 를 지운다. 계속?\')">')
            + csrf_field(request)
            + input_(type="hidden", name="agent_id", value=ident)
            + Safe('<div><label for="d-why">사유 *</label>')
            + input_(type="text", id="d-why", name="_reason", required=True)
            + Safe("</div>")
            + Safe('<div><button type="submit" name="op" value="delete" '
                   'class="danger">삭제</button></div>')
            + Safe("</form>"),
        ]), class_="card")

    body = join([
        h1(f"{kind}/{ident}"),
        p(spec["label"] + " — " + spec["hint"], class_="dim"),
        small(str(t.path.relative_to(_root(request))), class_="dim"),
        (div("이 파일은 아직 없다. 저장하면 새로 만들어진다.", class_="flash warn")
         if not t.exists else None),
        form,
        danger,
        p(a("← 통제 평면", href="/control")),
    ])
    return _shell(request, user, f"{kind}/{ident}", body, current="/control")


@require("control.view")
async def control_save(request: Request) -> Response:
    from . import cpedit

    user: User = request.state.user
    if not user.can("control.edit"):
        return _forbidden(request, user, "control.edit")
    kind = request.path_params["kind"]
    ident = request.path_params["ident"]
    form = await request.form()
    if not check_csrf(request, form):
        return RedirectResponse(f"/control/{kind}/{ident}", status_code=303)

    reason = str(form.get("_reason", "")).strip()[:500]
    try:
        res = cpedit.save(_root(request), kind, ident, str(form.get("text", "")),
                          actor=user.username, reason=reason)
    except cpedit.CPEditError as e:
        _audit(request).write("control.save", actor=user.username,
                              target=f"{kind}/{ident}", result="error",
                              ip=_ip(request), reason=reason, error=str(e))
        return _shell(request, user, "실패", join([
            h1("저장하지 않았다"),
            div(str(e), class_="flash bad"),
            p("파일은 바뀌지 않았다. 검증을 통과해야 남는다.", class_="dim"),
            p(a("← 돌아가기", href=f"/control/{kind}/{ident}")),
        ]), status=400)

    _audit(request).write("control.save", actor=user.username,
                          target=f"{kind}/{ident}", result="ok", ip=_ip(request),
                          reason=reason, validation=res.validation,
                          lines=len(res.diff), decision="edited", kind=kind)
    return _shell(request, user, "저장됨", join([
        h1("저장됨"),
        div(f"{kind}/{ident} — {res.validation}", class_="flash ok"),
        (Safe('<pre class="card" style="overflow:auto">')
         + Safe(h("\n".join(res.diff))) + Safe("</pre>") if res.diff else None),
        p(a("← 통제 평면", href="/control"), " · ",
          a("계속 고치기", href=f"/control/{kind}/{ident}")),
    ]))


@require("control.view")
async def control_agents(request: Request) -> Response:
    """에이전트 추가·삭제. **팀 명부까지 손대므로 한 곳에서만 한다.**"""
    from . import cpedit

    user: User = request.state.user
    if not user.can("control.edit"):
        return _forbidden(request, user, "control.edit")
    form = await request.form()
    if not check_csrf(request, form):
        return RedirectResponse("/control", status_code=303)

    op = str(form.get("op", ""))
    reason = str(form.get("_reason", "")).strip()[:500]
    aid = str(form.get("agent_id", "")).strip()
    try:
        if op == "delete":
            res = cpedit.delete_agent(_root(request), aid, actor=user.username,
                                      reason=reason)
        elif op == "create":
            res = cpedit.create_agent(
                _root(request), agent_id=aid, team=str(form.get("team", "")),
                name=str(form.get("name", "")).strip()[:120],
                persona="", works=[str(x) for x in form.getlist("works")],
                tools=[x.strip() for x in str(form.get("tools", "")).split(",")
                       if x.strip()],
                zone=str(form.get("zone", "dmz")),
                autonomy=str(form.get("autonomy", "A1")),
                actor=user.username, reason=reason)
        else:
            raise cpedit.CPEditError(f"알 수 없는 동작: {op}")
    except cpedit.CPEditError as e:
        _audit(request).write(f"control.agent.{op}", actor=user.username, target=aid,
                              result="error", ip=_ip(request), reason=reason,
                              error=str(e))
        return _shell(request, user, "실패", join([
            h1("반영하지 않았다"), div(str(e), class_="flash bad"),
            p(a("← 통제 평면", href="/control")),
        ]), status=400)

    _audit(request).write(f"control.agent.{op}", actor=user.username, target=aid,
                          result="ok", ip=_ip(request), reason=reason,
                          validation=res.validation, decision=op, op=op)
    return _shell(request, user, "반영됨", join([
        h1("반영됨"),
        div(f"{aid} — {res.validation}", class_="flash ok"),
        (p("SOUL.md 가 비어 있다. 판단 성향과 멈추는 순간을 채워라 — "
           "비어 있으면 팀 규칙만 가지고 움직인다.", class_="dim")
         if op == "create" else None),
        p(a("← 통제 평면", href="/control"),
          *((Safe(" · "), a("SOUL 쓰기", href=f"/control/soul/{h(aid)}"))
            if op == "create" else ())),
    ]))


# ── EG 조정 ──────────────────────────────────────────────────────────────


@require("eg.view")
async def eg_index(request: Request) -> Response:
    user: User = request.state.user
    ed: EGEditor = request.app.state.egeditor
    cards = []
    for kind, spec in EDITABLE.items():
        items = ed.load(kind)
        cards.append(div(
            h3(spec["label"]),
            p(f"{len(items)}건 · {spec['file']}", class_="dim small"),
            ul(*[li(a(x.get("id", "?"), href=f"/eg/{kind}/{x.get('id')}"), " ",
                    small(x.get("role") or x.get("statement", "")[:60], class_="dim"))
                 for x in items]),
            class_="card",
        ))
    body = join([
        h1("EG 조정"),
        p("에이전트 행동을 바꾸는 유일한 정식 경로다. 코드를 고치지 않는다 — "
          "여기서 고치면 다음 작업부터 전 에이전트에 전파된다.", class_="lede"),
        div(
            Safe("<b>수정 → 검증 → 재주입.</b> 검증에서 오류가 하나라도 나오면 "
                 "시드가 자동으로 되돌아가고 DB 는 건드리지 않는다."),
            class_="flash warn",
        ),
        div(*cards, class_="grid c2"),
        h2("변경 이력"),
        _eg_history(request),
    ])
    return _shell(request, user, "EG 조정", body, current="/eg")


def _eg_history(request: Request, limit: int = 12) -> Safe:
    recs = _audit(request).tail(limit, action_prefix="eg.")
    if not recs:
        return p("변경 이력이 없다.", class_="empty")
    rows = [tr(th("시각"), th("사람"), th("대상"), th("결과"))]
    for r in recs:
        d = r.get("detail", {})
        rows.append(tr(
            td(small(r["at"].replace("T", " "), class_="mono")),
            td(r["actor"]),
            td(code(r.get("target", "")), Safe("<br>"),
               small(str(d.get("reason", ""))[:70], class_="dim")),
            td(span(r["result"], class_="tag " + ("ok" if r["result"] == "ok" else "bad"))),
        ))
    return table(*rows)


@require("eg.view")
async def eg_node(request: Request) -> Response:
    user: User = request.state.user
    ed: EGEditor = request.app.state.egeditor
    kind = request.path_params["kind"]
    node_id = request.path_params["node_id"]
    spec = EDITABLE.get(kind)
    if spec is None:
        return RedirectResponse("/eg", status_code=303)
    node = ed.get(kind, node_id)
    if node is None:
        return _shell(request, user, "없음",
                      join([h1("EG 노드가 없다"), p(a("← EG 조정", href="/eg"))]), status=404)

    editable = user.can("eg.edit")
    fields = []
    for key, (label_text, kind_of) in spec["fields"].items():
        val = node.get(key, "")
        if kind_of == "lines":
            text = "\n".join(val if isinstance(val, list) else [str(val)])
            ctl = textarea(key, text, id=f"f-{key}", rows="8", disabled=not editable)
        elif kind_of.startswith("choice:"):
            opts = [(o, o) for o in kind_of.split(":", 1)[1].split("|")]
            ctl = select(key, opts, str(val), id=f"f-{key}", disabled=not editable)
        else:
            ctl = input_(type="text", name=key, id=f"f-{key}", value=str(val),
                         disabled=not editable)
        fields.append(Safe(f'<div><label for="f-{h(key)}">{h(label_text)}</label>')
                      + ctl + Safe("</div>"))

    body = join([
        h1(node_id),
        p(spec["label"], class_="lede"),
        div("읽기 전용이다. 수정하려면 eg.edit 권한이 필요하다.", class_="flash warn")
        if not editable else None,
        Safe(f'<form class="stack" method="post" action="/eg/{h(kind)}/{h(node_id)}">'),
        csrf_field(request),
        *fields,
        Safe('<div><label for="f-reason">변경 사유 * (감사 로그에 남는다)</label>')
        + textarea("_reason", "", id="f-reason", rows="2", required=editable,
                   disabled=not editable,
                   placeholder="왜 이 행동 규칙을 바꾸나. 다음 사람이 읽는다.")
        + Safe("</div>") if editable else None,
        Safe('<div>') + button("검증하고 반영", type="submit") + Safe("</div>")
        if editable else Safe(""),
        Safe("</form>"),
        p(a("← EG 조정", href="/eg")),
    ])
    return _shell(request, user, node_id, body, current="/eg")


@require("eg.edit")
async def eg_node_post(request: Request) -> Response:
    user: User = request.state.user
    ed: EGEditor = request.app.state.egeditor
    kind = request.path_params["kind"]
    node_id = request.path_params["node_id"]
    spec = EDITABLE.get(kind)
    form_data = await request.form()

    if spec is None or not check_csrf(request, form_data):
        return _shell(request, user, "오류",
                      join([h1("잘못된 요청"), p(a("← EG 조정", href="/eg"))]), status=400)

    reason = str(form_data.get("_reason", "")).strip()[:500]
    if not reason:
        return _shell(request, user, "사유 필요",
                      join([h1("변경 사유가 필요하다"),
                            div("EG 변경은 전 에이전트의 행동을 바꾼다. "
                                "왜 바꾸는지 없이 반영하지 않는다.", class_="flash bad"),
                            p(a("← 돌아가기", href=f"/eg/{kind}/{node_id}"))]), status=400)

    changes: dict[str, Any] = {}
    for key, (_lbl, kind_of) in spec["fields"].items():
        if key not in form_data:
            continue
        raw = str(form_data.get(key, ""))
        if kind_of == "lines":
            changes[key] = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        else:
            changes[key] = raw.strip()

    try:
        res = ed.update(kind, node_id, changes, actor=user.username, reason=reason)
    except EGEditError as exc:
        _audit(request).write("eg.update", actor=user.username, target=node_id,
                              result="error", ip=_ip(request), reason=reason,
                              error=str(exc))
        return _shell(request, user, "실패",
                      join([h1("EG 변경 실패"), div(str(exc), class_="flash bad"),
                            p(a("← 돌아가기", href=f"/eg/{kind}/{node_id}"))]), status=400)

    _audit(request).write("eg.update", actor=user.username, target=node_id,
                          result="ok" if res.ok else "rejected", ip=_ip(request),
                          reason=reason, kind=kind, diff=res.diff,
                          snapshot=res.snapshot, error=res.error,
                          decision="edited" if res.ok else "", node_id=node_id)

    body = join([
        h1("EG 변경 " + ("반영됨" if res.ok else "거부됨")),
        div(
            "검증을 통과했고 EG 에 재주입됐다. **다음 작업부터** 해당 조직의 모든 "
            "에이전트가 이 규칙으로 움직인다."
            if res.ok else res.error,
            class_="flash " + ("ok" if res.ok else "bad"),
        ),
        h3("변경 내용"),
        diff_block(res.diff) if res.diff else p("(차이 없음)", class_="empty"),
        h3("검증 결과"),
        pre(res.validation or "(없음)"),
        h3("재주입") if res.reload else None,
        pre(res.reload) if res.reload else None,
        p(small(f"스냅샷: {res.snapshot}"), class_="dim"),
        div(a("← 노드로", href=f"/eg/{kind}/{node_id}", class_="btn ghost"),
            Safe(" "), a("EG 조정", href="/eg", class_="btn ghost"), class_="row"),
    ])
    return _shell(request, user, "EG 변경", body, current="/eg")


# ── 관제 연동 ────────────────────────────────────────────────────────────


@require("aoc.view")
async def aoc_view(request: Request) -> Response:
    user: User = request.state.user
    root = request.app.state.root
    office = request.app.state.office_url

    try:
        from dawn_aoc.console import build_state

        st = build_state(root, limit=40)
    except Exception as exc:                          # 관제가 죽어도 포털은 살아 있어야 한다
        return _shell(request, user, "관제", join([
            h1("관제"),
            div(f"관제 상태를 읽지 못했다: {type(exc).__name__}: {exc}", class_="flash bad"),
            p("P3 관제가 기동돼 있는지 확인하라 (make aoc)."),
        ]), current="/aoc")

    crit = [c for c in st["cases"] if c["severity"] in ("critical", "high")]
    body = join([
        h1("관제"),
        p("지금 에이전트들이 무엇을 하고 있는지. 상세는 픽셀 오피스에서 본다.", class_="lede"),
        div(a("픽셀 오피스 열기 →", href=office, class_="btn", target="_blank",
              rel="noopener"),
            Safe(" "),
            small(f"관제 콘솔: {office}", class_="dim"),
            class_="row"),

        h2("KPI"),
        table(
            tr(th("지표"), th("값"), th("목표"), th("표본"), th("")),
            *[tr(td(k["name"]), td(f'{k["value"]:.1f}{k["unit"]}'),
                 td(f'{"≤" if k["direction"] == "down" else "≥"}{k["target"]}{k["unit"]}'
                    if k["target"] is not None else "-"),
                 td(str(k["sample"])),
                 td(span("표본 없음", class_="tag") if k["meets_target"] is None
                    else span("충족", class_="tag ok") if k["meets_target"]
                    else span("미달", class_="tag bad")))
              for k in st["kpis"]],
        ),

        h2(f"열린 케이스 ({len(st['cases'])})"),
        table(
            tr(th("심각도"), th("에이전트"), th("내용"), th("권고")),
            *[tr(td(span(c["severity"], class_="tag " +
                         ("bad" if c["severity"] in ("critical", "high") else "warn"))),
                 td(small(c["agent_id"])),
                 td(c["title"][:70]),
                 td(small(", ".join(c["recommended"]) or "-", class_="dim")))
              for c in st["cases"][:20]],
        ) if st["cases"] else p("열린 케이스가 없다.", class_="empty"),

        div(f"고심각 케이스 {len(crit)}건 — 관제 콘솔에서 대응하라.", class_="flash bad")
        if crit else None,

        h2("에이전트"),
        table(
            tr(th("에이전트"), th("조직"), th("존"), th("자율화"), th("상태"), th("실행")),
            *[tr(td(x["name"]), td(small(x["eg_org"])), td(small(x["zone"])),
                 td(x["autonomy"]),
                 td(span(x["control_state"],
                         class_="tag " + ("ok" if x["control_state"] == "running" else "bad"))),
                 td(str(x["runs"])))
              for x in st["agents"]],
        ),
        p(small("킬 스위치 조작은 관제 CLI(aoc.control 권한)에서 한다. "
                "포털에서 원클릭으로 종료시키지 않는다 — 그건 되돌리기 어려운 행동이다."),
          class_="dim"),
    ])
    return _shell(request, user, "관제", body, current="/aoc")


# ── 공지·문서·일정·디렉터리 ──────────────────────────────────────────────


# ── 작업 지시 (P7) ───────────────────────────────────────────────────────
#
# 내부 접수 경로. 홈페이지(외부)와 **같은 도메인**에 쓰고 **같은 규칙**을 쓴다
# (`dawn_core.workintake`). 다른 점은 둘뿐이다:
#   · 요청자가 로그인 계정이다 (외부는 이메일)
#   · 업무 DB 에 직접 쓴다 (외부는 파일로 떨구고 사내가 당겨 온다 — P4 격리)


@require("portal.view")
async def orders(request: Request) -> Response:
    from dawn_biz.store import BizStore
    from dawn_core import workintake

    user: User = request.state.user
    root = _root(request)
    store = BizStore(root)
    rows = store.work_orders(limit=100)
    cards = []
    for r in rows:
        chain = workintake.approval_chain(
            root, business=r["business"], infra_tier=r["infra_tier"],
            division=r["division"], origin=r["origin"])
        mine = any(c["portal_user"] == user.username for c in chain)
        cards.append(div(
            Safe('<b>') + a(f'#{r["id"]} {r["title"]}', href=f'/orders/{r["id"]}')
            + Safe('</b>')
            + Safe(" ") + span(r["status"], class_="tag")
            + (Safe(" ") + span("내 결재", class_="tag acc") if mine else Safe("")),
            small(f'{r["origin"]} · {r["business"] or "내부 지원"} · {r["division"]} · '
                  f'환경 {r["infra_tier"]} · {r["requester"] or "—"}', class_="dim"),
            small("결재: " + (" → ".join(f'{c["role"]}({c["portal_user"]})' for c in chain)
                             or "라인 없음"), class_="dim"),
            class_="card"))
    body = join([
        h1("작업 지시"),
        p("접수 → 결재 → 환경 준비 → 착수 → 검수 → 완료. "
          "결재 라인은 사업·인프라 등급·민감도에서 파생한다.", class_="dim"),
        _order_form(request) if user.can("portal.post") else None,
        join(cards) if cards else p("작업 지시가 없다.", class_="empty"),
    ])
    return _shell(request, user, "작업 지시", body, current="/orders")


def _order_form(request: Request) -> Safe:
    from dawn_core import workintake

    root = _root(request)
    cs = workintake.choices(root, include_planned=True)
    # 사업 없이도 접수된다 — 경리·문의 응대·시스템 운영은 수익 사업이 아니지만
    # 누군가는 해야 하는 일이다. 사업을 필수로 두면 경영관리부는 지시를 못 만든다.
    opts = [Safe('<option value="">— 사업 없음 (내부 지원 업무) —</option>')]
    for c in cs:
        opts.append(Safe(f'<option value="{h(c.id)}">') + Safe(h(c.name))
                    + Safe(f' — 환경 {h(", ".join(c.tiers))}</option>'))
    divs = [Safe('<option value="">— 사업을 골랐으면 비워 둔다 —</option>')]
    divs += [Safe(f'<option value="{h(did)}">') + Safe(h(name)) + Safe("</option>")
             for did, name in workintake.division_choices(root)]
    tiers = [Safe(f'<option value="{t}">') + Safe(h(lbl)) + Safe("</option>")
             for t, (lbl, _d) in workintake.TIER_LABEL.items()]
    return Safe('<details class="card"><summary>새 작업 지시</summary>'
                '<form class="stack" method="post" action="/orders" style="margin-top:10px">'
                ) + join([
        csrf_field(request),
        Safe('<div><label for="o-t">제목</label>')
        + input_(type="text", id="o-t", name="title", required=True, maxlength="200")
        + Safe("</div>"),
        Safe('<div><label for="o-biz">사업</label><select id="o-biz" name="business">')
        + join(opts) + Safe("</select></div>"),
        Safe('<div><label for="o-div">담당 본부</label>'
             '<select id="o-div" name="division">') + join(divs)
        + Safe("</select></div>"),
        Safe('<div><label for="o-tier">필요한 환경</label>')
        + Safe('<select id="o-tier" name="infra_tier" required>') + join(tiers)
        + Safe("</select></div>"),
        Safe('<div><label for="o-b">내용</label>')
        + textarea("body", "", id="o-b", rows="5") + Safe("</div>"),
        Safe('<div><button type="submit">접수</button></div>'),
    ]) + Safe("</form></details>")


@require("portal.view")
async def orders_post(request: Request) -> Response:
    from dawn_biz.store import BizStore
    from dawn_core import workintake

    user: User = request.state.user
    if not user.can("portal.post"):
        return _forbidden(request, user, "portal.post")
    root = _root(request)
    form = await request.form()
    if not check_csrf(request, form):
        return RedirectResponse("/orders", status_code=303)
    title = str(form.get("title", "")).strip()[:200]
    business = str(form.get("business", ""))
    tier = str(form.get("infra_tier", "none"))
    body_text = str(form.get("body", ""))[:8000]
    try:
        division, tier = workintake.validate(
            root, business=business, infra_tier=tier,
            division=str(form.get("division", "")))
        if not title:
            raise ValueError("제목이 비었다")
    except ValueError as e:
        return _shell(request, user, "작업 지시",
                      join([h1("작업 지시"), div(str(e), class_="flash bad"),
                            p(a("← 돌아가기", href="/orders"))]), current="/orders")

    store = BizStore(root)
    wid = store.add_work_order(
        title=title, body=body_text, origin="internal", requester=user.username,
        requester_org=user.org, business=business, division=division, infra_tier=tier)
    store.set_work_order_status(wid, "pending_approval")
    _audit(request).write("portal.order.create", actor=user.username,
                          target=f"work_order:{wid}", ip=_ip(request),
                          business=business, tier=tier, division=division)
    return RedirectResponse("/orders", status_code=303)


@require("portal.view")
async def order_detail(request: Request) -> Response:
    """작업 지시 1건 — 결재는 여기서 한다.

    승인 권한은 능력(capability)이 아니라 **결재 라인**이 정한다. `hitl.approve` 가
    있어도 이 작업의 차례가 아니면 못 누른다 — 결재 라인이 형식만 남지 않게.
    """
    from dawn_biz.store import BizStore
    from dawn_core import workintake

    user: User = request.state.user
    root = _root(request)
    wid = int(request.path_params["wid"])
    store = BizStore(root)
    r = store.work_order(wid)
    if r is None:
        return _shell(request, user, "작업 지시",
                      join([h1("작업 지시"), p("없는 작업 지시다.", class_="empty")]),
                      current="/orders", status=404)

    chain = workintake.approval_chain(root, business=r["business"],
                                      infra_tier=r["infra_tier"],
                                      division=r["division"], origin=r["origin"])
    decided = store.work_order_approvals(wid)
    nxt = workintake.next_approver(chain, decided)
    mine = bool(nxt and nxt["portal_user"] == user.username)

    steps = []
    for i, c in enumerate(chain):
        done = next((d for d in decided if d.get("step") == i + 1), None)
        mark = ("✔ 승인" if done["decision"] == "approved" else "✘ 반려") if done             else ("▶ 대기" if nxt and nxt["portal_user"] == c["portal_user"] else "· 예정")
        steps.append(div(
            Safe(f'<b>{i + 1}. {h(c["role"])}</b> ') + span(c["portal_user"], class_="tag")
            + Safe(" ") + span(mark, class_="tag acc" if done else "tag"),
            small(c["reason"] + (f' · {done["at"][:16].replace("T", " ")}' if done else ""),
                  class_="dim"),
            small(done.get("note", ""), class_="dim") if done and done.get("note") else None,
            class_="card"))

    form = None
    if mine:
        form = Safe('<form class="stack" method="post" '
                    f'action="/orders/{wid}" style="margin-top:10px">') + join([
            csrf_field(request),
            Safe('<div><label for="d-n">의견</label>')
            + textarea("note", "", id="d-n", rows="3") + Safe("</div>"),
            Safe('<div><button type="submit" name="decision" value="approve">승인</button> '
                 '<button type="submit" name="decision" value="reject">반려</button></div>'),
        ]) + Safe("</form>")

    body = join([
        h1(f'#{r["id"]} {r["title"]}'),
        div(join([
            small(f'상태 {r["status"]} · {r["origin"]} · '
                  f'{r["business"] or "내부 지원 (사업 없음)"} · '
                  f'{r["division"]} · 환경 {r["infra_tier"]}', class_="dim"),
            small(f'요청자 {r["requester"] or "—"} ({r["requester_org"] or "—"}) · '
                  f'{r["created_at"][:16].replace("T", " ")}', class_="dim"),
            markdownish(r["body"]) if r["body"] else None,
        ]), class_="card"),
        _order_infra(root, r),
        h1("결재"),
        join(steps) if steps else p("결재 라인이 없다 — division.yaml 에 lead 가 없다.",
                                    class_="empty"),
        form,
        p(a("← 작업 지시", href="/orders")),
    ])
    return _shell(request, user, f'작업 지시 #{wid}', body, current="/orders")


def _order_infra(root, r) -> Safe:
    """이 결재가 무엇을 점유하게 되는지 (P7 DoD-3).

    결재자가 "승인"을 누르기 전에 **무엇이 나가는지** 보여야 한다. 등급만 적혀
    있으면 `server` 가 무슨 뜻인지 모르는 채 누르게 된다.
    """
    from dawn_core import workintake
    from dawn_core.infrapool import PoolError, allocation_of, plan

    if r["infra_tier"] == "none":
        return Safe("")
    a = allocation_of(root, r["id"])
    if a is None:
        zone = r["zone"] or workintake.zone_for(root, r["division"])
        try:
            a = plan(root, order_id=r["id"], tier=r["infra_tier"], zone=zone,
                     business=r["business"])
            head = "승인하면 잡히는 것"
        except PoolError as e:
            return div(join([Safe("<b>인프라</b>"),
                             small(f"할당할 수 없다 — {e}", class_="dim")]),
                       class_="card")
    else:
        head = {"ready": "잡힌 자원", "waiting": "준비대기",
                "released": "반납됨"}.get(a.state, a.state)
    what = a.host_id or a.container or "—"
    return div(join([
        Safe("<b>인프라</b> ") + span(head, class_="tag"),
        small(f'{workintake.TIER_LABEL.get(a.tier, (a.tier, ""))[0]} · {what}'
              + (f' · {a.network}' if a.network else "")
              + (f' · 존 {a.zone}' if a.zone else ""), class_="dim"),
        small(a.reason, class_="dim") if a.reason else None,
        small("집행: " + a.command, class_="dim") if a.command else None,
    ]), class_="card")


@require("portal.view")
async def order_decide(request: Request) -> Response:
    from dawn_biz.store import BizStore
    from dawn_core import workintake

    user: User = request.state.user
    root = _root(request)
    wid = int(request.path_params["wid"])
    form = await request.form()
    if not check_csrf(request, form):
        return RedirectResponse(f"/orders/{wid}", status_code=303)

    store = BizStore(root)
    r = store.work_order(wid)
    if r is None:
        return RedirectResponse("/orders", status_code=303)
    chain = workintake.approval_chain(root, business=r["business"],
                                      infra_tier=r["infra_tier"],
                                      division=r["division"], origin=r["origin"])
    decided = store.work_order_approvals(wid)
    approve = str(form.get("decision", "")) == "approve"
    try:
        entry = workintake.decide(chain, decided, actor=user.username, approve=approve,
                                  note=str(form.get("note", "")), at=_now())
    except (PermissionError, ValueError) as e:
        return _shell(request, user, "결재 거부",
                      join([h1("결재할 수 없다"), div(str(e), class_="flash bad"),
                            p(a("← 돌아가기", href=f"/orders/{wid}"))]),
                      current="/orders", status=403)

    after = [*decided, entry]
    status = "rejected" if not approve else (
        "approved" if workintake.next_approver(chain, after) is None else "pending_approval")
    store.append_work_order_approval(wid, entry, status=status)
    # note 를 함께 남긴다 — 결재 사유는 이미 받고 있었는데 감사에 안 넘겼다.
    # 사유 없는 판단은 P8 이 세지 않는다(결정만으로는 왜 그랬는지 모른다).
    _audit(request).write("portal.order.decide", actor=user.username,
                          target=f"work_order:{wid}", ip=_ip(request),
                          decision=entry["decision"], step=entry["step"],
                          status=status, note=str(form.get("note", ""))[:2000])
    return RedirectResponse(f"/orders/{wid}", status_code=303)


@require("portal.view")
async def notices(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    items = store.notices(limit=50)
    body = join([
        h1("공지"),
        _notice_form(request) if user.can("portal.post") else None,
        join([div(
            Safe(f'<b>{h(n["title"])}</b>')
            + (Safe(" ") + span("고정", class_="tag acc") if n["pinned"] else Safe("")),
            markdownish(n["body"]),
            small(f'{n["author"]} · {n["org"]} · {n["created_at"][:16].replace("T", " ")}',
                  class_="dim"),
            class_="card",
        ) for n in items]) if items else p("공지가 없다.", class_="empty"),
    ])
    return _shell(request, user, "공지", body, current="/notices")


def _notice_form(request: Request) -> Safe:
    return Safe('<details class="card"><summary>새 공지</summary>'
                '<form class="stack" method="post" action="/notices" style="margin-top:10px">'
                ) + join([
        csrf_field(request),
        Safe('<div><label for="n-t">제목</label>')
        + input_(type="text", id="n-t", name="title", required=True, maxlength="200")
        + Safe("</div>"),
        Safe('<div><label for="n-b">내용</label>')
        + textarea("body", "", id="n-b", rows="6") + Safe("</div>"),
        Safe('<div class="chk"><input type="checkbox" id="n-p" name="pinned" value="1">'
             '<label for="n-p" style="margin:0">상단 고정</label></div>'),
        Safe("<div>") + button("등록", type="submit") + Safe("</div>"),
    ]) + Safe("</form></details>")


@require("portal.post")
async def notices_post(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return RedirectResponse("/notices", status_code=303)
    title = str(form_data.get("title", "")).strip()[:200]
    if title:
        nid = store.add_notice(title=title, body=str(form_data.get("body", ""))[:20000],
                               author=user.username, org=user.org,
                               pinned=bool(form_data.get("pinned")))
        _audit(request).write("portal.notice.create", actor=user.username,
                              target=f"notice:{nid}", ip=_ip(request), title=title)
    return RedirectResponse("/notices", status_code=303)


def _max_level(user: User) -> str:
    """이 사람이 볼 수 있는 최고 문서 등급.

    L3(인사·재무·개인정보)는 해당 조직이거나 admin 만. 등급을 능력으로 안 쓰고
    **조직**으로 정하는 이유: L3 는 조직의 책임 범위지 개인의 직급이 아니다.
    """
    if user.can("admin"):
        return "L3"
    if user.org in {"org:hr", "org:fin", "org:ga", "org:mgmt"}:
        return "L3"
    if user.can("eg.edit") or user.can("aoc.view"):
        return "L2"
    return "L1"


@require("portal.view")
async def documents(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    cap = _max_level(user)
    items = store.documents(max_level=cap, limit=100)
    body = join([
        h1("문서"),
        p(f"당신은 {cap} 등급까지 볼 수 있다 ({SECURITY_LEVELS[cap]}). "
          f"상위 등급 문서는 제목도 나오지 않는다 — 제목이 곧 정보인 경우가 있다.",
          class_="lede"),
        _document_form(request, cap) if user.can("portal.post") else None,
        table(
            tr(th("등급"), th("제목"), th("작성"), th("갱신")),
            *[tr(td(span(d["security_level"],
                         class_="tag " + ("bad" if d["security_level"] == "L3"
                                          else "warn" if d["security_level"] == "L2" else ""))),
                 td(a(d["title"], href=f'/documents/{d["id"]}')),
                 td(small(f'{d["author"]} · {d["org"]}', class_="dim")),
                 td(small(d["updated_at"][:16].replace("T", " "), class_="dim")))
              for d in items],
        ) if items else p("문서가 없다.", class_="empty"),
    ])
    return _shell(request, user, "문서", body, current="/documents")


def _document_form(request: Request, cap: str) -> Safe:
    opts = [(k, f"{k} — {v}") for k, v in SECURITY_LEVELS.items()
            if LEVEL_RANK[k] <= LEVEL_RANK[cap]]
    return Safe('<details class="card"><summary>새 문서</summary>'
                '<form class="stack" method="post" action="/documents" style="margin-top:10px">'
                ) + join([
        csrf_field(request),
        Safe('<div><label for="d-t">제목</label>')
        + input_(type="text", id="d-t", name="title", required=True, maxlength="200")
        + Safe("</div>"),
        Safe('<div><label for="d-l">보안 등급</label>')
        + select("security_level", opts, "L1", id="d-l") + Safe("</div>"),
        Safe('<div><label for="d-b">본문</label>')
        + textarea("body", "", id="d-b", rows="10") + Safe("</div>"),
        Safe("<div>") + button("저장", type="submit") + Safe("</div>"),
    ]) + Safe("</form></details>")


@require("portal.post")
async def documents_post(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return RedirectResponse("/documents", status_code=303)
    level = str(form_data.get("security_level", "L1"))
    cap = _max_level(user)
    if LEVEL_RANK.get(level, 9) > LEVEL_RANK[cap]:
        # 자기가 못 읽는 등급으로 문서를 만들 수 없다 (쓰고 나서 못 여는 문서가 생긴다)
        level = cap
    title = str(form_data.get("title", "")).strip()[:200]
    if title:
        did = store.add_document(title=title, body=str(form_data.get("body", ""))[:100000],
                                 author=user.username, org=user.org, security_level=level)
        _audit(request).write("portal.document.create", actor=user.username,
                              target=f"document:{did}", ip=_ip(request),
                              title=title, security_level=level)
    return RedirectResponse("/documents", status_code=303)


@require("portal.view")
async def document_detail(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    doc = store.document(int(request.path_params["doc_id"]), max_level=_max_level(user))
    if doc is None:
        _audit(request).write("portal.document.denied", actor=user.username,
                              target=f'document:{request.path_params["doc_id"]}',
                              result="denied", ip=_ip(request))
        return _shell(request, user, "없음", join([
            h1("문서를 볼 수 없다"),
            div("없는 문서이거나, 당신의 등급으로는 열람할 수 없다. "
                "어느 쪽인지는 알려주지 않는다 — 존재 여부도 정보다.", class_="flash bad"),
            p(a("← 문서", href="/documents")),
        ]), status=404)
    body = join([
        h1(doc["title"]),
        p(span(doc["security_level"], class_="tag"), " ",
          small(f'{doc["author"]} · {doc["org"]} · '
                f'{doc["updated_at"][:16].replace("T", " ")}', class_="dim")),
        div(markdownish(doc["body"]), class_="card"),
        p(a("← 문서", href="/documents")),
    ])
    return _shell(request, user, doc["title"], body, current="/documents")


@require("portal.view")
async def calendar(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    items = store.events(limit=100)
    body = join([
        h1("일정"),
        _event_form(request) if user.can("portal.post") else None,
        table(
            tr(th("시작"), th("종료"), th("일정"), th("장소"), th("등록")),
            *[tr(td(e["starts_at"].replace("T", " ")),
                 td(small(e["ends_at"].replace("T", " ") or "-", class_="dim")),
                 td(e["title"], Safe("<br>"), small(e["body"][:80], class_="dim")),
                 td(e["location"] or "-"), td(small(e["author"], class_="dim")))
              for e in items],
        ) if items else p("일정이 없다.", class_="empty"),
    ])
    return _shell(request, user, "일정", body, current="/calendar")


def _event_form(request: Request) -> Safe:
    return Safe('<details class="card"><summary>새 일정</summary>'
                '<form class="stack" method="post" action="/calendar" style="margin-top:10px">'
                ) + join([
        csrf_field(request),
        Safe('<div><label for="e-t">제목</label>')
        + input_(type="text", id="e-t", name="title", required=True, maxlength="200")
        + Safe("</div>"),
        Safe('<div><label for="e-s">시작</label>')
        + input_(type="datetime-local", id="e-s", name="starts_at", required=True)
        + Safe("</div>"),
        Safe('<div><label for="e-e">종료</label>')
        + input_(type="datetime-local", id="e-e", name="ends_at") + Safe("</div>"),
        Safe('<div><label for="e-l">장소</label>')
        + input_(type="text", id="e-l", name="location", maxlength="120") + Safe("</div>"),
        Safe('<div><label for="e-b">메모</label>')
        + textarea("body", "", id="e-b", rows="3") + Safe("</div>"),
        Safe("<div>") + button("등록", type="submit") + Safe("</div>"),
    ]) + Safe("</form></details>")


@require("portal.post")
async def calendar_post(request: Request) -> Response:
    user: User = request.state.user
    store: Store = request.app.state.store
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return RedirectResponse("/calendar", status_code=303)
    title = str(form_data.get("title", "")).strip()[:200]
    starts = str(form_data.get("starts_at", "")).strip()[:32]
    if title and starts:
        eid = store.add_event(title=title, starts_at=starts,
                              ends_at=str(form_data.get("ends_at", ""))[:32],
                              location=str(form_data.get("location", ""))[:120],
                              body=str(form_data.get("body", ""))[:5000],
                              author=user.username, org=user.org)
        _audit(request).write("portal.event.create", actor=user.username,
                              target=f"event:{eid}", ip=_ip(request), title=title)
    return RedirectResponse("/calendar", status_code=303)


@require("portal.view")
async def directory(request: Request) -> Response:
    """구성원 디렉터리 — **사람과 에이전트를 한 화면에.**

    이 회사는 둘이 같이 일한다. 조직도를 사람만으로 그리면 실제 인력의 절반이
    보이지 않는다.
    """
    user: User = request.state.user
    users: UserStore = request.app.state.users
    reg, eg = request.app.state.registry, request.app.state.eg

    people = users.list(tenant=user.tenant)
    by_org: dict[str, dict[str, list]] = {}
    for pr in people:
        by_org.setdefault(pr.org, {"people": [], "agents": []})["people"].append(pr)
    for aid, ag in sorted(reg.agents.items()):
        team = reg.teams.get(ag.team_id)
        org = team.data.get("eg_org", "") if team else ""
        by_org.setdefault(org, {"people": [], "agents": []})["agents"].append((aid, ag))

    blocks = []
    for org in sorted(by_org):
        g = by_org[org]
        blocks.append(div(
            h3(_org_name(reg, eg, org)),
            table(
                tr(th(""), th("이름"), th("역할"), th("상세")),
                *[tr(td(span("사람", class_="tag")), td(pr.name),
                     td(pr.title or "-"),
                     td(small(", ".join(pr.capabilities) or "portal.view", class_="dim")))
                  for pr in g["people"]],
                *[tr(td(span("에이전트", class_="tag acc")), td(ag.data["name"]),
                     td(ag.data.get("persona", "-")),
                     td(small(f'{ag.data.get("autonomy", "")} · '
                              f'{ag.data.get("status", "")} · zone {ag.data.get("zone", "-")}',
                              class_="dim")))
                  for _aid, ag in g["agents"]],
            ),
            class_="card",
        ))

    body = join([
        h1("구성원"),
        p(f"사람 {len(people)}명 · 에이전트 {len(reg.agents)}기. "
          f"조직도는 EG OrgUnit 에서 렌더한다 — 별도 조직 데이터를 만들지 않는다.",
          class_="lede"),
        *blocks,
    ])
    return _shell(request, user, "구성원", body, current="/directory")


# ── 감사 로그 ────────────────────────────────────────────────────────────


@require("portal.view")
async def audit_view(request: Request) -> Response:
    user: User = request.state.user
    prefix = request.query_params.get("action", "")
    # 관리자·EG 편집자·승인자가 아니면 자기 이력만 본다
    self_only = not (user.can("admin") or user.can("eg.edit") or user.can("hitl.approve"))
    recs = _audit(request).tail(200, action_prefix=prefix,
                                actor=user.username if self_only else "")
    rows = [tr(th("시각"), th("행위"), th("사람"), th("대상"), th("결과"), th("상세"))]
    for r in recs:
        d = r.get("detail", {})
        detail = "; ".join(f"{k}={v}" for k, v in d.items()
                           if k in ("reason", "skill", "severity", "capability", "title"))
        rows.append(tr(
            td(small(r["at"].replace("T", " "), class_="mono")),
            td(code(r["action"])),
            td(r["actor"]),
            td(small(r.get("target", ""), class_="dim")),
            td(span(r["result"], class_="tag " +
                    ("ok" if r["result"] in ("ok", "approved") else "bad"))),
            td(small(detail[:110], class_="dim")),
        ))
    body = join([
        h1("감사 로그"),
        p("append-only. 지우는 API 는 없다.", class_="lede"),
        div("당신은 자기 이력만 볼 수 있다.", class_="flash warn") if self_only else None,
        div(*[a(t, href=f"/audit?action={v}",
                class_="btn ghost" if prefix != v else "btn")
              for v, t in [("", "전체"), ("auth.", "로그인"), ("hitl.", "승인"),
                           ("eg.", "EG 변경"), ("access.", "접근 거부")]], class_="row"),
        table(*rows) if recs else p("기록이 없다.", class_="empty"),
    ])
    return _shell(request, user, "감사 로그", body, current="/audit")


# ── 판단 이력 (P8 수집 계층) ─────────────────────────────────────────────


def _judgment_store(request: Request):
    """판단은 EG 에 산다. 없으면 None — 화면이 조용히 비면 안 되므로 알려준다."""
    return request.app.state.eg


@require("portal.view")
async def judgments_view(request: Request) -> Response:
    """내가 내린 판단들.

    **기본이 본인 것만이다.** 한 사람의 판단 모음은 그 사람의 판단 모델이고,
    그건 개인정보(L3)다. 남의 것을 훑어보는 화면이 아니다 — 감사 로그(`/audit`)가
    "무슨 일이 있었나"를 답하는 자리고, 여기는 "나에 대해 무엇이 쌓였나"를
    본인이 확인하는 자리다.
    """
    from dawn_core.eg import judgment as jd

    user: User = request.state.user
    eg = _judgment_store(request)
    if eg is None:
        return _shell(request, user, "판단 이력", join([
            h1("판단 이력"),
            div("EG 가 없다 — make eg-load 후에 볼 수 있다.", class_="flash bad"),
        ]), current="/judgments")

    rows_data = jd.judgments(eg, actor=user.username)
    rows = [tr(th("시각"), th("경로"), th("결정"), th("대상"), th("사유"))]
    for n in rows_data:
        c = n.content
        sit = c.get("situation") or {}
        tgt = ", ".join(sit.get("assets") or []) or c.get("target", "")
        rows.append(tr(
            td(small(c.get("at", "").replace("T", " ")[:19], class_="mono")),
            td(code(c.get("source", ""))),
            td(span(c.get("decision", ""), class_="tag " +
                    ("ok" if c.get("decision") in ("approved", "edited") else "bad"))),
            td(small(tgt, class_="dim")),
            td(small(c.get("reason", "")[:120], class_="dim")),
        ))

    body = join([
        h1("내 판단 이력"),
        p("사유가 붙는 결정을 내릴 때마다 자동으로 쌓인다. 따로 입력하지 않는다.",
          class_="lede"),
        div("이 기록은 개인정보(L3)다. 클라우드 모델에 보내지 않는다. "
            "언제든 지울 수 있고, 지워도 감사 로그는 남는다 — 그건 법적 기록이다.",
            class_="flash warn"),
        table(*rows) if rows_data else p(
            "아직 없다. 승인·결재·EG 조정을 사유와 함께 하면 여기 쌓인다.",
            class_="empty"),
        Safe(f'<form method="post" action="/judgments" '
             f'onsubmit="return confirm(\'판단 {len(rows_data)}건을 지운다. '
             f'되돌릴 수 없다.\')">') if rows_data else None,
        csrf_field(request) if rows_data else None,
        Safe('<button class="btn bad" type="submit">내 판단 이력 전부 지우기</button>'
             '</form>') if rows_data else None,
    ])
    return _shell(request, user, "판단 이력", body, current="/judgments")


@require("portal.view")
async def judgments_post(request: Request) -> Response:
    """본인 판단 삭제. **남의 것은 지울 수 없다** — actor 를 폼에서 받지 않는다."""
    from dawn_core.eg import judgment as jd

    user: User = request.state.user
    form = await request.form()
    if not check_csrf(request, form):
        return RedirectResponse("/judgments", status_code=303)

    eg = _judgment_store(request)
    n = jd.forget(eg, user.username) if eg is not None else 0
    _audit(request).write("judgment.forget", actor=user.username,
                          target=user.username, result="ok", ip=_ip(request),
                          count=n)
    return _shell(request, user, "판단 이력", join([
        h1("지웠다"),
        div(f"판단 {n}건을 삭제했다. 감사 로그는 그대로 남는다.", class_="flash ok"),
        p(a("← 판단 이력", href="/judgments")),
    ]), current="/judgments")


# ── 계정 관리 ────────────────────────────────────────────────────────────


@require("admin")
async def admin_users(request: Request) -> Response:
    user: User = request.state.user
    users: UserStore = request.app.state.users
    eg = request.app.state.eg
    orgs = sorted({n.id for n in (eg.nodes(type="OrgUnit") if eg else [])}) or ["org:dawn"]

    rows = [tr(th("계정"), th("이름"), th("조직"), th("권한"), th("상태"), th(""))]
    for u in users.list(tenant=user.tenant):
        rows.append(tr(
            td(code(u.username)), td(u.name), td(small(u.org, class_="dim")),
            td(small(", ".join(u.capabilities), class_="dim")),
            td(span("사용 중지" if u.disabled else "정상",
                    class_="tag " + ("bad" if u.disabled else "ok"))),
            td(a("권한 편집", href=f"/admin/users/{u.username}")),
        ))

    body = join([
        h1("계정"),
        p("권한은 조직 × 능력이다. 능력을 준다고 전사 권한이 생기지 않는다 — "
          "승인은 여전히 자기 조직 트리 안에서만 가능하다.", class_="lede"),
        table(*rows),
        h2("새 계정"),
        Safe('<form class="stack" method="post" action="/admin/users">'),
        csrf_field(request),
        Safe('<div><label for="a-u">계정</label>')
        + input_(type="text", id="a-u", name="username", required=True, maxlength="64")
        + Safe("</div>"),
        Safe('<div><label for="a-n">이름</label>')
        + input_(type="text", id="a-n", name="name", required=True, maxlength="80")
        + Safe("</div>"),
        Safe('<div><label for="a-o">조직 (EG OrgUnit)</label>')
        + select("org", [(o, o) for o in orgs], "org:dawn", id="a-o") + Safe("</div>"),
        Safe('<div><label for="a-t">직함</label>')
        + input_(type="text", id="a-t", name="title", maxlength="80") + Safe("</div>"),
        Safe('<div><label for="a-p">임시 비밀번호 (8자 이상)</label>')
        + input_(type="password", id="a-p", name="password", required=True, minlength="8")
        + Safe("</div>"),
        Safe("<div><label>능력</label>") + _cap_checks([]) + Safe("</div>"),
        Safe("<div>") + button("만들기", type="submit") + Safe("</div>"),
        Safe("</form>"),
    ])
    return _shell(request, user, "계정", body, current="/admin/users")


def _cap_checks(selected: list[str]) -> Safe:
    out = []
    for cap, desc in CAPABILITIES.items():
        checked = ' checked' if cap in selected else ""
        out.append(
            f'<div class="chk"><input type="checkbox" id="c-{h(cap)}" name="capabilities" '
            f'value="{h(cap)}"{checked}><label for="c-{h(cap)}" style="margin:0">'
            f'<code>{h(cap)}</code> — {h(desc)}</label></div>'
        )
    return Safe("".join(out))


@require("admin")
async def admin_users_post(request: Request) -> Response:
    user: User = request.state.user
    users: UserStore = request.app.state.users
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return RedirectResponse("/admin/users", status_code=303)
    try:
        new = users.create(
            str(form_data.get("username", ""))[:64],
            str(form_data.get("password", "")),
            name=str(form_data.get("name", ""))[:80],
            org=str(form_data.get("org", "org:dawn")),
            title=str(form_data.get("title", ""))[:80],
            tenant=user.tenant,
            capabilities=[str(x) for x in form_data.getlist("capabilities")] or ["portal.view"],
        )
    except ValueError as exc:
        return _shell(request, user, "실패", join([
            h1("계정을 만들지 못했다"), div(str(exc), class_="flash bad"),
            p(a("← 계정", href="/admin/users")),
        ]), status=400)
    _audit(request).write("admin.user.create", actor=user.username, target=new.username,
                          ip=_ip(request), org=new.org, capabilities=new.capabilities)
    return RedirectResponse("/admin/users", status_code=303)


@require("admin")
async def admin_user_edit(request: Request) -> Response:
    user: User = request.state.user
    users: UserStore = request.app.state.users
    target = users.get(request.path_params["username"])
    if target is None:
        return RedirectResponse("/admin/users", status_code=303)
    body = join([
        h1(target.username),
        p(f"{target.name} · {target.org}", class_="lede"),
        Safe(f'<form class="stack" method="post" action="/admin/users/{h(target.username)}">'),
        csrf_field(request),
        Safe("<div><label>능력</label>") + _cap_checks(target.capabilities) + Safe("</div>"),
        Safe('<div class="chk"><input type="checkbox" id="dis" name="disabled" value="1"'
             + (" checked" if target.disabled else "") +
             '><label for="dis" style="margin:0">사용 중지</label></div>'),
        Safe("<div>") + button("저장", type="submit") + Safe("</div>"),
        Safe("</form>"),
        p(a("← 계정", href="/admin/users")),
    ])
    return _shell(request, user, target.username, body, current="/admin/users")


@require("admin")
async def admin_user_post(request: Request) -> Response:
    user: User = request.state.user
    users: UserStore = request.app.state.users
    username = request.path_params["username"]
    form_data = await request.form()
    if not check_csrf(request, form_data):
        return RedirectResponse("/admin/users", status_code=303)
    caps = [str(x) for x in form_data.getlist("capabilities")] or ["portal.view"]
    disabled = bool(form_data.get("disabled"))
    if username == user.username and ("admin" not in caps or disabled):
        return _shell(request, user, "거부", join([
            h1("자기 자신의 관리 권한은 뺄 수 없다"),
            div("마지막 관리자가 스스로를 잠그면 아무도 못 들어온다.", class_="flash bad"),
            p(a("← 계정", href="/admin/users")),
        ]), status=400)
    try:
        users.set_capabilities(username, caps)
        users.set_disabled(username, disabled)
    except (KeyError, ValueError) as exc:
        return _shell(request, user, "실패", join([
            h1("변경 실패"), div(str(exc), class_="flash bad"),
            p(a("← 계정", href="/admin/users")),
        ]), status=400)
    _audit(request).write("admin.user.update", actor=user.username, target=username,
                          ip=_ip(request), capabilities=caps, disabled=disabled)
    return RedirectResponse("/admin/users", status_code=303)


# ── 진단 ─────────────────────────────────────────────────────────────────


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "app": "groupware"})


@require("portal.view")
async def whoami(request: Request) -> JSONResponse:
    return JSONResponse(request.state.user.public())


routes = [
    Route("/login", login, methods=["GET"]),
    Route("/login", login_post, methods=["POST"]),
    Route("/logout", logout),
    Route("/", dashboard),
    Route("/approvals", approvals),
    Route("/approvals/{aid}", approval_detail, methods=["GET"]),
    Route("/approvals/{aid}", approval_decide, methods=["POST"]),
    Route("/control", control_index),
    Route("/control/agents", control_agents, methods=["POST"]),
    Route("/control/{kind}/{ident}", control_edit, methods=["GET"]),
    Route("/control/{kind}/{ident}", control_save, methods=["POST"]),
    Route("/eg", eg_index),
    Route("/eg/{kind}/{node_id}", eg_node, methods=["GET"]),
    Route("/eg/{kind}/{node_id}", eg_node_post, methods=["POST"]),
    Route("/aoc", aoc_view),
    Route("/orders", orders, methods=["GET"]),
    Route("/orders", orders_post, methods=["POST"]),
    Route("/orders/{wid:int}", order_detail, methods=["GET"]),
    Route("/orders/{wid:int}", order_decide, methods=["POST"]),
    Route("/notices", notices, methods=["GET"]),
    Route("/notices", notices_post, methods=["POST"]),
    Route("/documents", documents, methods=["GET"]),
    Route("/documents", documents_post, methods=["POST"]),
    Route("/documents/{doc_id:int}", document_detail),
    Route("/calendar", calendar, methods=["GET"]),
    Route("/calendar", calendar_post, methods=["POST"]),
    Route("/directory", directory),
    Route("/audit", audit_view),
    Route("/judgments", judgments_view, methods=["GET"]),
    Route("/judgments", judgments_post, methods=["POST"]),
    Route("/admin/users", admin_users, methods=["GET"]),
    Route("/admin/users", admin_users_post, methods=["POST"]),
    Route("/admin/users/{username}", admin_user_edit, methods=["GET"]),
    Route("/admin/users/{username}", admin_user_post, methods=["POST"]),
    Route("/healthz", health),
    Route("/api/whoami", whoami),
]

__all__ = ["csrf_token", "current_user", "require", "routes"]
