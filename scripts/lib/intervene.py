"""개입 시뮬레이션 — persona 원칙을 넣고/빼서 EG 반영을 실증한다 (verify-p1.sh).

python intervene.py add <persona-id> <문구>
python intervene.py check <persona-id> <문구>   → 반영됐으면 0
"""

import json
import pathlib
import sys

SEED = pathlib.Path("eg/seed/03_personas.json")


def load() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


def save(d: dict) -> None:
    SEED.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    action, pid, text = argv[0], argv[1], argv[2]
    if action == "add":
        d = load()
        for per in d["Persona"]:
            if per["id"] == pid:
                per["principles"].insert(0, text)
                save(d)
                return 0
        print(f"페르소나 없음: {pid}", file=sys.stderr)
        return 1
    if action == "check":
        from dawn_core.eg import org_profile
        from dawn_core.eg.cli import db_path
        from dawn_core.eg.store import EGStore
        from dawn_core.paths import Paths

        store = EGStore(db_path(Paths()))
        for p in org_profile(store, "org:hr").personas:
            if text in (p.prop("principles") or []):
                return 0
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
