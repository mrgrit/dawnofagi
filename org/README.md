# org/ — 조직·사업·에이전트 레지스트리

> **여기가 기계가 읽는 권위(source of truth)다.** 사업·조직·에이전트는 코드가 아니라 이 YAML 매니페스트로 존재한다.
> 새 사업/본부/팀/에이전트를 추가하는 것 = 파일 하나 추가 + `make registry`.
> 코드 수정은 필요 없다.

## 구조

```
org/
  businesses/<business-id>.yaml       사업 단위 (AOC 플랫폼, AX 컨설팅, 독자모델 개발 …)
  divisions/<division-id>/
    division.yaml                     본부
    <team-id>/
      team.yaml                       팀
      AGENT_TEAM.md                   L2 통제 — 이 팀의 행동양식
      gate.yaml                       (선택) 팀 수준 도구·자율화·예산 경계
  agents/<agent-id>/
    agent.yaml                        에이전트 매니페스트
    SOUL.md                           L4 통제 — 개인 페르소나
    gate.yaml                         (선택) 개인 수준 경계
  gate.yaml                           전사 기본 게이트 (루트)
```

## 새 사업을 추가하는 법 (예: 독자모델 개발 사업)

1. `org/businesses/foundation-model.yaml` 작성 — 아래 스키마 참조
2. 그 사업을 수행할 본부/팀 매니페스트 추가 (기존 본부에 팀만 붙여도 됨)
3. 팀에 `AGENT_TEAM.md` 작성
4. 에이전트 매니페스트 + `SOUL.md` 작성
5. 필요한 업무 SOP 를 `work/<도메인>/<업무>_WORK.md` 에 작성
6. `make registry && make control-lint`

이후 자동으로 파생되는 것:
- **EG 시드** (P1) — OrgUnit/Persona 노드가 매니페스트에서 생성됨
- **에이전트 런타임** (P2) — 매니페스트 등록만으로 기동 대상이 됨
- **AOC 레지스트리·픽셀오피스 층/방** (P3) — 본부=층, 팀=방
- **관제 KPI 집계 단위** (P3)

## 스키마

권위: `packages/dawn_core/schemas/*.schema.json` — `make registry` 가 검증한다.

### `businesses/*.yaml`
| 필드 | 필수 | 설명 |
|---|---|---|
| `id` | ✔ | `kebab-case`. EG 노드 id `biz:<id>` 로 매핑 |
| `name` / `name_en` | ✔ | 한글/영문 명 |
| `status` | ✔ | `active` \| `planned` \| `paused` \| `retired` |
| `mission` | ✔ | 한 줄 |
| `revenue_model` | ✔ | 리스트: `platform_license`, `consulting`, `managed_service`, `maintenance`, `staffing`, `model_dev`, `research_grant` … |
| `owning_divisions` | ✔ | 이 사업을 수행하는 본부 id 리스트 |
| `target_segments` | | 고객 세그먼트 (예: `university`, `security`, `marketing`) |
| `roadmap` | | `{phase, goal, status}` 리스트 |
| `kpis` | | `{name, target, unit}` 리스트 |
| `tenant_model` | | `single` \| `multi` — 멀티테넌트 대상인지 |
| `data_sensitivity` | | 최고 민감도 `L0`~`L3` |

### `divisions/*/division.yaml`
`id`, `name`, `mission`, `color`(픽셀오피스), `businesses`(참여 사업 id), `teams`(팀 id 리스트), `zone`(el34 존), `autonomy_default`

### `divisions/*/*/team.yaml`
`id`, `division`, `name`, `mission`, `persona_default`, `agents`, `work_domains`, `zone`, `escalation_to`

### `agents/*/agent.yaml`
`id`, `team`, `name`, `role`(`orchestrator`|`worker`|`verifier`|`daemon`), `persona`, `works`(수행 가능한 `*_WORK.md` id), `autonomy`, `model_hint`, `tools`, `status`

## 규칙

- **id 는 불변.** 한번 붙인 id 는 바꾸지 않는다 (EG 노드·감사 이력이 물고 있다).
- **참조 무결성.** 존재하지 않는 팀/사업/업무를 가리키면 `make registry` 가 실패한다.
- **`status: planned` 은 유효하다.** 아직 시작 안 한 사업도 매니페스트로 등록해 두면 조직 설계가 미리 검증된다.
- 사람이 읽는 요약표는 `COMPANY.md §5` 에 있다. 매니페스트를 바꾸면 그 표도 갱신한다.
