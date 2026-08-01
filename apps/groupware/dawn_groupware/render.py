"""HTML 렌더 — 템플릿 엔진 없이.

Jinja2 를 안 쓴 이유: 이 앱의 화면은 20개 남짓이고, 템플릿 엔진을 넣으면
**이스케이프 책임이 템플릿 작성자에게 흩어진다.** 여기서는 `h()` 를 통과하지 않은
문자열이 HTML 에 들어갈 방법이 없게 만든다 — `Safe` 로 감싸야만 원문이 나간다.

XSS 방어를 "조심해서 쓰기"에 맡기지 않고 타입으로 강제한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import Any


class Safe(str):
    """이미 이스케이프됐거나 의도적으로 원문인 HTML 조각.

    `Safe + x` 는 다시 `Safe` 다. 이게 없으면 `str.__add__` 가 평범한 `str` 을
    돌려주고, 그 조각이 상위 `join()` 에서 **통째로 이스케이프돼** 화면에 태그가
    글자로 찍힌다 (조용히 망가지는 종류의 버그다).

    오른쪽 피연산자가 `Safe` 가 아니면 이스케이프한다 — 붙이는 것도 안전이 기본이다.
    """

    def __add__(self, other: Any) -> Safe:
        return Safe(str(self) + str(h(other)))

    def __radd__(self, other: Any) -> Safe:
        return Safe(str(h(other)) + str(self))


def h(value: Any) -> Safe:
    """무엇이든 안전한 HTML 로. `Safe` 는 그대로, 나머지는 이스케이프."""
    if isinstance(value, Safe):
        return value
    return Safe(escape(str(value if value is not None else ""), quote=True))


def join(parts: Iterable[Any], sep: str = "") -> Safe:
    return Safe(sep.join(str(h(p)) for p in parts))


def attrs(**kw: Any) -> Safe:
    out = []
    for k, v in kw.items():
        if v is None or v is False:
            continue
        name = k.rstrip("_").replace("_", "-")
        if v is True:
            out.append(name)
        else:
            out.append(f'{name}="{escape(str(v), quote=True)}"')
    return Safe(" " + " ".join(out) if out else "")


# 태그 이름은 **위치 전용**이다 (`/`). 안 그러면 `input_(name="x")` 처럼
# `name` 이라는 HTML 속성을 쓰는 순간 태그 이름과 충돌한다.
def tag(_name: str, /, *children: Any, **kw: Any) -> Safe:
    inner = "".join(str(h(c)) for c in children if c is not None and c is not False)
    return Safe(f"<{_name}{attrs(**kw)}>{inner}</{_name}>")


def void(_name: str, /, **kw: Any) -> Safe:
    return Safe(f"<{_name}{attrs(**kw)}>")


# 자주 쓰는 것들
def div(*c: Any, **kw: Any) -> Safe: return tag("div", *c, **kw)
def span(*c: Any, **kw: Any) -> Safe: return tag("span", *c, **kw)
def p(*c: Any, **kw: Any) -> Safe: return tag("p", *c, **kw)
def a(*c: Any, **kw: Any) -> Safe: return tag("a", *c, **kw)
def ul(*c: Any, **kw: Any) -> Safe: return tag("ul", *c, **kw)
def li(*c: Any, **kw: Any) -> Safe: return tag("li", *c, **kw)
def h1(*c: Any, **kw: Any) -> Safe: return tag("h1", *c, **kw)
def h2(*c: Any, **kw: Any) -> Safe: return tag("h2", *c, **kw)
def h3(*c: Any, **kw: Any) -> Safe: return tag("h3", *c, **kw)
def section(*c: Any, **kw: Any) -> Safe: return tag("section", *c, **kw)
def table(*c: Any, **kw: Any) -> Safe: return tag("table", *c, **kw)
def tr(*c: Any, **kw: Any) -> Safe: return tag("tr", *c, **kw)
def td(*c: Any, **kw: Any) -> Safe: return tag("td", *c, **kw)
def th(*c: Any, **kw: Any) -> Safe: return tag("th", *c, **kw)
def form(*c: Any, **kw: Any) -> Safe: return tag("form", *c, **kw)
def button(*c: Any, **kw: Any) -> Safe: return tag("button", *c, **kw)
def label(*c: Any, **kw: Any) -> Safe: return tag("label", *c, **kw)
def pre(*c: Any, **kw: Any) -> Safe: return tag("pre", *c, **kw)
def code(*c: Any, **kw: Any) -> Safe: return tag("code", *c, **kw)
def small(*c: Any, **kw: Any) -> Safe: return tag("small", *c, **kw)
def strong(*c: Any, **kw: Any) -> Safe: return tag("strong", *c, **kw)


def textarea(_name: str, value: str = "", /, **kw: Any) -> Safe:
    return Safe(f"<textarea{attrs(name=_name, **kw)}>{escape(value)}</textarea>")


def input_(**kw: Any) -> Safe:
    return void("input", **kw)


def select(_name: str, options: Iterable[tuple[str, str]], value: str = "",
           /, **kw: Any) -> Safe:
    opts = "".join(
        f'<option value="{escape(str(v), quote=True)}"'
        f'{" selected" if str(v) == str(value) else ""}>{escape(str(t))}</option>'
        for v, t in options
    )
    return Safe(f"<select{attrs(name=_name, **kw)}>{opts}</select>")


def nl2br(text: str) -> Safe:
    """줄바꿈만 살린 본문 — 나머지는 전부 이스케이프."""
    return Safe(escape(str(text or "")).replace("\n", "<br>"))


def markdownish(text: str) -> Safe:
    """아주 얕은 마크다운: `## 제목`, `· 목록`, `**강조**`, 빈 줄 = 문단.

    파서를 붙이지 않은 이유는 **입력이 우리 것이 아닐 수 있기 때문**이다.
    이 함수는 이스케이프 먼저 하고 그 위에 몇 가지만 되살린다.
    """
    import re

    esc = escape(str(text or ""))
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"`([^`]+?)`", r"<code>\1</code>", esc)
    out, in_list = [], False
    for line in esc.split("\n"):
        s = line.strip()
        if s.startswith("### "):
            out.append(f"<h4>{s[4:]}</h4>")
        elif s.startswith("## "):
            out.append(f"<h3>{s[3:]}</h3>")
        elif s.startswith(("· ", "- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        elif s:
            out.append(f"<p>{s}</p>")
        if in_list:
            out.append("</ul>")
            in_list = False
    if in_list:
        out.append("</ul>")
    return Safe("".join(out))


# ── 페이지 껍데기 ────────────────────────────────────────────────────────

BASE_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#26303c;--ink:#dbe4ee;--dim:#8494a6;
  --acc:#2ab7a9;--warn:#f59e0b;--bad:#ef4444;--ok:#22c55e;--radius:6px}
@media (prefers-color-scheme:light){:root{--bg:#f7f9fb;--panel:#fff;--line:#dfe6ee;
  --ink:#16202b;--dim:#5d6b7a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Pretendard","Noto Sans KR",sans-serif}
a{color:var(--acc)}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,"D2Coding",Consolas,monospace;font-size:.9em}
pre{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:10px 12px;overflow:auto}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
header.top{border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
header.top .wrap{display:flex;align-items:center;gap:18px;height:56px}
.brand{font-weight:700;letter-spacing:-.2px;text-decoration:none;color:var(--ink)}
.brand b{color:var(--acc)}
nav.main{display:flex;gap:14px;flex-wrap:wrap}
nav.main a{color:var(--dim);text-decoration:none;font-size:14px;padding:4px 2px;
  border-bottom:2px solid transparent}
nav.main a:hover{color:var(--ink)}
nav.main a.on{color:var(--ink);border-bottom-color:var(--acc)}
.grow{flex:1}
.who{font-size:13px;color:var(--dim)}
.who b{color:var(--ink)}
main{padding:26px 0 60px}
h1{font-size:26px;margin:0 0 6px} h2{font-size:19px;margin:30px 0 10px}
h3{font-size:16px;margin:20px 0 8px} h4{font-size:14px;margin:14px 0 6px}
.lede{color:var(--dim);margin:0 0 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px 16px;margin:10px 0}
.grid{display:grid;gap:12px}
.grid.c2{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.grid.c3{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:1px 9px;
  font-size:12px;color:var(--dim)}
.tag.ok{border-color:var(--ok);color:var(--ok)}
.tag.warn{border-color:var(--warn);color:var(--warn)}
.tag.bad{border-color:var(--bad);color:var(--bad)}
.tag.acc{border-color:var(--acc);color:var(--acc)}
.dim{color:var(--dim)} .small{font-size:13px}
form.stack{display:grid;gap:12px;max-width:640px}
label{display:block;font-size:13px;color:var(--dim);margin-bottom:4px}
input[type=text],input[type=password],input[type=email],input[type=datetime-local],
textarea,select{width:100%;padding:8px 10px;background:var(--bg);color:var(--ink);
  border:1px solid var(--line);border-radius:var(--radius);font:inherit}
textarea{min-height:120px;resize:vertical;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
button,.btn{background:var(--acc);color:#04201d;border:0;border-radius:var(--radius);
  padding:8px 15px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none;
  display:inline-block}
button.ghost,.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line);font-weight:400}
button.bad{background:var(--bad);color:#fff}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.flash{border-radius:var(--radius);padding:10px 14px;margin:0 0 16px;border:1px solid}
.flash.ok{border-color:var(--ok);color:var(--ok);background:rgba(34,197,94,.07)}
.flash.bad{border-color:var(--bad);color:var(--bad);background:rgba(239,68,68,.07)}
.flash.warn{border-color:var(--warn);color:var(--warn);background:rgba(245,158,11,.07)}
footer{border-top:1px solid var(--line);color:var(--dim);font-size:13px;padding:20px 0 40px}
.empty{color:var(--dim);font-style:italic;padding:14px 0}
.diff{font-size:12.5px;line-height:1.5}
.diff .add{color:var(--ok)} .diff .del{color:var(--bad)} .diff .at{color:var(--acc)}
"""


