# 사내 GPU 서버 — "로컬 모델"의 실체

> **이 호스트에는 GPU 가 없다.** EG 가 말하는 "로컬 모델"(`cost_tier: local`)은
> 이 기계에서 도는 모델이 아니라 **사내 GPU 서버**에서 도는 모델이다.
> VPN 을 통과해야 도달한다.

## 왜 이게 중요한가

`pol:l3-local-only` — 극비(L3) 데이터는 클라우드 모델에 전송 금지, 로컬 모델 전용.
인사·재무·개인정보를 다루는 에이전트(인사팀·재무팀·경리총무팀)는 **이 서버가 없으면 아예 일하지 못한다.**
EG 의 모델 라우팅이 그렇게 설계돼 있다:

```
$ make eg-routing
  corp-admin-clerk-01   org:ga   local_only   사내 GPU open model   사내 GPU open model
  ccc-soc-triage-01     org:ccc  from_eg      CC Sonnet             차단   ← 로컬 모델 없음
```

GPU 서버에 못 닿으면 → 라우팅이 **차단**된다. 클라우드로 우회하지 않는다. 그게 의도다.

## 접속

| 항목 | 값 |
|---|---|
| VPN | GlobalProtect · 포털 `106.240.19.114` |
| GPU 서버 | `211.170.162.109:11434` (ollama) |
| 자격증명 | `.env` 의 `VPN_USER` / `VPN_PASSWORD` — **커밋 금지** |

주소·계정은 전부 `.env` 에만 둔다. **EG 시드나 코드에 넣지 않는다** (05_conventions #1).
EG 는 "어느 등급의 모델을 쓰는가"(정책)만 담고, "어디에 어떻게 접속하는가"(환경)는 담지 않는다.

## 확인

```bash
make gpu-check          # VPN 연결 여부 + ollama 도달 + 모델 목록
```

VPN 이 안 붙어 있으면 다음처럼 나온다:

```
✘ GPU 서버 미도달 — 211.170.162.109:11434
  ⓘ VPN(GlobalProtect)이 연결돼 있는지 확인하라. 포털: 106.240.19.114
```

## VPN 연결

GlobalProtect 는 대화형 인증이 필요해 스크립트가 대신 붙여줄 수 없다.
사람이 클라이언트로 붙은 뒤 `make gpu-check` 로 확인한다.

리눅스 CLI 를 쓴다면 `openconnect --protocol=gp` 로도 붙는다:

```bash
sudo openconnect --protocol=gp --user="$VPN_USER" "$VPN_PORTAL"
```

> 이 명령은 사람이 직접 실행한다. 에이전트는 VPN 을 붙이지 않는다 —
> 네트워크 경계 변경은 비가역 행동이고 `sec.firewall_change` 급으로 다룬다.

## P2 에서 어떻게 쓰이나

`packages/dawn_core/dawn_core/eg/traverse.py:model_for_org()` 가 EG 를 조회해
`model:openlocal` / `model:gptoss` 를 고르면, P2 하네스가 `LOCAL_LLM_BASE_URL` 로 호출한다.
서버가 죽어 있으면 **작업을 중단한다** — 클라우드 폴백은 없다.
(경리 `*_WORK.md` §8: "로컬 모델 사용 불가 → 작업 중단. 클라우드로 대체하지 않는다")
