# QUESTIONS.md — 사람에게 묻는 큐

> 규칙(05_conventions "모호하면 멈춤"): 스펙이 모호하거나 파괴적 결정·보안 판단이 필요하면
> 추측으로 진행하지 않고 여기에 남긴다. 답이 오면 `상태`를 `해결`로 바꾸고 반영 위치를 적는다.

| # | 상태 | 단계 | 질문 | 차단 여부 |
|---|---|---|---|---|
| Q1 | **해결** | P1 | EG 스키마 문서 전달 — 2종만 부재하여 재구성 | 해제 |
| Q2 | 대기 | P0 | GitHub PAT 노출 — 폐기·재발급 필요 (+ `workflow` 스코프) | 비차단 |
| Q3 | 해결 | P1 | 중간 산출물 = 최종본에 포함됨 (사용자 확인) | 해제 |
| Q4 | 해결 | P0 | el34 Assessor 는 `profiles: [assessor]` 로 기본 미기동 | 해제(§Q4) |
| Q5 | 참고 | P1 | 사내 GPU 서버는 VPN 연결이 사람 손을 탄다 | 비차단(§Q5) |
| Q11 | 해결 | P7 | AX본부 L2 공백 — `ax-university` 부터 채움 (에이전트 1명) | 해제(§Q11) |
| Q12 | 해결 | P7 | 사업 없는 내부 지원 작업 지시 허용 (1번) | 해제(§Q12) |

---

## Q1 — EG 스키마 문서 (해결 2026-08-01)

전달받음: `EG_SCHEMA_DESIGN.md` · `schema.json` · `seed/{01_foundation,02_policies,04_assets}.json` · `BOOTSTRAP.md`
→ `docs/context/02_eg_schema.md` · `eg/` 로 편입. P1 완료.

**여전히 없는 2종**은 근거를 갖고 재구성했다:

| 파일 | 처리 |
|---|---|
| `seed/03_personas.json` | 설계서 §4 + `03_org_personas.md` 매핑에서 역산. Persona 6 + 엣지 22 → 총계 74/136 일치 |
| `validate.py` | `BOOTSTRAP.md` 가 명시한 4가지 검사(스키마·참조·커버리지·핵심순회)로 작성 |

> **원본이 나오면**: `03_personas.json` 은 파일 하나 교체 후 `make eg-load && make eg-bridge`.
> `validate.py` 는 우리 것이 브리지·fail-safe 검사까지 하므로 교체보다 대조를 권한다.

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

## Q3 — 중간 산출물 Drive 폴더 (해결 2026-08-01)

사용자 확인: "중간 산출물은 최종에 다 있는 내용" → 별도 확보 불필요.
대학 AX 예시 폴더는 접근 가능했고 `docs/references/ax-university/INDEX.md` 에 색인했다.

---

## Q4 — el34 Assessor 기본 미기동 (해결)

`~/el34/docker-compose.yaml:334` 의 assessor 서비스는 `profiles: [assessor]` 라서 기본 `up` 에 포함되지 않는다.
또 호스트 포트 바인딩이 `192.168.0.151:9201` 인데 이 호스트 IP 는 `192.168.0.108` 이다.

**조치**: 헬스체크 스크립트(`infra/el34/healthcheck.py`)는 dmz 브리지의 컨테이너 IP
`10.20.32.55:8000` 을 우선 사용하고, 미기동 시 기동 명령을 안내하도록 만들었다.
`make health` 로 확인한다.


---

## Q5 — 사내 GPU 서버는 VPN 연결이 사람 손을 탄다 (참고)

`pol:l3-local-only` 가 요구하는 "로컬 모델"은 이 호스트가 아니라 **VPN 너머 사내 GPU 서버**다.
이 호스트에는 GPU 가 없다.

**에이전트는 VPN 을 붙이지 않는다.** 네트워크 경계 변경은 비가역 행동이고
`sec.firewall_change` 급으로 다룬다. 사람이 붙여야 한다:

```bash
make vpn           # infra/gpu/vpn-connect.sh — 스플릿 라우팅(el34 보존)
make gpu-check     # 도달 확인
```

