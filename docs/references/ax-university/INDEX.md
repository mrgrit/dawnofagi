# AX 대학 사업 — 참조 산출물 색인

> AX본부 대학사업부(`org/divisions/ax/university/`)의 레퍼런스.
> 원본은 Google Drive 공유 폴더에 있고, 여기에는 **색인만** 둔다 (저작물 자체는 커밋하지 않는다).

출처: https://drive.google.com/drive/folders/1eZSc1Nqv_Ek--oDpINa_4HfLYgTRqYdA (2026-07-26 공유)

| 파일 | 형식 | 크기 | 무엇 |
|---|---|---|---|
| 각 전공의 AI 접목 방향 | Google Docs | 10KB | 전공별 X+AI 접목 방향 — 커리큘럼 설계의 출발점 |
| 전공별-X플러스AI-발표자료.pptx | PPTX | 70KB | 고객(대학) 대면 발표 자료 |
| AI물류시뮬레이터.html | HTML 데모 | 28KB | 물류 전공 시뮬레이터 개념 데모 |
| 조리-시뮬레이션-개념데모.html | HTML 데모 | 21KB | 조리 전공 시뮬레이터 개념 데모 |
| 야근냥스튜디오-데모.html | HTML 데모 | 35KB | 교육용 인터랙티브 데모 |

## 이것이 시사하는 사업 패턴

대학 AX 의 제공 형태가 세 가지로 나뉜다 — `ax-consulting` 사업 매니페스트의 로드맵과 대응한다.

1. **진단·설계** — 전공별 AI 접목 방향 분석 (컨설팅)
2. **자료·커리큘럼** — 발표자료·강의안 (콘텐츠)
3. **실습 도구** — 전공 특화 시뮬레이터 (플랫폼 구축)

→ 대학사업부 에이전트를 만들 때(P2 이후) `work/consulting/` 에
`AX_DIAGNOSIS_WORK.md` · `CURRICULUM_DESIGN_WORK.md` · `SIMULATOR_BUILD_WORK.md` 로
분해하는 것이 자연스럽다. 세 가지가 위험도·HITL 경계가 서로 다르기 때문이다
(고객 대면 산출물은 근거 첨부 + 할루시네이션 평가 필수 — 05_conventions 품질 가드레일).

## 주의

고객 대면 산출물이므로 `persona: consulting` 의 원칙이 적용된다: **근거·출처 필수, 할루시네이션 평가 통과 후 전달.**
