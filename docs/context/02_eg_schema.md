# EL34 전사 EG 초기 스키마 설계서

> **목적**: 회사(EL34 Inc.)가 아직 없는 상태에서, 규정·보안등급·부서 페르소나를 어떤 노드/엣지로 담을지 확정한다. 이 스키마를 채우는 것이 곧 "회사를 EG로 세우는" 작업이다.
> **기반**: bastion EG(Task/Finding/Observation/Skill, `bastion_graph.db`) + experience_graph_mcp(`eg_search`/`eg_record_*`/`skill_preview`/`EG_MODE`).
> **산출물**: `schema.json`(타입 정의) · `seed/*.json`(EL34 초기값) · `validate.py`(검증) · `BOOTSTRAP.md`(주입).

---

## 1. 설계 원칙

### 1.1 거버넌스 계층 vs 런타임 계층 분리
bastion EG가 이미 가진 노드(`Task`/`Finding`/`Observation`/`Skill`)는 **에이전트가 자동 축적**하는 런타임 계층이다. 그 위에 **사람이 초기에 채우는** 거버넌스 계층 8종을 얹는다.

| 계층 | 노드 | 누가 채우나 | EG_MODE |
|---|---|---|---|
| 거버넌스 | OrgUnit, Persona, Policy, SecurityLevel, Zone, Asset, AutonomyLevel, ModelPolicy | 사람(founder→운영진) | playbook |
| 런타임 | Task, Finding, Observation | 에이전트 자동(`eg_record_*`) | experience |
| 런타임 | Skill | 시스템(bastion) | — |

**분리 이유**: 사람의 개입은 거버넌스 노드 수정으로만 이루어진다. 런타임 노드는 읽되 사람이 직접 쓰지 않는다. 이 경계가 "human은 EG를 조정함으로써 개입한다"를 구조로 만든다.

### 1.2 모든 것은 Asset과 SecurityLevel로 수렴
설계의 척추는 두 노드다.
- **Asset** = 런타임과 거버넌스가 만나는 교차점. `Task -TOUCHED-> Asset <-CLASSIFIED_AS- SecurityLevel`.
- **SecurityLevel** = 심각도·게이트·모델배치의 공통 기준. el34 존과 정렬(rank 0~3).

이 두 축 덕분에 에이전트가 무엇을 건드리든 "그게 어느 섹터/얼마나 민감/어떤 게이트"가 그래프 순회 한 번으로 나온다.

### 1.3 실물 앵커
`Zone`은 추상 개념이 아니라 el34의 실제 CIDR이다. 텔레메트리(Wazuh/Suricata)에서 IP로 위치가 자동 결정되고, 픽셀 오피스 방과 1:1 대응한다. 스키마가 실제 인프라에 바인딩된다.

---

## 2. 노드 타입 명세 (거버넌스 8종)

| 노드 | 핵심 속성 | 역할 |
|---|---|---|
| **OrgUnit** | type(bureau/dept/team), mission, outputs, self_first_order | 조직도. 에이전트 팀이 매핑됨 |
| **Persona** | role, principles[], prohibited[], escalation_rule | 일하는 방식. **핵심 개입 지점** |
| **Policy** | statement, rule(의사코드), enforcement(block/require_hitl/warn/log_only) | 규정을 판정 가능 형태로 |
| **SecurityLevel** | rank(0~3), label, handling_rule, requires_hitl | 보안등급. 모든 기준의 원점 |
| **Zone** | cidr, is_gate, pixel_room | el34 실물 세그먼트 |
| **Asset** | kind, irreversibility(read/write/execute/irreversible), owner_org | 보호 대상. 섹터 최소단위 |
| **AutonomyLevel** | level(0~3), gate_rule, promote_kpi | 자율화 A0~A3 |
| **ModelPolicy** | model, cost_tier | 부서별 모델 배치 |

---

## 3. 엣지 타입 명세 (16종)

### 거버넌스 내부
`PART_OF`(조직계통) · `HAS_PERSONA` · `USES_MODEL` · `OPERATES_AT` · `GOVERNED_BY`(페르소나→규정) · `APPLIES_TO`(규정→등급) · `CLASSIFIED_AS`(자산→등급) · `LOCATED_IN`(자산→존) · `MAPS_TO`(존→등급) · `ACTS_ON`(스킬→자산) · `OWNED_BY`(자산→조직)

### 런타임 → 거버넌스 (교차점)
`PERFORMED_BY`(작업→조직) · `TOUCHED`(작업→자산) · `ABOUT`(finding→자산) · `OBSERVES`(관찰→자산) · `CONSTRAINED_BY`(작업→규정, 감사추적)

---

## 4. EL34 인스턴스 (시드 현황)

검증 결과 **노드 74개 · 엣지 136개 · 오류 0**.

