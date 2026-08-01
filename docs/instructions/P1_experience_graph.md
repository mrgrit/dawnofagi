# P1 — Experience Graph 구축 (회사의 뇌, 최우선)

## 목표
회사의 규정·보안등급·조직·페르소나·자산을 EG에 심는다. 이것이 곧 "회사를 EG로 세우는" 작업이며, 이후 모든 에이전트가 이 EG를 참조해 행동한다. **EG 없이 P2로 가지 마라.**

## 먼저 읽어라
- `docs/context/02_eg_schema.md` (스키마 설계 전문)
- `eg/schema.json` (노드/엣지 타입 정의)
- `eg/seed/*.json` (초기값 시드)
- `eg/BOOTSTRAP.md` (주입 절차)
- `docs/context/03_org_personas.md` (조직·페르소나 맥락)

## 작업
1. **시드 회사명 갱신**: `eg/seed/01_foundation.json`의 최상위 조직 노드를 회사명 **the dawn of AGI (다노파기)**로 갱신한다. `org:el34` → `org:dawn`(또는 유지하되 name/mission을 헌장 문구로). 미션 = "AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다." 하위 조직 구조(4본부)는 그대로. 참조하는 다른 시드의 org id도 일괄 갱신.
2. **EG 저장소 선택**: `eg/BOOTSTRAP.md`의 두 경로 중 하나. 초기엔 경로 A(bastion_graph.db 확장, 거버넌스 네임스페이스). bastion.graph API 시그니처를 확인하고 로더를 실동작 코드로 작성.
3. **시드 주입**: 로더로 8종 거버넌스 노드(OrgUnit·Persona·Policy·SecurityLevel·Zone·Asset·AutonomyLevel·ModelPolicy)와 엣지를 upsert. `layer='governance'` 속성 필수.
4. **검증**: `eg/validate.py`를 실행해 참조 무결성·커버리지·핵심순회를 확인. **오류 0이어야 함**(경고 bastion 등급-존 괴리는 정상).
5. **eg_search 연동 확인**: 주입된 거버넌스 노드가 `eg_search`로 조회되는지 확인. 예: "인사팀 에이전트의 페르소나와 적용 정책은?" → persona:corporate + pol:pii-hr-fin 등이 나와야.
6. **주입 스냅샷**: 주입 후 그래프 상태를 덤프해 `eg/snapshots/`에 저장(롤백·감사용).

## 완료 조건 (DoD)
- [ ] 시드 회사명이 the dawn of AGI로 갱신됨
- [ ] EG 로더 작성·동작, 거버넌스 노드/엣지 주입 완료
- [ ] validate.py 오류 0 (노드 74·엣지 136 규모, 회사명 갱신 반영)
- [ ] eg_search로 조직→페르소나→정책 체인 조회 성공(3개 조직 샘플)
- [ ] 핵심 순회 3종 동작 확인: 심각도(비가역성×등급), 게이트(자산→등급→정책), 개입지점(조직→페르소나)
- [ ] 스냅샷 저장

## 자기검증
1. `validate.py` 출력을 BUILD_LOG.md에 첨부. 오류 0 확인.
2. eg_search 쿼리 3개(예: CCC·인사·보안AX)의 결과를 기록.
3. 개입 시뮬레이션: persona:corporate의 principles를 한 줄 수정 → 재주입 → eg_search로 반영 확인 → 원복. "사람이 EG를 고치면 반영된다"를 실증하고 기록.

## 다음
DoD 충족 시 `P2_harness_loop.md`로.
