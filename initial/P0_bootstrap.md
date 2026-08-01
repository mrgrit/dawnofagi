# P0 — 부트스트랩

## 목표
회사 코드베이스의 뼈대를 세우고, el34 인프라에 연결하고, CC가 이후 작업할 환경(모노레포·CI·시크릿 관리·CLAUDE.md)을 만든다.

## 먼저 읽어라
- `docs/context/00_charter.md` (회사가 무엇인지)
- `docs/context/04_tech_stack.md` (el34·저장소·스택)
- `docs/context/05_conventions.md` (엄수 규칙)

## 작업
1. **모노레포 생성**: 아래 최상위 구조로 저장소를 만든다. (이름 예: `dawn/`)
   ```
   dawn/
     apps/          # 홈페이지, 그룹웨어, 픽셀오피스 등 (P3~P5)
     agents/        # 에이전트 하네스·워커 (P2)
     aoc/           # 관제 시스템 (P3)
     eg/            # Experience Graph (P1)
     packages/      # 공용 라이브러리 (스키마·유틸)
     infra/         # 배포·el34 연동 스크립트
     docs/          # 이 참조 문서들을 복사
   ```
2. **CLAUDE.md 작성**: 저장소 루트에 CC용 상시 지침 파일. 내용: 회사 미션 1줄, 05_conventions.md의 핵심 가드레일 요약, "작업 전 START_HERE.md와 해당 Pn 지시문을 읽어라", 커밋 규칙.
3. **CI 설정**: lint + test 파이프라인. 시크릿 스캔(예: gitleaks) pre-commit 훅. `05_conventions.md`의 "시크릿 하드코딩 금지" 강제.
4. **시크릿 관리**: `.env.example` 템플릿과 볼트/환경변수 로딩 규약. 실제 `.env`는 .gitignore.
5. **el34 연결 확인**: el34의 Assessor 엔드포인트(`/assess`·`/activity`, X-API-Key)에 읽기 접근이 되는지 확인하는 헬스체크 스크립트를 `infra/`에 만든다. (P3 수집 계층의 전제)
6. **참조 문서 복사**: `docs/context/`를 모노레포 `docs/`로 복사해 CC가 항상 참조할 수 있게 한다.
7. **BUILD_LOG.md · QUESTIONS.md 생성**: 진행 기록과 질문 큐.

## 완료 조건 (DoD)
- [ ] 모노레포 구조 생성, 첫 커밋 완료
- [ ] CLAUDE.md 존재, 가드레일 요약 포함
- [ ] CI가 빈 프로젝트에서 통과(lint+test 스캐폴드)
- [ ] 시크릿 스캔 pre-commit 훅 동작(테스트: 더미 키 커밋 시 차단되는지)
- [ ] el34 Assessor 헬스체크 스크립트가 200 응답 확인
- [ ] docs/ 복사 완료, BUILD_LOG.md·QUESTIONS.md 생성

## 자기검증
1. 더미 API 키를 포함한 파일을 커밋 시도 → pre-commit 훅이 차단하는지 확인.
2. `infra/` 헬스체크 실행 → Assessor 응답 로그를 BUILD_LOG.md에 기록.
3. CI 파이프라인이 녹색인지 확인.

## 다음
DoD 충족 시 `P1_experience_graph.md`로. (EG가 다른 모든 것의 전제)