**운영상 함의**: VPN 이 끊기면 인사·재무·경리 에이전트(L3 취급)는 **작업을 중단한다.**
클라우드 폴백은 없다 — 그게 정책이다. P2 하네스가 이 상태를 인시던트로 올려야 한다.

el34 랩이 같은 호스트에서 도므로 **풀터널로 붙이면 랩이 끊긴다.** 반드시 스플릿 라우팅을 쓴다.


---

## Q6 — 대고객·공개 창구가 어느 존인가 (해결 — 1번 선택)

픽셀 오피스에 존별 방을 다시 세우면서 드러난 모델링 문제다. EG 상 두 존의 성격이
사람의 직관과 어긋난다:

| 존 | EG 라벨 | 대역 | 자산 |
|---|---|---|---|
| `zone:ext` | 외부/공개 · sec:L0 · "Zone 0 · 로비" | 10.20.30.0/24 | 웹 검색, 외부 공개 API, bastion |
| `zone:dmz` | 제한 · sec:L2 · "Zone 2 · 제한" | 10.20.32.0/24 | 포털·웹앱, CRM, SIEM, Assessor, EG DB, 소스코드, 계약, 프로젝트, 지식베이스 |

**문제**: P4 에서 만든 **공개 홈페이지(:8810)** 는 외부인이 보는 대고객 창구인데
`asset:portal` 하나로 **사내 그룹웨어(:8811)** 와 함께 묶여 `zone:dmz`(제한) 에 있다.
그래서 (a) 공개 창구가 "제한" 등급 방에 있고, (b) 로비(`zone:ext`) 는 어느 본부도
쓰지 않아 전 층에서 **미사용**으로 표시된다.

el34 랩 기준으로는 `ext` 가 인터넷·공격자 세그먼트이므로 지금 배치가 틀린 건
아니다. 다만 **"우리 회사의 대고객 접점이 어디냐"** 는 질문에 화면이 답을 못 한다.

**선택지** (전부 EG 변경이라 코드가 아니라 `eg/seed/04_assets.json` 을 고친다):

1. `asset:portal` 을 공개(`asset:site`)와 사내(`asset:groupware`)로 쪼개고
   공개 쪽만 `zone:ext` 로 옮긴다. → 로비가 실제 대고객 창구가 된다.
2. 지금 배치를 유지하고 `zone:dmz` 의 `pixel_room` 을 "Zone 2 · 대고객/제한" 으로
   고쳐 이름만 맞춘다.
3. 그대로 둔다 — 로비는 아웃바운드 전용(웹 검색·외부 API)이라고 정의한다.

**조치 (1번)**: `asset:portal` 을 둘로 쪼갰다.

| 자산 | 존 | 등급 | 소유 |
|---|---|---|---|
| `asset:site` 공개 홈페이지(대고객 창구) | `zone:ext` (Zone 0 · 로비) | sec:L0 | org:mgmt |
| `asset:groupware` 사내 그룹웨어(승인·EG 조정) | `zone:dmz` (Zone 2 · 제한) | sec:L2 | org:it-dc |

EG 노드 77→78, 엣지 148→150. `make eg-load && make eg-validate` 오류 0.
코드 참조는 없었다 (어떤 스킬도 `asset:portal` 을 touches 로 걸고 있지 않았다).

**남는 사실 하나**: 로비는 여전히 전 층 **미사용**이다. 공개 홈페이지가 거기 있는데도
그렇다 — **그 자산을 만지는 도구가 하나도 없기 때문이다.** 고객관리팀은 문의를
`asset:crm`(dmz)로 처리하고, 홈페이지 자체를 건드리는 에이전트는 아직 없다.
자산 배치가 아니라 **업무가 없다**는 뜻이고, 이제 화면이 그걸 정확히 말한다.


---

## Q7 — 자산 미선언 도구가 16/35 이고, 페일세이프에 구멍이 있다 (1~3 조치 완료 · 4 대기)

> **정정.** 처음 이 항목을 적을 때 `sec.trace_query` 가 호출마다 게이트 판정이
> 갈린다고 썼는데, 확인해 보니 **옛 트레이스였다**. 자산 없는 2건은 08-01 14:55·15:08,
> 즉 P3 빌드 중 그 스킬에 `touches=["asset:assessor"]` 를 붙이기 **전**이다.
> 15:53 이후 3건은 전부 `asset:assessor` 를 달고 `require_hitl` 이다. 살아 있는
> 불일치가 아니다.

