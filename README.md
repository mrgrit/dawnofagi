# the dawn of AGI (다노파기)

> **AI 역사가 AGI로 가는 길에 필요한 것들을 만들어 공급하고 확산한다.**

에이전트가 스스로 일하고, 스스로를 관제하고, 스스로 개선하는 회사.
사람은 코드나 프롬프트를 만지지 않는다 — **EG(Experience Graph)와 통제 평면 문서를 조정함으로써만 개입한다.**

자사가 첫 고객이다. 여기서 도는 것만 판다. (테넌트 #0 = 레퍼런스 구현)

---

## 5분 만에 시작

```bash
git clone https://github.com/mrgrit/dawnofagi.git && cd dawnofagi
./bootstrap.sh                 # 깨끗한 리눅스 한 대면 이거 하나로 끝난다
source .venv/bin/activate
make help
```

`bootstrap.sh` 가 하는 일: 시스템 패키지 · Node · Docker · Python venv · dawn-core ·
gitleaks · git 훅 · `.env` 생성 · 검증(레지스트리·통제 평면·테스트·el34)까지 전부.
Ubuntu/Debian · RHEL/Rocky/Fedora · Arch 지원. `--no-el34 --no-docker --no-node --no-sudo` 로 부분 실행 가능.

---

## 이 회사를 어떻게 조종하는가

에이전트는 프롬프트로 조종하지 않는다. **5개의 손잡이**로 조종한다.

| 손잡이 | 파일 | 범위 | 예: 무엇을 바꾸나 |
|---|---|---|---|
| **L1 회사** | [`COMPANY.md`](COMPANY.md) | 전사 전부 | "전사 금지 하나 추가" |
| **L2 팀** | `org/divisions/<본부>/<팀>/AGENT_TEAM.md` | 그 팀 전체 | "이 팀이 너무 공격적이야" |
| **L3 업무** | `work/<도메인>/<업무>_WORK.md` | 그 업무 수행 시 | "이 절차가 틀렸어" |
| **L4 개인** | `org/agents/<id>/SOUL.md` | 그 에이전트 1명 | "얘만 좀 조용히 시켜" |
| **⛔ 게이트** | `org/**/gate.yaml` | 도구·자율화·예산 | "이 도구 못 쓰게 해" |

**불변식 — 단조 축소**: 하위는 상위를 **좁힐 수만 있고 넓힐 수 없다.**
L4 SOUL이 "이 도구도 써도 된다"고 써도 막힌 것은 막힌 것이다. 컴파일러가 기계적으로 강제한다.

```bash
make registry               # 조직도 + 정합성
make gate A=corp-admin-clerk-01     # 이 에이전트가 실제로 뭘 할 수 있는지
make prompt A=ccc-soc-triage-01     # 실효 시스템 프롬프트 (4계층 합본)
make control-lint           # Control Readiness Score — 80점 미만이면 CI 실패
```

자세히: [`docs/governance/CONTROL_PLANE.md`](docs/governance/CONTROL_PLANE.md)

---

## 새 사업을 붙이는 법

사업·조직·에이전트는 **코드가 아니라 매니페스트로 존재한다.** 코드 수정 없이 늘어난다.

```bash
# 1. 사업 매니페스트
vim org/businesses/foundation-model.yaml      # 이미 planned 로 등록돼 있다

# 2. 조직 (기존 본부에 팀만 붙여도 됨)
vim org/divisions/aoc/<새팀>/team.yaml
vim org/divisions/aoc/<새팀>/AGENT_TEAM.md    # L2
vim org/divisions/aoc/<새팀>/gate.yaml        # 경계

# 3. 에이전트
vim org/agents/<id>/agent.yaml
vim org/agents/<id>/SOUL.md                   # L4

# 4. 업무 SOP
vim work/<도메인>/<업무>_WORK.md               # L3

# 5. 검증
make registry && make control-lint
```

여기서 자동으로 파생되는 것: EG 시드(P1) · 에이전트 런타임(P2) · 관제 레지스트리와
픽셀오피스 층/방(P3) · KPI 집계 단위(P3).

자세히: [`org/README.md`](org/README.md)

---

## 저장소 지도

```
COMPANY.md                    L1 — 회사 헌법. 모든 에이전트에 주입된다
org/                          조직·사업·에이전트 레지스트리 (YAML = 권위)
  businesses/                   사업 — 새 사업은 여기 YAML 추가만으로 편입
  divisions/                    본부/팀 + AGENT_TEAM.md(L2) + gate.yaml
  agents/                       에이전트 + SOUL.md(L4)
  tools.yaml                    도구 카탈로그 (namespace.action + 위험도)
work/                         L3 — 재사용 가능한 업무 SOP (*_WORK.md)
packages/dawn_core/           레지스트리 로더 · 게이트 병합 · 통제 평면 컴파일러 · 린터
eg/                           Experience Graph — 회사의 뇌            (P1)
agents/                       에이전트 하네스 · 워커 런타임            (P2)
aoc/                          관제 시스템 (수집·탐지·트리아지·대응)     (P3)
apps/                         홈페이지 · 그룹웨어 · 픽셀오피스          (P4/P5)
infra/                        배포 · el34 연동
docs/
  START_HERE.md                 구축 순서 P0→P6
  context/                      참조 문서 (헌장·AOC 아키텍처·조직·스택·컨벤션)
  instructions/                 구축 지시문 P0~P6
  governance/CONTROL_PLANE.md   통제 평면 사용법
BUILD_LOG.md                  진행 기록      QUESTIONS.md  사람에게 묻는 큐
```

---

## 인프라 — el34 위에 짓는다

el34 는 4-tier 세그먼트 보안 실습/운영 인프라이자 **이 회사의 첫 관제 대상**이다.

| 존 | CIDR | 주요 자산 |
|---|---|---|
| ext | 10.20.30.0/24 | bastion, attacker |
| pipe | 10.20.31.0/24 | fw, ips — 존 사이의 문(PEP) |
| dmz | 10.20.32.0/24 | web, Wazuh SIEM, portal, **Assessor** |
| int | 10.20.40.0/24 | 취약 웹(고객 모사), DB |
| user | 10.20.33.0/24 | Windows 엔드포인트 |

```bash
make el34-assessor-up      # Assessor 기동 (el34 저장소는 수정하지 않는다)
make health                # 도달성 + 인증 확인
```

---

## 구축 순서

| 단계 | 내용 | 상태 |
|---|---|---|
| **P0** | 부트스트랩 — 모노레포·CI·시크릿·el34 연결·**통제 평면** | ✅ 완료 |
| P1 | Experience Graph — 회사의 뇌 | ⏸ [Q1](QUESTIONS.md) 대기 |
| P2 | 에이전트 하네스·루프·행동 게이트 | ⬜ |
| P3 | AOC 관제 시스템 · 픽셀 오피스 | ⬜ |
| P4 | 홈페이지 · 그룹웨어 | ⬜ |
| P5 | 업무 시스템 (CRM·문서·프로젝트·경리) | ⬜ |
| P6 | 통합 · 레드팀 검증 · 자사 운영 개시 | ⬜ |

각 단계의 DoD와 자기검증 결과는 [`BUILD_LOG.md`](BUILD_LOG.md).

---

## 명령

```bash
make help            # 전체 목록
make check           # lint · test · registry · compile · control-lint  (CI와 동일)
make verify          # P0 자기검증 (DoD + 개입·게이트 실증)
make secrets         # 저장소 전체 시크릿 스캔
make bundles         # 통제 평면 번들 생성 → var/control-plane/  (P2 하네스 입력)
```

---

## 규칙 (예외 없음)

1. 시크릿 하드코딩 금지 — gitleaks pre-commit 훅이 차단한다
2. 파괴적 작업은 HITL 게이트
3. 최소권한 — 매니페스트에 없는 도구는 못 쓴다
4. 테넌트 격리 — 크로스테넌트 접근 코드 금지
5. L3(인사·재무·개인정보)는 로컬 모델 전용
6. 공격코드 금지 — 레드팀조차 스코프는 `zone:int` 취약웹 한정
7. 행동을 바꾸려면 코드가 아니라 EG·통제 평면 문서를 고친다
8. 모호하면 멈추고 `QUESTIONS.md` 에 남긴다

전문: [`docs/context/05_conventions.md`](docs/context/05_conventions.md) · [`CLAUDE.md`](CLAUDE.md)
