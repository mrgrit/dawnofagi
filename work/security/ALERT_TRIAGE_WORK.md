---
id: security/alert-triage
name: 알럿 트리아지
domain: security
owner_team: itops-ccc
risk: MED
autonomy_max: A2
version: 1
---

# ALERT_TRIAGE_WORK.md — 알럿 트리아지

> **통제 평면 L3.** 이 업무를 수행할 때만 주입된다. 여러 팀·여러 에이전트가 참조할 수 있다.

## 1. 목적

들어온 알럿을 **올릴 것 / 닫을 것 / 즉시 대응할 것** 으로 가른다.
이 업무의 성공은 "많이 처리했다"가 아니라 **"오탐율과 미탐율이 함께 낮다"** 이다.

## 2. 트리거 (언제 시작되나)

**이벤트 구동.** 상시 폴링 금지.

| 트리거 | 소스 |
|---|---|
| Wazuh 알럿 생성 (level ≥ 7) | SIEM 웹훅 |
| Suricata IPS 시그니처 매치 | IPS 이벤트 스트림 |
| ModSecurity WAF 차단 | WAF 로그 훅 |
| 에이전트 텔레메트리 이상 (스텝 폭주·토큰 급증·비정상 도구 시퀀스) | AOC 이상탐지기 |
| 사람의 수동 제보 | 그룹웨어 |

## 3. 입력

| 필드 | 필수 | 설명 |
|---|---|---|
| `alert_id` | ✔ | 소스 시스템의 고유 ID |
| `source` | ✔ | `wazuh` \| `suricata` \| `modsec` \| `aoc` \| `manual` |
| `raw` | ✔ | 원문 (마스킹 전 원본은 참조만, 산출물엔 마스킹본) |
| `assets` | ✔ | 관련 자산 id 리스트 (없으면 IP/호스트에서 해석) |
| `observed_at` | ✔ | ISO8601 KST |

## 4. 절차

### 단계 1 — 참조 (`eg_search`)
```
질의: "이 알럿 시그니처 / 이 자산 / 이 소스 IP 에 대한 과거 판단"
```
- 동일·유사 알럿의 **과거 결론**을 조회한다. 있으면 그 판단과 일관되게 간다.
- 다르게 판단할 거면 **왜 다른지**를 반드시 쓴다.
- 관련 `Insight` 노드가 있으면 그 지침을 따른다.

### 단계 2 — 자산·등급 해석
```
자산 → LOCATED_IN → Zone
자산 → HAS_LEVEL → SecurityLevel
```
- 자산의 `SecurityLevel.rank` 와 `irreversibility` 를 **EG에서 조회한다. 추측 금지.**
- 자산을 특정 못 하면 → 단계 5 에스컬레이션.

### 단계 3 — 심각도 산정
```
severity = f(Asset.irreversibility, SecurityLevel.rank)
           read < write < execute < irreversible
```
- 비가역 × 광역 = 최고 등급 + 사전 HITL.
- 산정 **근거를 산출물에 쓴다** (어느 자산, 어느 등급, 어느 비가역성).

### 단계 4 — 판정
| 판정 | 조건 | 다음 |
|---|---|---|
| `close` | 과거 전례상 오탐 + 자산 등급 낮음 + 증거상 정상 | 근거 기록 후 종결 |
| `escalate` | 심각도 MED 이상 / 증거 불충분 / **처음 보는 유형** | `security/incident-investigation` 로 |
| `respond_now` | 진행 중 침해 + 심각도 HIGH | 대응 플레이북 + HITL 동시 |

**처음 보는 유형은 무조건 `escalate`.** 자동 종결 권한이 없다.

### 단계 5 — 검증
- 판정을 **다른 에이전트**가 반증 시도한다 (검증자 ≠ 생산자).
- 반증 성공 시 단계 4로 되돌아간다.

### 단계 6 — 기록 (`eg_record`)
- `Experience`: 무엇을 근거로 어떻게 갈랐는지
- `close` 한 건은 **닫은 이유를 반드시** 남긴다
- 반복 패턴이면 `Insight` 로 정제 제안

## 5. 사용 도구

`eg_search`, `eg_record`, `skill_preview`, `siem_query`, `suricata_query`, `waf_query`, `read_file`, `write_file`

> `run_command`·`docker_inspect` 는 조사(L2) 업무에서만. 트리아지는 읽기 위주다.

## 6. 위험도 · 게이트

| 액션 | 위험도 | 게이트 |
|---|---|---|
| 조회·판정·기록 | LOW | 없음 |
| `escalate` | LOW | 없음 |
| `respond_now` 의 대응 액션 | HIGH | **HITL 필수** (방화벽·컨테이너·자격증명 전부) |
| 알럿 대량 일괄 종결 | MED | 10건 초과 일괄 종결은 HITL |

## 7. 완료 조건 (DoD)

- [ ] 단계 1 `eg_search` 를 실행했고 그 결과가 판단에 반영됐다
- [ ] 자산·등급을 EG에서 조회했다 (추측 아님)
- [ ] 심각도 산정 근거가 산출물에 있다
- [ ] 판정이 `close`/`escalate`/`respond_now` 중 하나로 확정됐다
- [ ] 증거(로그 원문·스팬 ID)가 첨부됐다. 출처 없는 주장이 없다
- [ ] 검증자가 반증을 시도했다
- [ ] `eg_record` 완료. `close` 면 닫은 이유 포함
- [ ] 개인정보가 마스킹됐다

## 8. 실패 시 처리

| 실패 | 처리 |
|---|---|
| 자산을 특정 못 함 | `escalate`. 추측해서 판정하지 않는다 |
| EG 조회 실패 | 작업 중단 + 사람 통보. EG 없이 판정하지 않는다 |
| 증거 접근 불가 (로그 유실 등) | `escalate` + "증거 미확보" 명시 |
| 서킷 브레이커 발동 (스텝/토큰 초과) | 중단 + 현재까지 상태를 케이스에 남기고 사람 통보 |
| 검증자가 반증 성공 | 단계 4 재실행. 2회 반증되면 `escalate` |

## 9. 산출물 템플릿

```markdown
## 케이스 <case_id>
**판정**: close | escalate | respond_now
**심각도**: <LOW|MED|HIGH|CRIT> — 근거: 자산 <asset_id>(등급 <Lx>, 비가역성 <read|write|execute|irreversible>)

### 요약
<3줄 이내>

### 증거
- <source>:<line/span_id> — <원문 인용(마스킹)>

### 타임라인
| 시각(KST) | 사건 |
|---|---|

### 영향범위
자산: / 존: / 테넌트:

### 판단
확인된 것: <...>
추정: <...>
과거 전례: <experience_id> — <같게/다르게 판단한 이유>

### 후속 제안
<탐지룰 / 가드레일 / EG 보강>
```

## 10. KPI

| 지표 | 목표 |
|---|---|
| 오탐율 (닫아야 할 걸 올림) | ↓ |
| 미탐율 (올려야 할 걸 닫음) | ↓ — **0에 수렴해야 함** |
| MTTD (탐지까지) | ↓ |
| 전례 일관성 (같은 알럿 같은 판단) | ↑ |