**실제로 누락된 것은 정확히 두 개다: `eg.search`, `eg.record`.** 두 가지가 겹쳤다:

1. **레지스트리에 `touches` 가 없다** — `agents/dawn_agents/skills.py:223-224`
   다른 스킬은 전부 `touches=[...]` 를 달고 있다 (`fin.*` → `asset:ledger`,
   `sec.siem_query` → `asset:siem` …). eg 두 개만 비어 있다.
2. **액션 게이트를 아예 지나가지 않는다** — `worker.eg_search()` / `eg_record()` 는
   `use_skill()` 을 거치지 않고 자기 스팬을 직접 연다. 거기서 `dawn.gate.decision`
   을 `"log_only"` 로 **하드코딩**하고 `dawn.assets` · `dawn.severity` ·
   `dawn.policies` 는 아예 싣지 않는다.

2번에는 이유가 있다. eg_search/eg_record 는 워커 루프의 **필수 단계 ①/④** 다.
게이트가 이걸 막을 수 있으면 에이전트가 자기 작업을 기록하지 못하고, "④ eg_record
를 마쳐야 완료"라는 루프 불변식이 깨진다.

**결과**: EG DB(`asset:eg-db`)는 `zone:dmz` 에 있는 자산인데, 그걸 읽고 쓰는
호출이 존을 넘지 않은 것처럼 기록된다. 픽셀 오피스에서는 자기 자리(홈 존)에 앉은
것으로 그려진다. 경영관리부 층 기준 그런 스팬이 42건이다.

**선택지:**

1. **텔레메트리만 채운다 (권장)** — `touches=["asset:eg-db"]` 를 달고, eg 스팬이
   레지스트리의 touches 를 그대로 싣게 한다. 게이트는 여전히 안 지난다.
   → 게이트 판정·KPI·기존 케이스 심각도는 **하나도 안 바뀐다.** 바뀌는 건
   "EG 조회는 dmz 자산을 만진다"는 사실이 화면과 로그에 나타나는 것뿐이다.
   부작용: 픽셀 오피스에서 모든 에이전트가 eg 조회 때마다 dmz 방으로 들어간다.
   실제로 그렇긴 하지만 화면이 시끄러워지고 의미 있는 존 이동이 묻힐 수 있다.
2. **게이트도 지나게 한다** — 일관성은 최고지만 필수 단계가 막힐 수 있다.
   루프 불변식을 깨므로 권장하지 않는다.
3. **EG DB 를 존에 두지 않는다** — 모든 에이전트가 상시 읽는 공용 인프라(DNS 같은)로
   보고 `LOCATED_IN` 을 뗀다. 그러면 "선언 누락"이 아니라 "존에 없는 자산"이 된다.

### 개별 건이 아니라 구조 문제였다 (추가 조사)

등록된 스킬을 전부 세어 봤다: **35개 중 16개가 자산 미선언.** eg 둘만이 아니었다.

```
ctl.cross_tenant  ctl.modify_gate  ctl.modify_kill_switch      ← 통제 평면 자체
sys.deploy  sys.mkfs  sys.rm_rf_root  sys.run_command          ← 파괴적
dev.dependency_add  dev.git  dev.tests                         ← asset:source 여야
net.fetch  net.web_search                                      ← asset:web-search/public-api 여야
eg.record  eg.search                                           ← asset:eg-db 여야
skill.preview  skill.run                                       ← 메타 도구. 자산 없는 게 맞다
```

### 페일세이프 구멍

심각도 = 행동 비가역성 × 자산 등급이다. **자산을 하나도 선언 안 하면 계산할 게
없어서 0 이 된다.** 실측:

```
도구                  위험   선언 자산            심각도       판정
sys.rm_rf_root       HIGH  (없음)              0  낮음     block
ctl.modify_gate      HIGH  (없음)              0  낮음     block
sec.container_stop   HIGH  ['asset:fw-ips']    6  최고     block
```

"미분류 자산은 최고 등급으로 친다"는 페일세이프는 있는데, **아예 선언 안 한 경우**는
거기 안 걸린다. 회사에서 가장 위험한 도구들이 심각도 0 이다.

