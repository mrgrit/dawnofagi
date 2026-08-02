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

## Q7 — 자산 미선언 도구가 16/35 이고, 페일세이프에 구멍이 있다 (결정 필요)

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
