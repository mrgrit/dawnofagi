# 운영 러너북 — 사람 운영자용

**하루에 세 번 보고, 문제가 생기면 여기로 돌아온다.**

관련: [`CONTROL_PLANE.md`](CONTROL_PLANE.md) 사전 통제 ·
[`AOC_OPERATIONS.md`](AOC_OPERATIONS.md) 관제 ·
[`PORTAL_GUIDE.md`](PORTAL_GUIDE.md) 그룹웨어

---

## 0. 아침에 하는 것 (5분)

```bash
make gpu-check           # 1. 사내 GPU 살아 있나 — 죽어 있으면 L3 업무가 전부 멈춘다
make health              # 2. el34 Assessor
dawn-ops status          # 3. 전 계층 현황 한 장
dawn-aoc cases           # 4. 밤사이 열린 케이스
make hitl                # 5. 승인 대기 (사람을 기다리는 에이전트)
```

**우선순위는 5번이 1번이다.** 승인 대기가 쌓인다는 건 에이전트가 멈춰 서 있다는 뜻이다.

`make aoc` 를 돌리면 **실업무 run 은 LLM-judge 가 자동으로 채점**한다 (드릴·레드팀은
채점하지 않는다 — 일부러 실패하는 실행이라 할루시네이션율만 오염된다).
판정에는 사내 GPU 가 필요하므로 1번이 죽어 있으면 품질 축도 같이 멈춘다.

---

## 1. 서비스 기동·중지

| 무엇 | 기동 | 중지 | 주소 |
|---|---|---|---|
| 픽셀 오피스 (관제) | `make office-bg` | `make office-stop` | `:8800` |
| 공개 홈페이지 | `make web-bg` | `make web-stop` | `:8810` |
| 사내 그룹웨어 | `make web-bg` | `make web-stop` | `:8811` |

로그: `var/aoc/serve.log` · `var/web/site.log` · `var/web/portal.log`

**주의**: 세 서비스 모두 `0.0.0.0` 에 열린다. 신뢰하는 망에서만 띄운다.
`HOST=127.0.0.1` 로 로컬 전용으로 묶을 수 있다.

전제 조건 하나: **VPN**. 사내 GPU(`211.170.162.109:11434`)가 없으면 L3 업무가
전부 막힌다(클라우드 폴백 없음 — 그게 설계다).

```bash
make vpn                 # 스플릿 라우팅 — el34 랩(10.20.x)은 보존된다
make vpn-status
```

---

### 외부에서 접속해야 할 때

```bash
make tunnel              # 공개 홈페이지만 — 기본값이 이것인 이유가 있다
make tunnel-status       # 지금 열려 있는 것
make tunnel-down         # 닫는다
```

**사람이 실행한다.** 에이전트는 외부 노출을 만들지 않는다 — 공개 범위 변경은
`comm.external_send`·`sec.firewall_change` 와 같은 급이다.

| 열 것 | 인증 | 판단 |
|---|---|---|
| 공개 홈페이지 :8810 | 없음 | **열어도 된다.** 원래 외부인이 보는 화면이다 |
| 사내 그룹웨어 :8811 | 로그인 | 열면 무차별 대입 표적. `DAWN_PORTAL_HTTPS=1` 로 재기동 |
| 픽셀 오피스 :8800 | **없음** | URL 을 아는 누구나 전 에이전트 텔레메트리를 본다. 상시 노출은 Access 를 앞에 붙인 뒤에 |

퀵 터널은 프로세스가 죽으면 URL 이 사라진다. 상시 운영은 네임드 터널 +
Cloudflare Access — [`infra/cloudflare/README.md`](../../infra/cloudflare/README.md).

---

## 2. 일상 — 에이전트에게 일 시키기

에이전트는 **이벤트로 기동한다.** 상시 폴링이 아니다.