**실제 파급**: `triage.py` 의 `if case.severity_score >= 6` 이 **`revoke_credentials`**
플레이북을 여는 조건이다. 자산 미선언 도구는 이 문턱을 **절대 못 넘는다.**
에이전트가 자기 게이트를 고치려 시도해도(`ctl.modify_gate`) 자격증명 회수 권고가
뜨지 않는다. deny 리스트가 실행은 막지만 **심각도가 거짓말을 한다.**

### 제안 (4단계, 1~3 은 판정 불변 / 4 는 재계산)

1. `org/tools.yaml` 에 도구별 `touches:` 선언 — **카탈로그가 권위**가 된다.
2. 스킬 등록 시 카탈로그의 touches 를 자동 적용 — 등록 코드가 빠뜨려도 채워진다.
3. `make registry` 가 touches 없는 도구를 실패로 잡는다 (`skill.*` 처럼 자산이 없는
   게 맞는 메타 도구는 카탈로그에 명시적으로 면제 표시).
4. **페일세이프 구멍 메우기** — 자산이 하나도 없으면 0 이 아니라 위험도 기반
   최소값을 준다 (HIGH·destructive → 최고 등급 취급). 선언을 다 채워도 미래에 또
   빠질 수 있으니 **계산 쪽에서** 막아야 한다.

1~3 은 게이트 판정을 안 바꾼다(자산 선언이 늘어도 deny 는 그대로). **4 는 기존
케이스 심각도와 KPI 를 재계산시킨다** — 그래서 여기서 멈추고 묻는다.

### 조치 (1~3 완료)

* `org/tools.yaml` 50개 도구 전부 `touches:` 선언. 메타 도구(`skill.*`)는 `touches: []` 로 명시.
* `ToolCatalog.load` 가 `touches` 미선언·`desc` 없음을 **거부**한다 → `make check` 에서 잡힌다.
* 스킬 등록 시 카탈로그의 touches 를 자동 적용 — 등록부가 빠뜨려도 채워진다.
* 심각도 정상화: `sys.rm_rf_root`·`ctl.modify_gate` **0 → 6(최고)**. 자격증명 회수
  문턱(`severity_score >= 6`)을 이제 넘는다.
* 덤으로 발견: 16개 도구의 `desc` 가 `desc:""…""` 로 깨져 파싱 시 사라지고 있었다. 고쳤다.

### 이 과정에서 깨진 불변식 하나와 그 처리

`eg.record` 에 자산을 달자 `pol:autonomy-gate` 가 발동해 `require_hitl` 이 됐다
(A1 조직 < dmz 자산 등급, action=write). 그러면 **에이전트가 자기 작업을 기록하지
못해** "④ eg_record 를 마쳐야 완료"라는 루프 불변식이 깨진다
(`test_loop_instrumentation_is_not_gated` 가 잡았다).

암묵적으로 워커 코드가 게이트를 건너뛰던 것을 **카탈로그 선언으로 끌어올렸다**:

```yaml
eg.search: { ..., touches: [asset:eg-db], loop_instrumentation: true }
```

`loop_instrumentation` 이 붙은 도구는 판정이 `log_only` 로 강제된다. 다만 **자산·
심각도·정책 판정은 그대로 기록**하고, 강제했다는 사실을 이유에 남긴다. 비가역·고위험
도구에는 붙일 수 없다 (`ToolCatalog.load` 가 거부). 우회가 코드 깊숙이 숨어 있지 않고
통제 평면 문서에 적혀 있게 됐다.


---

## Q8 — judge 가 자기 자신을 채점하고 있었다 (해결)

품질 축을 붙이다 발견. 담합 방지 검사가 **정책 id** 만 비교하고 있었다:

```
model:gptoss     → ollama/gpt-oss:120b
model:openlocal  → ollama/gpt-oss:120b   ← 정책은 다른데 같은 모델
```

둘 다 `.env` 의 `$LOCAL_LLM_MODEL` 로 풀린다. 그래서 gpt-oss 가 자기 산출물을
채점했다. 문서에는 "산출물을 만든 모델과 다른 모델이 채점한다"고 적혀 있었는데
사실이 아니었다.

**조치**

