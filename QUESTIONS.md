# QUESTIONS.md — 사람에게 묻는 큐

> 규칙(05_conventions "모호하면 멈춤"): 스펙이 모호하거나 파괴적 결정·보안 판단이 필요하면
> 추측으로 진행하지 않고 여기에 남긴다. 답이 오면 `상태`를 `해결`로 바꾸고 반영 위치를 적는다.

| # | 상태 | 단계 | 질문 | 차단 여부 |
|---|---|---|---|---|
| Q1 | **대기** | P1 | EG 스키마 문서 5종이 없다 (아래 상세) | **P1 차단** |
| Q2 | 대기 | P0 | GitHub PAT 노출 — 폐기·재발급 필요 (+ `workflow` 스코프) | 비차단 |
| Q3 | 대기 | P1 | 중간 산출물 Drive 폴더 접근 불가 | P1 부분 차단 |
| Q4 | 참고 | P0 | el34 Assessor 는 `profiles: [assessor]` 로 기본 미기동 | 해결됨(§Q4) |

---

## Q1 — EG 스키마 문서 5종 부재 (**P1 차단**)

`docs/instructions/P1_experience_graph.md` 가 "먼저 읽어라"로 지정한 아래 5개가 `initial/` 에 없다:

| 파일 | 용도 |
|---|---|
| `docs/context/02_eg_schema.md` | EG 스키마 설계 전문 |
| `eg/schema.json` | 노드/엣지 타입 정의 |
| `eg/seed/*.json` | 초기값 시드 (01_foundation.json 등) |
| `eg/BOOTSTRAP.md` | 주입 절차 (경로 A: bastion_graph.db 확장) |
| `eg/validate.py` | 참조 무결성·커버리지·순회 검증 |

또한 `00_charter.md`·`01_aoc_architecture.md`·`03_org_personas.md` 가 `02_eg_schema.md` 를 참조한다.
P1 DoD 는 "validate.py 오류 0 (**노드 74·엣지 136 규모**)"를 요구하는데, 그 구체 목록이 원본에만 있다.

**선택지**
- **(A)** 원본 5종을 제공한다 → 그대로 사용. *권장*
- **(B)** CC가 참조 문서(00·01·03·04·05)에서 스키마를 **역설계**해 8종 거버넌스 노드
  (OrgUnit·Persona·Policy·SecurityLevel·Zone·Asset·AutonomyLevel·ModelPolicy)와
  엣지(APPLIES_TO·HAS_LEVEL·LOCATED_IN·USES_MODEL 등)를 새로 정의하고,
  `org/` 레지스트리에서 시드를 자동 생성한다. 노드/엣지 수는 74/136 과 달라질 수 있다.

> **현재 가정**: 답이 없으면 **(B)** 로 진행하되, 생성한 스키마를 `docs/context/02_eg_schema.md` 로
> 저장하고 원본 도착 시 교체 가능하도록 로더를 분리한다. 이 가정은 P1 착수 시점에 재확인한다.

---

## Q2 — GitHub PAT 이 평문으로 전달됨 (비차단, 그러나 즉시 조치 권장)

지시 중 GitHub PAT(`ghp_…`)이 평문으로 전달됐다.
저장소에는 **커밋하지 않았고**(`.gitignore` + gitleaks 훅으로 차단), 로컬 git credential 로만 사용했다.

**권고**: 해당 토큰을 **폐기(revoke)하고 재발급**하라. 대화·로그에 남은 값은 유출된 것으로 간주해야 한다.
재발급 후에는 `.env` 의 `GITHUB_TOKEN` 으로만 주입한다 (`.env.example` 참조).

현재 토큰은 로컬 credential store(`~/.git-credentials-dawnofagi`, 권한 600, **저장소 밖**)에만 있다.

**재발급 시 `workflow` 스코프를 함께 부여하라.** 현재 토큰에는 없어서
`.github/workflows/ci.yml` 을 push 할 수 없었다. CI 정의는 `infra/ci/github-actions-ci.yml` 에
보관돼 있고, 스코프가 생기면:

```bash
make ci-enable
git add .github/workflows/ci.yml && git commit -m "[P0][DoD-3] CI 활성화" && git push
```

---

## Q3 — 중간 산출물 Drive 폴더 접근 불가

| 폴더 | 상태 |
|---|---|
| 대학 AX 예시 (`1eZSc…`) | ✔ 접근 가능 — 파일 목록을 `docs/references/ax-university/INDEX.md` 에 색인 |
| 중간 산출물 (`1UscG…`) | ✘ 로그인 필요 — 접근 불가 |

Q1 의 EG 스키마가 여기에 있을 가능성이 높다.
**권고**: 링크 공유 설정을 "링크가 있는 모든 사용자"로 바꾸거나, 파일을 저장소에 직접 넣어 달라.

---

## Q4 — el34 Assessor 기본 미기동 (해결)

`~/el34/docker-compose.yaml:334` 의 assessor 서비스는 `profiles: [assessor]` 라서 기본 `up` 에 포함되지 않는다.
또 호스트 포트 바인딩이 `192.168.0.151:9201` 인데 이 호스트 IP 는 `192.168.0.108` 이다.

**조치**: 헬스체크 스크립트(`infra/el34/healthcheck.py`)는 dmz 브리지의 컨테이너 IP
`10.20.32.55:8000` 을 우선 사용하고, 미기동 시 기동 명령을 안내하도록 만들었다.
`make health` 로 확인한다.
