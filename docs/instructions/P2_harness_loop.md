# P2 — 에이전트 하네스·루프 엔지니어링

## 목표
EG를 참조해 행동하는 에이전트 런타임을 만든다. 워커 루프(eg_search→skill_preview→skill_run→eg_record), 행동 게이트, 모델 라우팅, 부서 오케스트레이션. bastion의 패턴을 재사용·확장한다.

## 먼저 읽어라
- `docs/context/03_org_personas.md` (워커 루프·오케스트레이션·모델배치)
- `docs/context/04_tech_stack.md` (bastion·experience_graph_mcp·모델)
- `docs/context/05_conventions.md` (행동 게이트·최소권한)
- P1에서 구축한 EG (이 위에서 동작)

## 작업
1. **워커 에이전트 골격**: 모든 에이전트가 공통으로 도는 루프를 구현.
   - 착수 시 `eg_search`로 자기 조직의 Persona + 적용 Policy + 관련 과거 경험을 조회해 시스템 프롬프트에 주입.
   - 도구 실행 전 `skill_preview`로 위험도(LOW/MED/HIGH)·destructive 확인.
   - HIGH/destructive이고 정책상 게이트면 HITL 승인 대기(승인 큐).
   - 완료 후 `eg_record`로 Task/Finding/Observation 축적.
2. **행동 게이트 엔진**: `skill_preview` 위험도 + EG 순회(자산→SecurityLevel→APPLIES_TO→Policy.enforcement)를 결합해 block/require_hitl/warn/log_only를 결정. 05_conventions의 파괴적 작업 규칙과 일치.
3. **모델 라우팅**: 에이전트가 자기 조직의 `USES_MODEL`(EG ModelPolicy)을 eg_search로 조회해 사용할 모델을 결정. L3 자산 관여 시 로컬 모델 강제(pol:l3-local-only).
4. **팀 오케스트레이터**: 부서별 오케스트레이터가 워커에게 위임(CC Subagent 패턴, bastion SubAgent A2A 재사용). 부서 간 협업은 LangGraph 상태머신.
5. **이벤트 구동**: 상시 시뮬레이션이 아니라 훅/이벤트로 기동. experience_graph_mcp의 UserPromptSubmit 훅 패턴 확장. 반복 업무는 큐로.
6. **텔레메트리 방출**: 모든 루프 단계를 OTel GenAI 스팬으로 방출(invoke_agent→chat/execute_tool). 버전 pin. P3 수집 계층이 이걸 받는다.
7. **최소 2개 조직으로 시연**: 예) CCC(secops 페르소나, Sonnet+로컬) 워커 1개, 경영관리(corporate, Haiku) 워커 1개를 실제로 돌려 루프·게이트·라우팅이 동작함을 보인다.

## 완료 조건 (DoD)
- [ ] 워커 루프 4단계 동작(eg_search→preview→run→record)
- [ ] 행동 게이트: destructive+L3 자산 작업이 HITL로 라우팅되는지 테스트
- [ ] 모델 라우팅: 조직별로 다른 모델이 선택되는지 확인(EG 기반)
- [ ] 팀 오케스트레이터가 워커에 위임 성공
- [ ] 이벤트 구동(훅) 동작, 상시 폴링 아님
- [ ] OTel 스팬 방출 확인(invoke_agent/execute_tool 트리)
- [ ] 2개 조직 워커 실제 실행 데모

## 자기검증
1. 게이트 테스트: 가짜 "결제 실행(asset:payment, L3, irreversible)" 작업을 워커에 주고 → HITL 게이트가 걸리는지 확인, 로그 기록.
2. 라우팅 테스트: CCC 워커와 경영관리 워커가 각각 다른 모델을 eg_search로 골랐는지 확인.
3. 개입 실증: EG에서 해당 조직 Persona의 prohibited에 항목을 추가 → 워커가 다음 실행에서 그 행동을 회피하는지 확인 → 원복.
4. 스팬 덤프를 BUILD_LOG.md에 첨부.

## 다음
DoD 충족 시 `P3_aoc_system.md`로.