* `pick_judge_model` / `judge` 가 **풀린 모델까지 비교**한다. 같으면 판정을 내지
  않는다 — 틀린 점수보다 "판정 못 했다"가 낫다 (`verdict=unknown` 은 탐지를
  만들지 않는다).
* EG 에 판정 전용 `model:judge` 추가 (`.env` 의 `LOCAL_JUDGE_MODEL`).
  업무용으로는 배정하지 않는다. EG 노드 78→79.
* 추론(thinking) 모델이 생각에 토큰을 다 쓰고 본문을 못 내던 것도 고쳤다
  (`ollama` 호출에 `think: false`). 첫 독립 판정이 빈 응답이라 "판정 불가"로
  떨어졌던 원인이다.

**결과** — 독립 판정기를 붙이자마자 첫 실제 품질 결함이 잡혔다:

```
judge[model:judge → qwen3.6:35b]  fail  근거=30 완결=90 궤적=80
· doc.search 결과 없음(근거 문서 부재)이라는 제한을 무시하고 API·관리 콘솔·
  파일럿 구축 프로세스를 단정적으로 서술 — SOP 의 "근거 문서가 있을 때만 쓴다" 위반
```

게이트는 아무것도 막지 않았다(파괴적 행동이 없었으므로). 품질 축이 잡아야 할 바로
그것 — **조용히 잘못한 것**이다.

**남은 판단** — `LOCAL_JUDGE_MODEL` 을 `qwen3.6:35b` 로 잡았다. 사내 GPU 에 더 큰
모델(`llama3.3:70b`, `deepseek-r1:70b`)도 있으니 판정 품질을 올리려면 바꿔도 된다.
`.env` 한 줄이다.


---

## Q9 — P7 작업 지시 파이프라인 (① 결정됨 · ② 결정됨 · ③ 대기)

`docs/instructions/P7_work_orders.md` 에 설계를 적었다. DoD-1(도메인·접수)과
DoD-2(결재 라인)는 결정 없이 시작할 수 있지만, 아래 셋은 추측하면 안 된다.

### 1. 인프라 프로비저닝 — **결정: 접수 시 선택 · 풀에서 할당**

이분법(컨테이너만 / 서버까지)이 아니었다. 두 가지가 정해졌다.

**① 등급은 요청자가 접수 시 고른다.** 선택지는 그 사업의 `infra.allowed` 로 제한된다:

```yaml
aoc-platform:      default: server     allowed: [container, vm, server]
ax-consulting:     default: container  allowed: [none, container, vm, server]
foundation-model:  default: server     allowed: [vm, server]      # L3 → 로컬 GPU 전용
```

**② 프로비저닝은 생성이 아니라 할당이다.**

| 등급 | 어디 | 방식 |
|---|---|---|
| `none` | 기존 환경 | — |
| `container` | **이 호스트의 el34 인프라** | 도커 + 존 네트워크 |
| `vm` / `server` | **외부 시스템** (하드웨어·OS 준비 완료, L2 연결) | `infra/pool.yaml` 에서 할당 |

이게 중요한 이유: 에이전트가 **클라우드 크레덴셜을 쥘 필요가 없다**(과금 행동 없음),
실패가 "생성 실패"가 아니라 **"가용 자원 없음"**(다루기 쉽다), 회수가 **반납**이지
삭제가 아니다.

`vm` 이상은 외부 시스템 자원을 점유하므로 **대표이사 결재**가 붙는다.

**용량은 더 이상 제약이 아니다** — 개발 시스템을 2배 스펙으로 이관 예정이고,
`vm`·`server` 는 외부 장비다. 다만 **풀이 비어 있으면 `준비대기`** 이므로
`infra/pool.yaml` 에 장비를 등록하는 것이 선행 조건이다.

### 2. 결재 계정 — 본부장 4명·대표이사 1명

"모든 본부의 본부장 및 대표이사는 human이 할거야" 로 이해했다. 그런데 지금
그룹웨어 계정은 `admin` 하나뿐이고, 레지스트리는 **에이전트만 안다** — 사람 역할이라는
개념 자체가 없다.

필요한 것:

* `org/company.yaml` (신규) — 대표이사
* `org/divisions/<본부>/division.yaml` 에 `lead` 추가 — 본부장 4명
* 그룹웨어 계정 5개 (`hitl.approve` / 대표는 `hitl.approve.critical`)

