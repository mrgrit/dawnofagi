#!/usr/bin/env python3
"""픽셀 오피스 화면을 PNG 로 뽑는다 — 브라우저가 없는 서버에서 구도를 보려고.

이 저장소는 헤드리스 우분투에서 만들어졌다. 콘솔이 죽지 않는지는
`aoc/tests/test_pixel_office.py` 가 좌표로 증명하지만, **사무실처럼 보이는가**는
눈으로 봐야 한다. 그래서 index.html 의 캔버스 호출을 그대로 받아 적어
(aoc/tests/office_harness.js) 여기서 다시 그린다. 브라우저 렌더링과 픽셀 단위로
같지는 않다 — 구도·배치·색을 보기 위한 것이다.

    pip install quickjs pillow          # 둘 다 배포에 필요한 의존이 아니다
    python3 scripts/office-preview.py --out var/aoc/preview

    --view building|floor|desk   --div <division_id>   --agent <agent_id>
    --at first|now|<ns>          --frames <n>   (그 지점부터 이벤트 n 개)

한글 글리프가 있는 폰트가 없으면 글자는 회색 막대로 대체한다 (자리만 본다).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "dawn_core"))
sys.path.insert(0, str(ROOT / "aoc"))

FONTS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def build_context(state: dict, spans: list):
    import quickjs

    harness = (ROOT / "aoc" / "tests" / "office_harness.js").read_text(encoding="utf-8")
    body = (ROOT / "apps" / "pixel-office" / "index.html").read_text(
        encoding="utf-8").split("<script>", 1)[1].split("</script>", 1)[0]
    src = (harness.replace("__STATE__", json.dumps(state, ensure_ascii=False))
                  .replace("__TRACE__", json.dumps(spans, ensure_ascii=False))
                  .replace("__SCRIPT__", body))
    ctx = quickjs.Context()
    ctx.set_memory_limit(512 * 1024 * 1024)
    ctx.set_time_limit(-1)
    ctx.eval(src)
    for _ in range(2000):
        if not ctx.execute_pending_job():
            break
    return ctx


def rgb(css: str) -> tuple[int, int, int]:
    css = (css or "#888888").strip()
    if css.startswith("#"):
        h = css[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if css.startswith("rgb"):
        nums = [int(float(x)) for x in css[css.index("(") + 1:css.index(")")].split(",")[:4]]
        return tuple(nums[:3])                                   # type: ignore[return-value]
    return (136, 136, 136)


def css_alpha(css: str) -> float:
    if css.startswith("rgba"):
        parts = css[css.index("(") + 1:css.index(")")].split(",")
        if len(parts) == 4:
            return float(parts[3])
    return 1.0


def render(frame: dict, out: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw, ImageFont

    w, h = int(frame["w"]), int(frame["h"])
    img = Image.new("RGB", (w, h), (7, 10, 14))
    font_path = next((f for f in FONTS if pathlib.Path(f).is_file()), None)
    cache: dict[int, object] = {}

    def font(px: int):
        if px not in cache:
            cache[px] = ImageFont.truetype(font_path, px) if font_path else ImageFont.load_default()
        return cache[px]

    def paint(op_fn, color, alpha):
        """알파가 있으면 오버레이에 그려 합성한다 (캔버스 globalAlpha 재현)."""
        if alpha >= 0.995:
            op_fn(ImageDraw.Draw(img), color)
            return
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        op_fn(ImageDraw.Draw(layer), (*color, int(alpha * 255)))
        img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))

    for op in frame["ops"]:
        kind = op[0]
        if kind == "r":
            _, x, y, ww, hh, css, ga = op
            if ww <= 0 or hh <= 0:
                continue
            a = ga * css_alpha(css)
            paint(lambda d, c, x=x, y=y, ww=ww, hh=hh: d.rectangle(
                [x, y, x + ww, y + hh], fill=c), rgb(css), a)
        elif kind == "sr":
            _, x, y, ww, hh, css, ga, lw = op
            paint(lambda d, c, x=x, y=y, ww=ww, hh=hh, lw=lw: d.rectangle(
                [x, y, x + ww, y + hh], outline=c, width=max(1, int(lw))), rgb(css), ga)
        elif kind == "p":
            _, pts, css, ga = op
            a = ga * css_alpha(css)
            flat = [(p[0], p[1]) for p in pts]
            paint(lambda d, c, flat=flat: d.polygon(flat, fill=c), rgb(css), a)
        elif kind == "l":
            _, pts, css, ga, lw = op
            a = ga * css_alpha(css)
            flat = [(p[0], p[1]) for p in pts]
            paint(lambda d, c, flat=flat, lw=lw: d.line(flat, fill=c, width=max(1, int(lw))),
                  rgb(css), a)
        elif kind == "t":
            _, txt, x, y, css, ga, fnt, align = op
            size = 11
            for tok in str(fnt).split():
                if tok.endswith("px"):
                    size = max(6, int(float(tok[:-2])))
                    break
            ascii_ok = font_path and all(ord(ch) < 0x2E80 for ch in txt)
            if ascii_ok or not font_path:
                f = font(size)
                anchor = {"center": "mm", "right": "rm", "left": "lm"}.get(align, "lm")
                paint(lambda d, c, txt=txt, x=x, y=y, f=f, anchor=anchor:
                      d.text((x, y), txt, fill=c, font=f, anchor=anchor), rgb(css), ga)
            else:
                # 글리프가 없다 — 자리만 회색 막대로 남긴다
                bw = len(txt) * size * 0.62
                x0 = x - bw / 2 if align == "center" else (x - bw if align == "right" else x)
                paint(lambda d, c, x0=x0, y=y, bw=bw, size=size: d.rectangle(
                    [x0, y - size * 0.42, x0 + bw, y + size * 0.42], fill=c), rgb(css), ga * 0.55)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="var/aoc/preview")
    ap.add_argument("--view", default="building", choices=["building", "floor", "desk"])
    ap.add_argument("--div", default="")
    ap.add_argument("--agent", default="")
    ap.add_argument("--at", default="first")
    ap.add_argument("--frames", type=int, default=1)
    args = ap.parse_args()

    try:
        import quickjs  # noqa: F401
        from PIL import Image  # noqa: F401
    except ModuleNotFoundError as e:
        print(f"미리보기에는 quickjs·pillow 가 필요하다 ({e.name} 없음).")
        print("  pip install quickjs pillow      # 배포에 필요한 의존은 아니다")
        return 2

    from dawn_aoc.collect import TraceLake
    from dawn_aoc.console import build_state

    state = build_state(ROOT, limit=60)
    tid = next((a["last_trace"] for a in state["agents"] if a["last_trace"]), "")
    spans = sorted(TraceLake(ROOT).spans(tid), key=lambda s: s["start_ns"]) if tid else []
    ctx = build_context(state, spans)

    div = args.div or (state["floorplan"]["floors"][0]["division_id"])
    agent = args.agent or state["agents"][0]["agent_id"]

    # 시계는 이벤트 단위로 간다 — 선형 시간으로 뽑으면 1ms 짜리 도구 호출은 한 장도
    # 안 걸린다 (콘솔의 타임라인과 같은 눈금을 쓴다).
    events = sorted({s[k] for s in state["occupancy"] for k in ("start_ns", "end_ns")})
    if args.at == "first":
        i0 = 0
    elif args.at == "now":
        i0 = max(0, len(events) - 1)
        events = events or [state["now_ns"]]
    else:
        want = int(args.at)
        i0 = min(range(len(events)), key=lambda i: abs(events[i] - want)) if events else 0
        events = events or [want]

    outs = []
    for i in range(args.frames):
        t = events[min(i0 + i, len(events) - 1)]
        if args.at not in ("first", "now") and args.frames == 1:
            t = int(args.at)                       # 정확히 그 순간을 원한 것이다
        call = (f"record({json.dumps(args.view)}, {json.dumps(div)}, "
                f"{json.dumps(agent)}, {t})")
        frame = json.loads(ctx.eval(call))
        name = f"{args.view}-{i:02d}.png" if args.frames > 1 else f"{args.view}.png"
        outs.append(render(frame, pathlib.Path(args.out) / name))

    for p in outs:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
