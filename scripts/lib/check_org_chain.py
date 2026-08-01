"""조직 → 페르소나 → 정책 체인이 성립하는지 확인 (verify-p1.sh DoD-4)."""

import sys

from dawn_core.eg import org_profile
from dawn_core.eg.cli import db_path
from dawn_core.eg.store import EGStore
from dawn_core.paths import Paths


def main(org_ids: list[str]) -> int:
    store = EGStore(db_path(Paths()))
    bad = 0
    for oid in org_ids:
        p = org_profile(store, oid)
        personas = [x.id for x in p.personas]
        policies = [x.id for x in p.policies]
        if not personas:
            print(f"  ✘ {oid}: 페르소나 없음")
            bad += 1
            continue
        if not policies:
            print(f"  ✘ {oid}: 적용 정책 없음")
            bad += 1
            continue
        print(
            f"  {oid:<12} → {', '.join(personas):<42} → 정책 {len(policies)}개: "
            f"{', '.join(policies)}"
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