```bash
# 홈페이지 문의 → CRM → 에이전트
make biz-intake                                  # 접수함 흡수
make biz-run W=inquiry S=<문의id>                 # 처리 (초안까지)

# 경비 (L3 — 로컬 모델 + HITL)
make biz-run W=expense S=EXP-2026-0801-002

# 프로젝트 조율
make biz-run W=project S=AOC_PLATFORM

# 이벤트로 깨우기 (외부 시스템이 하는 방식)
make biz-emit E=crm.inquiry.new P='{"inquiry_id":1}'
```

결과 확인: `make biz-crm ID=<id>` · `make biz-acct ID=<request-id>`

---

## 3. 사람이 개입하는 두 가지 통로

### 3-1. 승인 (그룹웨어 `:8811` → 승인 큐)

에이전트가 비가역·고심각 행동 앞에서 멈춰 기다린다.

- 승인자는 **그 에이전트의 조직이거나 상위 조직**이어야 한다.
- 최고 심각도(6)는 `hitl.approve.critical` 이 따로 필요하다.
- **사유는 감사 로그에 남는다.** 다음 사람이 읽는다.
- 한 번 판정된 요청은 재판정할 수 없다.

CLI 로도 볼 수 있다: `make hitl`

### 3-2. EG 조정 (그룹웨어 → EG 조정)

**에이전트 행동을 바꾸는 유일한 정식 경로다.** 코드를 고치지 않는다.

```
수정 → 스냅샷 → 검증(eg/validate.py) → 재주입 → 감사
        검증 실패 시 시드 자동 롤백. DB 는 손도 안 댄다.
```

바꾼 뒤 확인:

```bash
make prompt A=<agent-id>      # 시스템 프롬프트에 반영됐나
make eg-org O=org:ga          # 조직 프로파일
make eg-routing               # 모델 라우팅이 바뀌었나
```

---

## 4. 케이스가 떴다

```bash
dawn-aoc cases                       # 목록
dawn-aoc cases <case-id>             # 상세 (탐지 근거·자산·정책·권고)
dawn-aoc replay <trace-id>           # 그때 무슨 일이 있었나
dawn-aoc respond <case-id>           # 권고 플레이북 집행
```

**대응은 두 갈래로 갈린다.**

| 되돌릴 수 있는 것 → 즉시 집행 | 되돌릴 수 없는 것 → 승인 큐 |
|---|---|
| `pause` `isolate` `block_tool` `rollback` `escalate_hitl` | `kill` `revoke_credentials` `report_regulator` |

`rollback` 은 **지우지 않는다.** `var/aoc/quarantine/<case>/` 로 옮긴다 — 증거다.

### 심각도 읽는 법

```
심각도 = 그 행동의 비가역성 × 건드린 자산의 보안등급
```

읽기(0) < 쓰기(1) < 실행(2) < 비가역(3), L0(0) < L1(1) < L2(2) < L3(3).
**미분류 자산은 최고 등급으로 친다** — "모르면 안전"이 사고를 만든다.

---

## 5. 급할 때 — 킬 스위치

```bash
dawn-aoc control                                       # 현재 상태
dawn-aoc control pause   <agent-id> --by human:<이름>   # 멈춰 (권한은 유지)
dawn-aoc control isolate <agent-id> --by human:<이름>   # 격리
dawn-aoc control kill    <agent-id> --by human:<이름>   # 종료 + A0 강등
dawn-aoc control revoke  <agent-id> --by human:<이름>   # 자격증명 회수
dawn-aoc control resume  <agent-id> --by human:<이름>   # 해제 (killed 는 사람만)
```

**`pause` 와 `kill` 은 다르다.** 멈추는 것(stop)과 권한을 뺏는 것(de-authorize)은
다른 행동이다. "일단 멈춰"가 곧 "권한 회수"가 되면, 사소한 오작동에도 재기동
비용이 커져 **결국 아무도 멈추지 않게 된다.**

에이전트는 이 계층을 못 건드린다 — `ctl.*` 는 게이트 deny + 실행부 부재, 두 겹.

---

## 6. 장애 대응