**계정 이름과 실제 담당자를 어떻게 할지** 알려달라. 지금은 전부 한 사람(당신)이
겸임하는 게 현실적일 텐데, 그러면 **결재 라인이 형식만 남는다.** 그래도 파이프라인은
그 구조로 두는 게 맞다 — 나중에 사람이 늘면 계정만 바꾸면 되니까.

임시안: `ceo`, `lead-aoc`, `lead-ax`, `lead-corp`, `lead-itops` 5계정을 만들고
전부 같은 사람이 쓰다가, 실제 담당자가 생기면 넘긴다.

### 3. 정산 기준

DoD-7 의 "보상 업무" 를 어디까지 볼지.

* **(a) 내부 원가만** — 토큰·GPU 시간·컨테이너 시간을 집계해 `expense` 에 기록
* **(b) 고객 청구까지** — 계약 단가 × 사용량 → 청구서. `contract` 테이블 연동 필요
* **(c) 지금은 집계만, 청구는 나중** ← **권장**

(c) 를 권하는 이유: 단가 정책이 없다. 실제 고객 계약이 생긴 뒤에 정하는 게 맞다.
집계는 지금부터 쌓아 둬야 그때 근거가 된다.


---

## Q10 — 컨테이너 존과 외부 호스트 존의 L3 경로가 다르다

`infra/pool.yaml` 을 만들면서 드러났다. 같은 "존"이라는 이름을 쓰지만 실제 네트워크가 다르다:

```
컨테이너   도커 브리지   10.20.30/31/32/40.0/24   (이 호스트 안)
외부 호스트 물리 LAN      192.168.0.0/24           (L2 연결)
```

작업 지시가 `zone: dmz` 로 컨테이너를 띄우면 `el34-dmz`(10.20.32.0/24) 에 붙는다.
같은 `zone: dmz` 인 외부 서버는 192.168.0.x 다. **둘은 서로 직접 못 본다** —
이 호스트를 경유하는 라우팅이 없으면.

문제가 되는 경우:
* 외부 서버의 에이전트가 dmz 컨테이너의 자산(SIEM·CRM 등)을 조회해야 할 때
* 컨테이너 작업이 외부 서버의 산출물을 받아야 할 때 (hand-off)

**선택지:**

1. **호스트 라우팅** — 이 호스트가 게이트웨이가 되어 10.20.x ↔ 192.168.0.x 를 잇는다.
   pipe 존(PEP)의 의미가 유지된다. 지금 구조와 가장 잘 맞는다.
2. **외부 호스트도 오버레이에 넣는다** — docker swarm / VXLAN 으로 같은 L2 를 만든다.
   깔끔하지만 el34 랩 구성을 건드려야 한다.
3. **존을 분리해서 본다** — 컨테이너 존과 물리 존을 다른 이름으로 두고 서로 안 닿는다고
   전제한다. 가장 안전하지만 hand-off 가 파일 교환으로 제한된다.

지금은 정하지 않아도 DoD-1·2 는 진행된다. **DoD-3(할당)에서 필요**하다.


---

## Q11 — 일을 받을 수 있는 팀이 4/17 뿐이다 (L2 공백) — 해결 2026-08-02

P7 DoD-4 를 돌리다 드러났다. 편성은 **팀에 `AGENT_TEAM.md`(L2)가 있어야** 가능한데
(없으면 규칙 없이 일하는 팀이 된다), 실제로 있는 팀은 4개뿐이다:

```
✔ aoc-dev · corp-admin · corp-cs · itops-ccc
—  나머지 13팀 (aoc-ops, aoc-lab, ax-university, ax-security, ax-marketing,
   corp-hr, corp-finance, corp-marketing, corp-sales, corp-secretary,
   itops-datacenter, itops-support, itops-bizsupport)
```

**특히 AX본부는 3팀 전부 L2 가 없다.** 그런데 `ax-consulting` 사업은 `status: active`
다 — **활성 사업인데 일을 받을 수 있는 팀이 하나도 없다.** 홈페이지에서 AX 작업
요청을 접수하면 결재까지는 가지만 편성에서 멈춘다.

