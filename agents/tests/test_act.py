"""도구 루프 프로토콜 — 모델 답을 어떻게 읽나.

여기서 지키는 것: **형식이 아니라고 산출물을 버리지 않는다.** 모델이 그냥
답을 쓴 것과 프로토콜을 어긴 것을 구별할 방법이 없으므로, 도구 호출로 안
읽히면 최종 답으로 본다. 반대로 하면 멀쩡한 결과가 오류가 된다.
"""

from __future__ import annotations

from dawn_agents.worker import Worker

parse = Worker._parse_act


def test_도구_호출을_읽는다():
    kind, name, args = parse('''
<도구 이름="fs.write">
<인자 이름="path">a/b.md</인자>
<인자 이름="content">첫 줄
둘째 줄</인자>
</도구>
''')
    assert kind == "tool"
    assert name == "fs.write"
    assert args == {"path": "a/b.md", "content": "첫 줄\n둘째 줄"}


def test_완료를_읽는다():
    kind, body, args = parse("앞말\n<완료>\n다 했다\n</완료>\n뒷말")
    assert (kind, body, args) == ("done", "다 했다", {})


def test_완료가_도구보다_먼저다():
    """둘 다 있으면 완료로 본다 — 예시를 인용하면서 끝내는 경우가 있다."""
    kind, body, _ = parse('<도구 이름="fs.read"><인자 이름="path">x</인자></도구>\n'
                          "<완료>끝</완료>")
    assert kind == "done" and body == "끝"


def test_형식이_아니면_최종답으로_본다():
    kind, body, _ = parse("## 결과\n그냥 산문으로 답했다.")
    assert kind == "done"
    assert "그냥 산문으로" in body


def test_인자가_없는_도구도_된다():
    kind, name, args = parse('<도구 이름="dev.tests"></도구>')
    assert (kind, name, args) == ("tool", "dev.tests", {})


def test_내용_안의_꺾쇠는_살아남는다():
    """파일 내용에 태그가 들어가는 일은 흔하다 — 거기서 잘리면 못 쓴다."""
    kind, name, args = parse(
        '<도구 이름="fs.write">'
        '<인자 이름="path">t.html</인자>'
        '<인자 이름="content"><div class="x">안</div></인자>'
        "</도구>")
    assert kind == "tool"
    assert args["content"] == '<div class="x">안</div>'


def test_공백이_붙어도_읽는다():
    kind, name, _ = parse('<도구  이름="eg.search" >\n</도구>')
    assert (kind, name) == ("tool", "eg.search")