| 노드 타입 | 개수 | 내용 |
|---|---|---|
| SecurityLevel | 4 | L0 공개 / L1 내부 / L2 기밀 / L3 극비 |
| Zone | 5 | ext / pipe(게이트) / dmz / int / user |
| AutonomyLevel | 4 | A0 전건승인 → A3 완전자율 |
| ModelPolicy | 5 | Opus / Sonnet / Haiku / gpt-oss / DGX+open |
| OrgUnit | 18 | 4본부 + 부서/팀 (CCC는 초기 단일, 후에 6팀 분화) |
| Policy | 11 | 테넌트격리 · 비가역HITL · L3로컬 · PII · 존게이트 · 자율화게이트 · EU AI Act 12/14 · 레드팀스코프 · 품질 · 금융 |
| Persona | 6 | 전사기본 · AOC개발 · 보안운영 · 오펜시브 · 컨설팅 · 경영관리 |
| Asset | 21 | el34 인프라 8 + 사내데이터 8 + 도구 5 |

### 초기 정책 결정(시드에 반영된 판단)
- 인사/재무 데이터(PII·급여·원장) = **L3, 로컬 모델 전용, HITL 필수, A0**.
- AOC개발·연구소 = **Opus, A1**. 경영관리 정형팀 = **Haiku, A1**. 인사/재무 = **로컬, A0**.
- CCC = 초기 단일 팀, Sonnet, A1. 운영 안정화 후 6팀 분화.

---

## 5. 핵심 순회 실증 (validate.py 출력)

### 심각도 = 비가역성 + 보안등급rank
```
🟢낮음(0)  웹 검색       @ Zone 0·로비
🟠높음(3)  CRM          @ Zone 2·제한
🔴최고(6)  고객 PII      @ Zone 3·통제
🔴최고(6)  결제 실행     @ Zone 3·통제
🔴최고(6)  방화벽·IPS    @ 존 사이의 문
```

### 게이트 결정 = 자산→등급→걸린 정책의 enforcement
```
고객 PII  → sec:L3 → 정책 9개 → {block, require_hitl, log_only}
결제 실행 → sec:L3 → 정책 9개 → {block, require_hitl, log_only}
CRM      → sec:L2 → 정책 7개 → {block, require_hitl, log_only}
```

### 개입 지점 = 조직→페르소나
```
CCC부      → persona:secops
인사팀     → persona:corporate
보안AX사업부 → persona:consulting
```
이 페르소나 노드를 고치면 해당 조직 에이전트 행동이 다음 작업부터 바뀐다.

---

## 6. 개입 모델 — 사람이 EG를 조정하는 법

| 바꾸려는 것 | 수정할 노드/엣지 | 효과 |
|---|---|---|
| 에이전트 행동/말투 | Persona.principles / prohibited | 다음 작업부터 해당 조직 반영 |
| 규정/게이트 강도 | Policy.rule / enforcement | 해당 등급 전 자산에 반영 |
| 자율화 승급 | OPERATES_AT 엣지 A1→A2 | 인간 게이트 조건 완화 |
| 자산 민감도 | CLASSIFIED_AS 재분류 | 심각도·게이트·모델 자동 변경 |
| 부서 모델 교체 | USES_MODEL 엣지 | 해당 부서 에이전트 모델 변경 |

수정 → `validate.py` 검증 → 재주입 → `eg_search` 반영 → `EG_MODE` A/B로 효과 측정. **코드 변경 0**.

---

## 7. AOC와의 접점

이 스키마는 AOC 관제의 근거 데이터가 그대로 된다.
- **섹터 배정** = `Asset -LOCATED_IN-> Zone` (픽셀 오피스 방)
- **심각도 트리아지** = `irreversibility × SecurityLevel.rank`
- **행동 게이트** = `Asset -CLASSIFIED_AS-> SecurityLevel <-APPLIES_TO- Policy.enforcement` + `Skill.risk`
- **정책 위반 탐지** = `OrgUnit.autonomy_level < Asset.sec_rank` (A1이 L3 단독 접근 등)
- **감사 추적(EU AI Act 12조)** = `Task -CONSTRAINED_BY-> Policy` + `pol:auditable-logging`

---

## 8. 확장 로드맵
1. CCC 6팀 분화 (팀별 OrgUnit + Persona).
2. 테넌트 도입 (tenant 속성 + 네임스페이스, `pol:no-cross-tenant` 강제).
3. bastion 33 Skill을 Skill 노드로 로드, `ACTS_ON`으로 Asset 연결.
4. 런타임 축적 시작 (`eg_record_*` → Task/Finding/Observation).
5. 테넌트 #0(자사) 완성 후 고객 온보딩 = 고객 규정/조직/자산을 같은 스키마로 채우는 작업.