L2 는 그 팀 에이전트 전체의 행동 규칙이라 **자동 생성하지 않기로 했다.** 사람이 써야
한다. 우선순위를 정해 달라 — 첫 고객이 대학 AX 라면 `ax-university` 가 먼저다.

### 결정 — `ax-university` 부터 채운다 (에이전트 1명)

활성 사업(`ax-consulting`)이 실제로 일을 받을 수 있게 하는 것이 먼저다. 만든 것:

| 계층 | 파일 |
|---|---|
| L2 | `org/divisions/ax/university/AGENT_TEAM.md` + `gate.yaml` |
| L3 | `work/consulting/AX_DIAGNOSIS_WORK.md` (`consulting/ax-diagnosis`) |
| L4 | `org/agents/ax-univ-diag-01/{agent.yaml,SOUL.md}` |

L3 를 새로 쓴 이유: `work/consulting/` 이 **비어 있었다.** 참조 색인이 제안한
3분해(진단·커리큘럼·시뮬레이터) 중 **진단**만 먼저 썼다 — 나머지 둘의 입력이
진단서이므로 순서가 있고, 안 쓸 SOP 를 미리 쓰면 실제와 어긋난다.

게이트에서 배운 것 두 가지 (실측):

* `model.policy: cloud_ok` 로 적었더니 **단조 축소에 걸렸다** (상위가 `from_eg`).
  넓히는 방향이라 거부된 것이 맞다 — 정책은 상위에 두고 `force_local_when` 만 더했다.
* 팀 게이트에 `dev.*`·`proj.*` 를 열어 뒀더니 control-lint 가 **"좁힐 여지"** 로 경고했다.
  시뮬레이터 에이전트가 아직 없어서다. 닫았다 — 생길 때 넓히는 것이 의도적 행위여야 한다.

**남은 12팀은 여전히 L2 가 없다.** 편성은 계속 거부된다(설계대로). 다음 고객·다음
상시 업무가 정해질 때 그 팀부터 채운다.

## Q12 — 경영관리부는 어느 사업의 소관도 아니다 (해결 2026-08-02 — 1번)

같은 작업 중 드러났다. 작업 지시는 사업을 골라야 하고 담당 본부는 그 사업의
`owning_divisions` 에서 나오는데:

```
aoc-platform      → aoc, itops
ax-consulting     → ax
foundation-model  → aoc
경영관리부(corp)   → 어느 사업에도 없다
```

그래서 **경리·문의·인사 같은 내부 지원 업무는 작업 지시를 만들 수 없다.**
경영관리부는 실제로 일하는데(경비 처리·고객 문의 응대) 파이프라인에 진입할 통로가 없다.

**선택지:**

1. **사업 없는 작업 지시를 허용한다** ← 권장. 내부 지원 업무는 수익 사업이 아니다.
   `business` 를 비우고 `division` 을 직접 고르게 한다. 인프라 등급은 기본 `none`,
   결재는 담당 본부장.
2. `corp` 를 `aoc-platform` 의 `owning_divisions` 에 넣는다 — 사실과 다르다.
   경영관리는 그 사업을 소유하지 않는다.
3. 내부 지원 사업(`internal-ops`)을 하나 만든다 — 사업 목록이 실제 사업과 관리
   기능을 섞게 된다.

1번을 권하는 이유: **사업은 돈을 버는 단위이고 본부는 일하는 단위**라 원래 1:1 이
아니다. 지금 모델이 그 둘을 묶어 놨다.

### 결정 — 1번 (사업 없는 작업 지시 허용)

`dawn_core.workintake.validate()` 가 `business=""` 를 받는다. 대신 두 가지를 요구한다:

* **본부는 반드시 고른다.** 사업도 본부도 없으면 결재 라인을 못 만들고, 받아 두면
  갈 곳 없는 지시가 쌓인다.
* **`vm`·`server` 는 못 쓴다.** 외부 시스템 자원 점유는 비용 귀속처가 있어야 한다.
  필요하면 사업을 붙여서 올린다.

**그룹웨어(내부)에만 열었다. 공개 홈페이지는 사업 필수 그대로다** — 외부 고객의
요청은 정의상 수익 사업에 속한다. 내부 지원 업무를 외부에서 접수할 이유가 없다.