| 증상 | 먼저 볼 것 | 조치 |
|---|---|---|
| L3 업무가 전부 실패 | `make gpu-check` | VPN 재연결 (`make vpn`). **클라우드로 우회하지 않는다** |
| 에이전트가 아무것도 안 함 | `dawn-aoc control` | `paused`/`killed` 면 사유 확인 후 `resume` |
| 승인 요청이 폭주 | `make aoc-kpi` HITL 개입률 | 팀 `gate.yaml` 의 `hitl.require_on` 을 좁힌다 |
| 에이전트가 자꾸 틀림 | `make aoc-judge` | groundedness 낮으면 EG 페르소나 또는 `*_WORK.md` 보강 |
| 같은 도구만 반복 | 케이스 `tool_loop` | `pause` → 업무 지시를 좁혀 재실행 |
| 포털·콘솔이 안 열림 | `ss -ltn \| grep 88` | 안 떠 있으면 `make office-bg` / `make web-bg` |
| 페이지는 뜨는데 데이터가 빔 | `dawn-aoc collect` | 스팬이 0 이면 에이전트가 안 돈 것 |
| 업무 데이터가 관제에 안 잡힘 | `make biz-egcheck` | 자산 미선언이면 EG 시드에 자산 추가 후 `make eg-load` |
| EG 를 고쳤는데 안 바뀜 | 화면의 "거부됨" 확인 | 검증 실패 시 자동 롤백된 것. 오류 메시지를 보라 |

---

## 7. 정기 점검

### 주 1회

```bash
make check               # lint · test · 레지스트리 · 통제평면 · EG · 업무자산
make aoc-kpi             # KPI + 자율화 승급/강등 검토
dawn-ops redteam         # 탐지 커버리지 (놓친 것마다 보강 제안이 나온다)
```

**자율화 승급은 KPI 충족 시에만.** 감으로 올리지 않는다.
**강등은 critical 인시던트 즉시** — 조건 없다.

**KPI 는 실업무만 센다.** 드릴·레드팀 run 은 일부러 차단되어 완료에 도달하지 않으므로
같이 세면 "잘 막혔다"가 "일을 못 한다"로 뒤집혀 읽힌다. KPI 마다 몇 건을 뺐는지
표시되니(`실업무 외 drill 21, redteam 6 제외`) 그 숫자도 같이 봐라 — 실업무 표본이
너무 적으면 KPI 자체가 아직 의미가 없다는 뜻이다.

### 월 1회

```bash
dawn-ops rehearsal       # 인시던트 3축 + 비가역 대응 3종 실증
dawn-ops tenant          # 멀티테넌트 격리 점검
make eg-snapshot LABEL=monthly
make secrets             # 저장소 전체 시크릿 스캔
```

**리허설에서 안 눌러본 버튼은 사고 때도 안 눌린다.** 월 1회는 최소선이다.

### 분기 1회

```bash
dawn-ops e2e --live      # 전 구간 연결 확인
make verify              # P0~P6 전체 자기검증
```

---

## 8. 새 사업·새 조직·새 에이전트

**사업은 플러그인이다.** 코드를 고치지 않는다.

```bash
# 1. 사업 추가
vim org/businesses/<new>.yaml
make registry                      # 검증 — 홈페이지·프로젝트가 자동으로 따라 붙는다

# 2. 조직 추가
vim org/divisions/<div>/<team>/team.yaml
vim org/divisions/<div>/<team>/gate.yaml        # 전사 게이트를 좁히기만
vim org/divisions/<div>/<team>/AGENT_TEAM.md    # 에이전트가 있으면 필수

# 3. 에이전트 추가
mkdir org/agents/<id> && vim org/agents/<id>/agent.yaml
vim org/agents/<id>/SOUL.md                     # 필수 (L4)
make control-lint                               # 컴파일 되는지 (합격선 80)
make eg-bridge                                  # 통제평면 ↔ EG 정합
```

새 도구가 필요하면 `org/tools.yaml` 에 먼저 등록한다 —
`action: read|write|execute` 를 반드시 붙인다 (위험도와 다른 축이다).

---

## 9. 고객 온보딩 (테넌트 추가)

```bash
dawn-ops tenant          # 절차 8단계가 출력된다
```

