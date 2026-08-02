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

## Q6 — 대고객·공개 창구가 어느 존인가 (결정 필요)

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

**정하지 않은 채로는 옮기지 않았다.** 존 재배치는 게이트 판정·심각도 계산·
`pol:zone-gate` 에 전부 영향을 준다 (COMPANY.md 원칙 #7: 에이전트 행동은 코드가
아니라 EG 로 바꾼다).
