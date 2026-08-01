# START HERE — the dawn of AGI 회사 구축 지시서

> **CC(Claude Code)에게**: 이 저장소는 회사 "the dawn of AGI(다노파기)"를 el34 인프라 위에 구축하기 위한 문서 묶음이다. 아래 순서를 반드시 지켜라.

## 이 회사는 무엇인가
- **회사명**: the dawn of AGI (한국어: 다노파기)
- **미션**: "AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다."
- **핵심 원리**: 사람 개입 최소화, 에이전트 자동 루프 운영. 사람은 **EG(Experience Graph)를 조정**함으로써만 개입한다. 우리가 먼저 AX 된다(자사가 첫 고객이자 데모).

## 문서 구조
```
START_HERE.md            ← 지금 읽는 파일. 항상 여기서 시작.
docs/context/            ← 참조 문서. 읽고 맥락으로 삼되 그대로 두라(수정은 사람이).
  00_charter.md            회사 헌장 — 미션·사업·조직·원리
  01_aoc_architecture.md   AOC 관제 아키텍처 — 무엇을 관제/탐지/대응하나
  02_eg_schema.md          EG 스키마 — 회사의 뇌, 노드/엣지 정의
  03_org_personas.md       조직·페르소나 — 누가 어떻게 일하나
  04_tech_stack.md         기술 스택 — el34·모델·MCP·저장소
  05_conventions.md        컨벤션·가드레일 — 코딩/보안/품질 규칙 (반드시 준수)
instructions/            ← 구축 지시문. P0부터 순서대로 실행.
  P0_bootstrap.md          모노레포·CI·시크릿·el34 연결
  P1_experience_graph.md   EG 구축 (회사의 뇌 — 최우선)
  P2_harness_loop.md       에이전트 하네스·루프·행동 게이트
  P3_aoc_system.md         AOC 관제 시스템·시각화
  P4_web_groupware.md      홈페이지·그룹웨어
  P5_business_systems.md   업무용 시스템(CRM·문서·프로젝트·경리)
  P6_integration.md        통합·검증·자사 운영 개시
```

## 실행 규칙 (엄수)
1. **순서대로**: P0 → P1 → ... → P6. 앞 단계의 완료 조건(DoD)을 충족하지 못하면 다음으로 넘어가지 마라.
2. **참조 먼저**: 각 지시문(Pn)을 시작하기 전에, 그 문서가 지정한 `docs/context/` 참조 문서를 읽어라.
3. **EG 우선**: P1(EG)이 다른 모든 것의 전제다. 에이전트·관제·업무시스템은 EG의 페르소나·정책을 참조해야 한다. EG 없이 에이전트를 만들지 마라.
4. **가드레일 준수**: `05_conventions.md`의 보안·품질 규칙은 예외 없이 적용한다. 특히 시크릿을 코드에 하드코딩하지 말고, 파괴적 작업은 사람 승인 게이트를 거쳐라.
5. **자기검증**: 각 지시문 끝의 "완료 조건(DoD)"과 "자기검증" 절차를 실행하고, 결과를 `BUILD_LOG.md`에 append하라.
6. **불확실하면 멈춰라**: 스펙이 모호하거나 파괴적 결정이 필요하면 추측하지 말고 사람에게 물어라(이 저장소에 `QUESTIONS.md`로 남겨라).
7. **작게 커밋**: 기능 단위로 커밋하고, 각 커밋 메시지에 어느 Pn·어느 DoD 항목인지 명시하라.

## 지금 할 일
1. `docs/context/`의 6개 문서를 순서대로 읽어 회사 전체 맥락을 파악한다.
2. `instructions/P0_bootstrap.md`를 열어 첫 구축을 시작한다.
3. `BUILD_LOG.md`를 만들어 진행을 기록하기 시작한다.