def page(title: str, body: Safe, *, css: str = "", nav: Safe | None = None,
         who: Safe | None = None, footer: Safe | None = None,
         brand_href: str = "/") -> str:
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(title)}</title>"
        f"<style>{BASE_CSS}{css}</style></head><body>"
        "<header class=\"top\"><div class=\"wrap\">"
        f"<a class=\"brand\" href=\"{escape(brand_href, quote=True)}\">"
        "the dawn of <b>AGI</b></a>"
        f"{nav or ''}<span class=\"grow\"></span>{who or ''}"
        "</div></header>"
        f"<main><div class=\"wrap\">{body}</div></main>"
        f"<footer><div class=\"wrap\">{footer or ''}</div></footer>"
        "</body></html>"
    )


def navbar(items: list[tuple[str, str]], current: str) -> Safe:
    return Safe("<nav class=\"main\">" + "".join(
        str(a(text, href=href, class_="on" if href == current else None))
        for href, text in items
    ) + "</nav>")


def diff_block(lines: list[str]) -> Safe:
    out = []
    for ln in lines:
        cls = ("add" if ln.startswith("+") and not ln.startswith("+++")
               else "del" if ln.startswith("-") and not ln.startswith("---")
               else "at" if ln.startswith("@@") else "")
        out.append(f'<span class="{cls}">{escape(ln)}</span>')
    return Safe('<pre class="diff">' + "\n".join(out) + "</pre>")


__all__ = [
    "BASE_CSS",
    "Safe",
    "a",
    "attrs",
    "button",
    "code",
    "diff_block",
    "div",
    "form",
    "h",
    "h1",
    "h2",
    "h3",
    "input_",
    "join",
    "label",
    "li",
    "markdownish",
    "navbar",
    "nl2br",
    "p",
    "page",
    "pre",
    "section",
    "select",
    "small",
    "span",
    "strong",
    "table",
    "tag",
    "td",
    "textarea",
    "th",
    "tr",
    "ul",
    "void",
]
