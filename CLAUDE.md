# CLAUDE.md — Claude Code 상시 지침

> **the dawn of AGI (다노파기)** — "AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다."

## 작업 시작 전 반드시

1. [`COMPANY.md`](COMPANY.md) — 회사 헌법. 모든 행동의 최상위 제약.
2. [`docs/START_HERE.md`](docs/START_HERE.md) — 구축 순서(P0→P6).
3. 지금 하는 단계의 지시문 `docs/instructions/Pn_*.md` 와 그 문서가 지정한 `docs/context/` 참조 문서.
4. [`BUILD_LOG.md`](BUILD_LOG.md) 최하단 — 어디까지 왔는지.
5. [`TODO.md`](TODO.md) — 남은 일. **누가** 할 수 있는지가 항목마다 적혀 있다.

## 절대 규칙 (05_conventions.md 요약 — 예외 없음)

| # | 규칙 |
|---|---|
| 1 | **시크릿 하드코딩 금지.** API키·비밀번호·토큰은 환경변수/볼트. `.env` 는 커밋 금지. gitleaks pre-commit 훅이 차단한다. |
| 2 | **파괴적 작업은 게이트.** 삭제·배포·결제·외부발송·방화벽변경은 HITL 승인 또는 명시적 확인 후에만. |
| 3 | **최소권한.** 각 에이전트·서비스는 필요한 범위만. el34 존 경계를 넘으면 pipe 게이트를 통과하고 로그를 남긴다. |
| 4 | **테넌트 격리.** 자사 = 테넌트 #0. 크로스테넌트 접근 코드 금지. |
| 5 | **L3 로컬 처리.** 인사·재무·개인정보는 클라우드 모델 전송 금지. 로컬 모델만. |
| 6 | **공격코드 금지.** 레드팀조차 스코프는 el34 취약웹(`zone:int`) 한정. |
| 7 | **EG 우선.** 에이전트 행동을 바꿀 땐 코드가 아니라 EG(Persona/Policy)와 통제 평면 문서를 고친다. |
| 8 | **모호하면 멈춤.** 추측으로 진행하지 말고 `QUESTIONS.md` 에 남긴다. |
| 9 | **도구는 만지는 자산을 선언한다.** `org/tools.yaml` 의 `touches:` — 빠뜨리면 심각도가 0 으로 계산돼 가장 위험한 도구가 가장 안전해 보인다. 자산이 없는 게 맞으면 `touches: []` 로 명시. |

## 커밋 규칙

- 기능 단위로 **작게** 커밋한다.
- 메시지 형식: `[Pn][DoD-x] 한 줄 요약`
  - 예: `[P0][DoD-3] gitleaks pre-commit 훅 추가`
- 새 외부 의존성을 추가하면 이유와 라이선스를 `BUILD_LOG.md` 에 기록한다.
- 각 마일스톤(Pn) 완료 시 DoD 체크 + 자기검증 결과를 `BUILD_LOG.md` 에 append 하고 push 한다.

## 저장소 지도

```
COMPANY.md            L1 통제 — 전사 에이전트 헌법 (읽어라)
TODO.md               남은 일 — 누가 할 수 있는지까지
QUESTIONS.md          사람의 결정이 필요한 질문
org/                  조직·사업·에이전트 레지스트리 (YAML 매니페스트 = 권위)
  businesses/           사업 단위 — 새 사업은 여기 YAML 추가만으로 편입
  divisions/            본부/팀 + AGENT_TEAM.md(L2) + gate.yaml
  agents/               에이전트 + SOUL.md(L4)
work/                 L3 — 구조화된 업무 단위 SOP (*_WORK.md)
eg/                   Experience Graph — 회사의 뇌 (P1)
agents/               에이전트 하네스·워커 런타임 (P2)
aoc/                  관제 시스템 — 수집·탐지·트리아지·대응·킬스위치·KPI (P3)
apps/pixel-office/    픽셀 오피스 관제 콘솔 — index.html 파일 1개, 의존성 0 (P3)
apps/groupware/       공개 홈페이지 + 사내 그룹웨어 — 승인 관문·EG 조정 UI (P4)
biz/                  업무 시스템 — 문서·CRM·프로젝트·경리 (P5)
ops/                  통합·레드팀·인시던트 리허설·운영 (P6)
packages/dawn_core/   공용 라이브러리 — 레지스트리 로더·통제 평면 컴파일러·설정
infra/                배포·el34 연동 스크립트
docs/                 참조 문서(context) + 구축 지시문(instructions) + 거버넌스
```

## 자주 쓰는 명령

```bash
make setup          # 가상환경 + 의존성 + pre-commit 훅
make lint           # ruff
make test           # pytest
make control-lint   # 통제 평면 검증 + Control Readiness Score
make registry       # 조직·사업 레지스트리 검증 및 요약
make health         # el34 Assessor 헬스체크
make check          # lint + test + control-lint + registry (CI와 동일)
make aoc            # 관제 1회전 — 수집 → 탐지 → 트리아지 (실업무는 judge 자동)
make office-bg      # 픽셀 오피스 — 3D 사옥 (:8800)
make office-preview # 화면을 PNG 로 (브라우저 없는 서버용)
make web-bg         # 홈페이지(:8810) + 그룹웨어(:8811)
make biz-egcheck    # 업무 데이터 ↔ EG 자산 정합성
make ops-status     # 전 계층 현황 한 장
make redteam        # 오펜시브 레드팀 + 탐지 커버리지
```

## 진행 상황

현재 단계는 `BUILD_LOG.md` 최하단을 보라. 앞 단계의 DoD를 충족하지 못했으면 다음으로 넘어가지 마라.

P0~P7 은 완료됐다. 남은 일은 [`TODO.md`](TODO.md) — **사람만 할 수 있는 것과 에이전트가
할 수 있는 것이 갈라져 있다.** 막힌 항목을 붙잡고 있지 말고 그 구분을 먼저 보라.