요약: 번호 배정 → EG 네임스페이스(`org:t<N>-*`) → 고객 규정을 같은 스키마로 →
게이트 → 에이전트 → 관제 편입 → 계정 → 격리 검증.

**크로스테넌트 접근 코드는 절대 만들지 않는다** (`pol:no-cross-tenant`).

---

## 9-1. 품질 축 — 시킨 대로·원칙대로 했나

권한(무엇에 손댈 수 있나)과 별개 축이다. 게이트가 아무것도 막지 않아도 **조용히 잘못할
수 있다.** LLM-judge 가 세 축으로 채점한다:

| 축 | 묻는 것 |
|---|---|
| **근거** groundedness | 근거를 대고 말하나. 출처 없이 단정한 데가 있나 |
| **완결** completeness | 요구한 걸 다 채웠나. 빼먹고 완료라고 했나 |
| **경로** trajectory | 절차를 지켰나. 건너뛴 단계가 있나 |

70 미만이면 `quality` 축 케이스가 열린다. 픽셀 오피스에서는 **사람 발밑 품질 막대
3칸**으로 보인다 (회색이면 아직 판정 안 됨).

**판정 모델은 감시 대상과 실제로 달라야 한다.** 정책 id 만 다른 걸로는 부족하다 —
`model:gptoss` 와 `model:openlocal` 이 둘 다 같은 로컬 모델로 풀려서 한동안 **모델이
자기 산출물을 채점**하고 있었다. 지금은 풀린 모델까지 비교하고, 같으면 판정을 내지
않는다. 판정 전용 정책은 `model:judge` (`.env` 의 `LOCAL_JUDGE_MODEL`).

```bash
make gpu-check                       # 판정 모델이 GPU 에 있는지 (LOCAL_JUDGE_MODEL)
dawn-aoc cases                       # quality 축 케이스
```

판정 결과는 `var/aoc/judge/<trace_id>.json` 에 남고 같은 run 을 두 번 채점하지 않는다.

---

## 10. 절대 하지 않는 것

1. **`--no-verify` 로 훅을 우회하지 않는다.** 시크릿 훅이 막으면 이유가 있다.
2. **L3 를 클라우드로 우회하지 않는다.** GPU 가 죽으면 그 업무는 멈추는 게 맞다.
3. **에이전트 행동을 코드로 고치지 않는다.** EG 를 고친다.
4. **승인 없이 비가역 행동을 집행하지 않는다.** 급해도 그렇다.
5. **감사 로그·트레이스를 지우지 않는다.** 롤백도 지우는 게 아니라 격리다.
6. **`var/` 를 커밋하지 않는다.** 계정·세션키·업무 데이터가 들어 있다.

---

### 케이스·판정 파일을 손으로 지우지 마라

`var/aoc/cases/` 와 `var/aoc/judge/` 는 운영 상태다. 한 건만 지우려다 **필터가 넓어
90건을 날린 적이 있다** (2026-08-02). git 에 없으므로 되돌릴 수 없다.

지워야 한다면 **먼저 세어 보고** 지운다:

```bash
ls var/aoc/cases/*.json | wc -l                    # 전체
dawn-aoc cases | head                              # 무엇이 지워지는지 눈으로
```

원본 트레이스(`var/traces/`)는 건드리지 마라 — 케이스는 여기서 다시 파생되지만
트레이스는 다시 만들 수 없다. EU AI Act 12조가 요구하는 기록이다.

---

## 11. 파일이 어디 있나

```
var/traces/           OTel 스팬 원본 (P2)
var/aoc/              관제 — runs·cases·control·responses·quarantine·state
var/hitl/             승인 큐 (append-only)
var/groupware/        계정·세션키·감사 로그·EG 스냅샷·포털 DB
var/biz/business.db   업무 데이터 (문서·CRM·프로젝트·경비·자산)
var/website/          홈페이지 문의 접수함
var/ops/              E2E·레드팀·리허설 기록
eg/snapshots/         EG 스냅샷 (롤백·감사)
```

전부 gitignore 된다. **감사 증거가 필요하면 `var/` 를 통째로 보관한다.**
