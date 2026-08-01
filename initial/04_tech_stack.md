# 04. 기술 스택·인프라 (참조)

> CC는 이 스택 안에서 구축한다. 새 의존성 추가 시 05_conventions.md의 승인 규칙을 따른다.

## 기반 인프라 — el34 (이미 구축됨)
4-tier 세그먼트. 섹터 배정과 픽셀 오피스의 실물 앵커.

| 존 | CIDR | 민감도 | 주요 자산 |
|---|---|---|---|
| ext | 10.20.30.0/24 | 외부/공개 | bastion(점프), attacker |
| pipe | 10.20.31.0/24 | 경유/검사 | fw, ips (= 게이트/PEP) |
| dmz | 10.20.32.0/24 | 제한 | web, **Wazuh SIEM**, portal, **Assessor** |
| int | 10.20.40.0/24 | 통제 | 7 취약 웹(고객 모사), DB |
| user | 10.20.33.0/24 | 내부 | Windows 엔드포인트 |

보안 스택: Wazuh SIEM · Suricata IPS · ModSecurity WAF. Assessor(읽기전용 평가 수집, `/assess`·`/activity`·X-API-Key) = AOC 수집 계층의 원형.

## 기존 저장소 (재사용·확장)
| 저장소 | 역할 | 어디서 쓰나 |
|---|---|---|
| **el34** | 보안 인프라·관제 대상 | 전체 기반 |
| **bastion** | 보안운영 에이전트·EG 엔진·33 Skill·SubAgent A2A | P1 EG, P2 하네스 |
| **experience_graph_mcp** | EG를 CC에 노출 (eg_search/eg_record/skill_preview/skill_run, EG_MODE, UserPromptSubmit 훅) | P1, P2 |
| **tw2** | 보안 산업 AX | AX본부 보안사업부 |

## 모델
- **클라우드**: Claude Code — Opus(고복잡·비가역), Sonnet(관제·컨설팅), Haiku(정형·반복)
- **로컬**: ollama gpt-oss:120b (bastion 기존 운영), DGX Spark + open model (민감 데이터)
- 모델 배치 규칙은 EG의 ModelPolicy 노드에 있음 → 부서 에이전트가 eg_search로 자기 모델을 결정(동적 라우팅 가능)

## 관측성
- **OTel GenAI semantic conventions**: invoke_agent→chat/execute_tool 스팬, gen_ai.* 속성. **버전 pin 필수**(experimental). 콘텐츠 캡처는 마스킹 전제 opt-in.
- 트레이스 레이크 저장. Assessor 확장으로 수집.

## 권장 기술 선택 (CC 재량, 05 컨벤션 준수 하에)
- 백엔드: Python(에이전트·관제) / Node 또는 Python(웹). 모노레포.
- 프런트: 홈페이지·그룹웨어·픽셀오피스는 웹 표준. 픽셀오피스는 캔버스/경량 게임 렌더링.
- 큐/스케줄: Temporal 또는 Celery. 오케스트레이션: LangGraph.
- 저장: EG는 bastion_graph.db 확장. 업무데이터는 별도 DB(테넌트 격리 고려).
- 배포: el34 존에 맞춰 배치(공개=dmz 앞단, 내부=user/int). 시크릿은 볼트/환경변수.
