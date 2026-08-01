# 전사 EG 부트스트랩 가이드

회사가 아직 없으므로, 이 시드를 실제 Experience Graph에 주입하는 것이 곧 "회사를 EG로 세우는" 첫 작업이다.

## 파일 구성
```
el34_eg/
├── schema.json            # 노드/엣지 타입 정의 (거버넌스 8종 + 런타임 4종)
├── seed/
│   ├── 01_foundation.json # 보안등급·존·자율화·모델·조직 + 골격 엣지
│   ├── 02_policies.json   # 규정 11개 + 등급 연결
│   ├── 03_personas.json   # 페르소나 6개 + 조직/규정 연결
│   └── 04_assets.json     # 자산 21개 + 존/등급/소유 연결
├── validate.py            # 무결성 검증 + 핵심 순회 시연
└── BOOTSTRAP.md           # 이 문서
```

## 주입 경로 — 두 가지

### 경로 A. bastion EG DB에 직접 주입 (권장, 초기)
`experience_graph_mcp`는 `bastion_graph.db`를 공유한다. 거버넌스 노드는 런타임 노드와 성격이 다르므로, bastion의 그래프 스토어에 **거버넌스 네임스페이스**로 넣는다.

1. bastion의 그래프 저장 API(`bastion.graph`)를 확인한다.
2. 거버넌스 노드/엣지를 `layer='governance'` 속성과 함께 upsert하는 얇은 로더를 작성한다 (아래 의사코드).
3. `eg_search`가 이 노드들을 조회 대상에 포함하도록 인덱싱한다.

```python
# 의사코드 — 실제 bastion.graph API에 맞춰 조정
import json, glob
from bastion import graph  # 실제 모듈 경로에 맞춤

for f in sorted(glob.glob("seed/*.json")):
    data = json.load(open(f, encoding="utf-8"))
    for ntype, items in data.items():
        if ntype.startswith("_") or ntype == "edges":
            continue
        for it in items:
            graph.upsert_node(
                id=it["id"], type=ntype, layer="governance",
                props=it, created_by=data["_meta"]["created_by"],
            )
    for e in data.get("edges", []):
        graph.upsert_edge(type=e["type"], src=e["from"], dst=e["to"])
```

### 경로 B. EG_MODE 주입 계층으로 활용 (운영)
`experience_graph_mcp`의 `EG_MODE`(off/playbook/experience/full)에 맞춰:
- `Policy`·`Persona` → **playbook** 모드에서 주입 (규정·일하는 방식)
- `Task`·`Finding`·`Observation` → **experience** 모드 (런타임 경험)
- 전체 → **full**

`UserPromptSubmit` 훅(`inject_eg.py`)이 작업 착수 시 해당 조직의 Persona → Policy 체인을 조회해 시스템 프롬프트에 주입하도록 확장한다.

## 검증
```bash
python3 validate.py
```
- 참조 무결성(엣지 from/to 실존), 타입 방향, enum
- 커버리지(모든 Asset은 등급·존, 모든 Zone은 등급)
- 핵심 순회 실증(심각도·게이트·개입지점)

오류 0이어야 주입 가능. 경고 중 `[등급-존 괴리] asset:bastion`은 의도된 것(외부존의 고위험 도구).

## 사람의 개입 = 이 시드의 수정
운영 중 개입은 코드가 아니라 이 노드들을 고치는 것이다:
- **행동을 바꾸려면** → `03_personas.json`의 `principles`/`prohibited` 수정
- **규정을 바꾸려면** → `02_policies.json`의 `rule`/`enforcement` 수정
- **자율화를 올리려면** → `01_foundation.json`의 `OPERATES_AT` 엣지를 A1→A2로
- **자산 등급을 바꾸려면** → `04_assets.json`의 `CLASSIFIED_AS` 수정

수정 후 `validate.py`로 검증 → 재주입 → `eg_search`를 통해 다음 작업부터 전 에이전트에 반영. `EG_MODE` A/B로 반영 효과 측정.

## 다음 단계 (시드 확장)
1. **CCC 6팀 분화**: `org:ccc`를 SOC/CTI/분석대응/오펜시브/보안운영/보안교육사업 6개 team으로.
2. **테넌트 도입**: 고객사별 `tenant` 속성 + 테넌트 네임스페이스 Asset/Zone. `pol:no-cross-tenant`가 이를 강제.
3. **Skill 연결**: bastion 33개 Skill을 `Skill` 노드로 로드하고 `ACTS_ON`으로 Asset에 연결 → 위험도×자산등급으로 게이트 자동 결정.
4. **런타임 축적 시작**: 에이전트가 `eg_record_task/finding/observation` 실행 → Task/Finding/Observation이 Asset에서 거버넌스와 만나기 시작.
