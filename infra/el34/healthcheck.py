#!/usr/bin/env python3
"""el34 연결 헬스체크 — P3 수집 계층의 전제.

Assessor(`/health`·`/activity`·`/assess`, X-API-Key)에 읽기 접근이 되는지 확인한다.
el34 는 이 회사의 관제 대상이자 실물 앵커이므로, 여기가 안 되면 AOC 는 눈이 없다.

시크릿은 코드에 없다 — `.env` 또는 환경변수에서만 읽는다 (05_conventions #1).

  python infra/el34/healthcheck.py            # 기본 검사
  python infra/el34/healthcheck.py --json     # 기계 판독용
  python infra/el34/healthcheck.py --zones    # el34 4-tier 존 도달성까지

종료 코드: 0 정상 / 1 실패 / 2 설정 오류
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

DEFAULT_TIMEOUT = 5

# el34 4-tier 세그먼트 (docs/context/04_tech_stack.md)
ZONES = {
    "ext": ("10.20.30.0/24", "외부/공개 — bastion, attacker"),
    "pipe": ("10.20.31.0/24", "경유/검사 — fw, ips (PEP)"),
    "dmz": ("10.20.32.0/24", "제한 — web, Wazuh SIEM, portal, Assessor"),
    "int": ("10.20.40.0/24", "통제 — 취약 웹(고객 모사), DB"),
    "user": ("10.20.33.0/24", "내부 — Windows 엔드포인트"),
}

# 존별 도달성 프로브 (호스트에서 브리지로 직접)
ZONE_PROBES = [
    ("dmz", "10.20.32.110", 9200, "Wazuh indexer"),
    ("dmz", "10.20.32.55", 8000, "Assessor (수집 계층 원형)"),
    ("dmz", "10.20.32.80", 80, "web (WAF 앞단)"),
    ("int", "10.20.40.81", 3000, "juiceshop (취약웹 — 레드팀 스코프)"),
]


def load_dotenv(path: Path) -> None:
    """.env 를 환경변수로 로드한다 (이미 설정된 값은 덮지 않는다)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "COMPANY.md").is_file():
            return p
    return here.parents[2]


def http(
    url: str,
    api_key: str | None,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, float]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if api_key:
        req.add_header("X-API-Key", api_key)
    if data:
        req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(400).decode("utf-8", "replace"), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf-8", "replace"), time.monotonic() - t0
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return 0, f"{type(e).__name__}: {e}", time.monotonic() - t0


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def candidate_bases() -> list[str]:
    """Assessor 후보 URL. 환경변수가 최우선, 없으면 알려진 위치를 순서대로."""
    explicit = os.getenv("EL34_ASSESSOR_URL")
    if explicit:
        return [explicit.rstrip("/")]
    return [
        "http://10.20.32.55:8000",  # dmz 브리지의 컨테이너 IP (호스트에서 직접)
        "http://127.0.0.1:9201",  # 호스트 포트 바인딩
        "http://localhost:8000",
    ]


def check_assessor(api_key: str | None, timeout: int) -> dict:
    result: dict = {"component": "assessor", "ok": False, "base": None, "checks": []}

    base = None
    for cand in candidate_bases():
        status, body, dt = http(f"{cand}/health", None, timeout=timeout)
        result["checks"].append(
            {
                "name": "health",
                "url": f"{cand}/health",
                "status": status,
                "ms": round(dt * 1000),
                "body": body[:160],
            }
        )
        if status == 200:
            base = cand
            break
    if base is None:
        result["hint"] = (
            "Assessor 가 응답하지 않는다. el34 의 assessor 는 profiles:[assessor] 라 기본 기동되지 않는다.\n"
            "  기동:  cd ~/el34 && docker compose --profile assessor up -d assessor\n"
            "  또는 EL34_ASSESSOR_URL 환경변수로 실제 주소를 지정하라."
        )
        return result
    result["base"] = base

    # 인증이 필요한 엔드포인트 — 키 유무에 따라 기대 응답이 다르다
    if not api_key:
        result["hint"] = "EL34_API_KEY 가 없어 /activity·/assess 인증 검사를 건너뛴다 (.env 참조)"
        result["ok"] = True  # /health 200 이면 도달성은 확인된 것
        return result

    status, body, dt = http(f"{base}/activity", api_key, "POST", {}, timeout)
    result["checks"].append(
        {"name": "activity(auth)", "status": status, "ms": round(dt * 1000), "body": body[:160]}
    )
    auth_ok = status not in (0, 401)

    status2, body2, dt2 = http(f"{base}/activity", "wrong-key-on-purpose", "POST", {}, timeout)
    result["checks"].append(
        {
            "name": "activity(bad key → 401 기대)",
            "status": status2,
            "ms": round(dt2 * 1000),
            "body": body2[:120],
        }
    )
    rejects_bad_key = status2 == 401

    result["ok"] = auth_ok and rejects_bad_key
    if not rejects_bad_key:
        result["hint"] = "잘못된 키가 401 로 거부되지 않는다 — 인증이 실제로 걸려 있는지 확인하라"
    return result


def check_zones() -> dict:
    out: dict = {"component": "el34_zones", "ok": True, "checks": []}
    for zone, host, port, label in ZONE_PROBES:
        ok = tcp_open(host, port)
        out["checks"].append({"zone": zone, "target": f"{host}:{port}", "label": label, "open": ok})
        if not ok:
            out["ok"] = False
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="el34 연결 헬스체크")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--zones", action="store_true", help="el34 존 도달성까지 확인")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    root = repo_root()
    load_dotenv(root / ".env")
    api_key = os.getenv("EL34_API_KEY") or os.getenv("API_KEY")

    report = {"root": str(root), "components": [check_assessor(api_key, args.timeout)]}
    if args.zones:
        report["components"].append(check_zones())
    report["ok"] = all(c["ok"] for c in report["components"])

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    for comp in report["components"]:
        mark = "✔" if comp["ok"] else "✘"
        print(f"{mark} {comp['component']}" + (f"  ({comp['base']})" if comp.get("base") else ""))
        for c in comp["checks"]:
            if "open" in c:
                print(
                    f"    {'●' if c['open'] else '○'} [{c['zone']}] {c['target']:<22} {c['label']}"
                )
            else:
                st = c["status"]
                sym = "●" if st and st < 400 else ("◐" if st == 401 else "○")
                print(f"    {sym} {c['name']:<32} HTTP {st or '---'}  {c['ms']}ms")
                if c.get("body") and st != 200:
                    print(f"        {c['body'][:120]}")
        if comp.get("hint"):
            for ln in comp["hint"].splitlines():
                print(f"    ⓘ {ln}")
    print()
    print("결과:", "정상" if report["ok"] else "실패")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
