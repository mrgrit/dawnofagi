"""회사명 갱신 확인 (verify-p1.sh DoD-1)."""

import json
import pathlib
import subprocess


def main() -> int:
    r = subprocess.run(
        ["grep", "-rq", "org:el34", "eg/seed/", "eg/schema.json"], capture_output=True
    )
    if r.returncode == 0:
        print("구 회사명 org:el34 가 남아 있다")
        subprocess.run(["grep", "-rn", "org:el34", "eg/seed/", "eg/schema.json"])
        return 1

    d = json.loads(pathlib.Path("eg/seed/01_foundation.json").read_text(encoding="utf-8"))
    hits = [x for x in d["OrgUnit"] if x["id"] == "org:dawn"]
    if not hits:
        print("org:dawn 노드가 없다")
        return 1
    o = hits[0]
    print(f"  id      {o['id']}")
    print(f"  name    {o['name']}")
    print(f"  mission {o['mission'][:78]}…")
    if "dawn of AGI" not in o["name"]:
        print("회사명이 the dawn of AGI 가 아니다")
        return 1
    if "AGI로 가는 길" not in o["mission"]:
        print("미션이 헌장 문구가 아니다")
        return 1

    # 하위 조직이 새 최상위에 붙어 있는가
    parents = {e["to"] for e in d["edges"] if e["type"] == "PART_OF"}
    if "org:dawn" not in parents:
        print("어떤 본부도 org:dawn 에 PART_OF 로 붙어 있지 않다")
        return 1
    n = sum(1 for e in d["edges"] if e["type"] == "PART_OF" and e["to"] == "org:dawn")
    print(f"  본부    {n}개가 org:dawn 에 소속")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
