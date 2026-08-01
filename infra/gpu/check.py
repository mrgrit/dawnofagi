#!/usr/bin/env python3
"""사내 GPU 서버(ollama) 도달성 확인 — "로컬 모델"의 전제.

이 호스트에는 GPU 가 없다. `pol:l3-local-only` 가 요구하는 로컬 모델은
VPN 너머 사내 GPU 서버에서 돈다. 여기 못 닿으면 L3 업무는 **중단**된다
(클라우드 폴백 없음 — 그게 정책이다).

  python infra/gpu/check.py
  python infra/gpu/check.py --json
  python infra/gpu/check.py --generate   # 실제 추론 1회까지 확인

종료 코드: 0 정상 / 1 미도달
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "COMPANY.md").is_file():
            return p
    return here.parents[2]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def http_json(url: str, payload: dict | None = None, timeout: int = 20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data)
    if data:
        req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body), time.monotonic() - t0
            except json.JSONDecodeError:
                return r.status, body, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300], time.monotonic() - t0
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return 0, f"{type(e).__name__}: {e}", time.monotonic() - t0


_SIZE_HINTS = [
    ("135m", 0.135),
    ("1.2b", 1.2),
    ("1b", 1),
    ("2.4b", 2.4),
    ("2b", 2),
    ("3b", 3),
    ("3.8b", 3.8),
    ("4b", 4),
    ("8b", 8),
    ("9b", 9),
    ("12b", 12),
    ("20b", 20),
]


def _smallest(models: list[str]) -> str | None:
    """이름에서 파라미터 수를 추정해 가장 작은 모델을 고른다 (콜드 스타트 최소화)."""
    best, best_size = None, float("inf")
    for m in models:
        low = m.lower()
        for tag, size in _SIZE_HINTS:
            if tag in low and size < best_size:
                best, best_size = m, size
                break
    return best


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사내 GPU 서버 확인")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--generate", action="store_true", help="실제 추론 1회까지 확인")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument(
        "--gen-model",
        default=None,
        help="추론 확인에 쓸 모델 (기본: 가장 작은 것). 대형 모델은 콜드 스타트가 수 분이다",
    )
    ap.add_argument(
        "--gen-timeout",
        type=int,
        default=600,
        help="추론 타임아웃(초). 120b 콜드 스타트는 2분을 넘긴다",
    )
    args = ap.parse_args(argv)

    root = repo_root()
    load_dotenv(root / ".env")

    base = os.getenv("LOCAL_LLM_BASE_URL", "").rstrip("/")
    model = os.getenv("LOCAL_LLM_MODEL", "")
    portal = os.getenv("VPN_PORTAL", "")

    report: dict = {"base_url": base, "model": model, "ok": False, "checks": []}

    if not base:
        report["hint"] = "LOCAL_LLM_BASE_URL 이 비어 있다 (.env 참조)"
        _print(report, args.json)
        return 1

    host = urlparse(base).hostname or ""
    port = urlparse(base).port or 11434

    # 1. TCP 도달 — VPN 이 붙었는지 가장 빨리 알 수 있는 신호
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=args.timeout):
            tcp_ok = True
    except OSError as e:
        tcp_ok = False
        report["tcp_error"] = str(e)
    report["checks"].append(
        {"name": f"TCP {host}:{port}", "ok": tcp_ok, "ms": round((time.monotonic() - t0) * 1000)}
    )

    if not tcp_ok:
        report["hint"] = (
            "GPU 서버 미도달 — VPN(GlobalProtect)이 연결돼 있는지 확인하라."
            + (f" 포털: {portal}" if portal else "")
            + "\n  연결 후:  make gpu-check"
            + "\n  L3(인사·재무·개인정보) 업무는 이 서버 없이는 중단된다 — 클라우드 폴백 없음."
        )
        _print(report, args.json)
        return 1

    # 2. ollama API — 모델 목록
    status, body, dt = http_json(f"{base}/api/tags", timeout=args.timeout)
    models = []
    if status == 200 and isinstance(body, dict):
        models = [m.get("name", "?") for m in body.get("models", [])]
    report["checks"].append(
        {"name": "GET /api/tags", "status": status, "ms": round(dt * 1000), "models": models}
    )
    report["models"] = models

    if status != 200:
        report["hint"] = f"TCP 는 열렸으나 ollama API 가 응답하지 않는다: {body}"
        _print(report, args.json)
        return 1

    if model and not any(
        m == model or m.startswith(f"{model}:") or m.split(":")[0] == model.split(":")[0]
        for m in models
    ):
        report["checks"].append(
            {
                "name": f"모델 '{model}' 존재",
                "ok": False,
                "note": f"서버가 가진 모델: {', '.join(models) or '없음'}",
            }
        )
        report["hint"] = (
            f"LOCAL_LLM_MODEL={model} 이 서버에 없다. "
            f"`ollama pull {model}` 하거나 .env 를 서버의 모델명으로 맞춰라."
        )
        _print(report, args.json)
        return 1
    if model:
        report["checks"].append({"name": f"모델 '{model}' 존재", "ok": True})

    # 3. 실제 추론 (선택)
    # 경로 검증이 목적이므로 기본값은 **가장 작은 모델**을 쓴다.
    # LOCAL_LLM_MODEL(120b 급)은 콜드 스타트에 수 분이 걸려 "연결 확인"에 부적합하다.
    if args.generate:
        gen_model = args.gen_model or _smallest(models) or model
        report["gen_model"] = gen_model
        status, body, dt = http_json(
            f"{base}/api/generate",
            {"model": gen_model, "prompt": "1+1=? 숫자만 답하라.", "stream": False},
            timeout=args.gen_timeout,
        )
        resp = body.get("response", "").strip()[:80] if isinstance(body, dict) else str(body)[:80]
        report["checks"].append(
            {
                "name": f"POST /api/generate ({gen_model})",
                "status": status,
                "ms": round(dt * 1000),
                "response": resp,
            }
        )
        if status != 200:
            report["hint"] = "모델 목록은 보이나 추론이 실패한다"
            _print(report, args.json)
            return 1

    report["ok"] = True
    _print(report, args.json)
    return 0


def _print(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    mark = "✔" if report["ok"] else "✘"
    print(f"{mark} 사내 GPU 서버  ({report['base_url']})")
    for c in report["checks"]:
        ok = c.get("ok", c.get("status") == 200)
        sym = "●" if ok else "○"
        extra = ""
        if "status" in c:
            extra = f"HTTP {c['status'] or '---'}"
        if c.get("ms") is not None:
            extra += f"  {c['ms']}ms"
        print(f"    {sym} {c['name']:<28} {extra}")
        if c.get("models"):
            print(f"        모델: {', '.join(c['models'])}")
        if c.get("response"):
            print(f"        응답: {c['response']}")
        if c.get("note"):
            print(f"        {c['note']}")
    if report.get("hint"):
        for ln in report["hint"].splitlines():
            print(f"    ⓘ {ln}")
    print()
    print("결과:", "정상 — L3 업무 가능" if report["ok"] else "미도달 — L3 업무 중단")


if __name__ == "__main__":
    raise SystemExit(main())
