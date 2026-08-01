# infra/ci — CI 정의

`github-actions-ci.yml` 이 이 저장소의 CI 정의다.
평소엔 `.github/workflows/ci.yml` 에 있어야 하지만, **`workflow` 스코프가 없는
PAT 로는 그 경로를 push 할 수 없다** (GitHub 의 보호 장치).

## 활성화

```bash
make ci-enable      # infra/ci/github-actions-ci.yml → .github/workflows/ci.yml
git add .github/workflows/ci.yml && git commit -m "[P0][DoD-3] CI 활성화" && git push
```

push 가 `refusing to allow a Personal Access Token to create or update workflow` 로 거부되면
토큰에 **`workflow` 스코프**를 추가하라 (Settings → Developer settings → Personal access tokens).

## CI 가 보는 것

| 잡 | 내용 |
|---|---|
| `secrets` | gitleaks — 히스토리 전체. 다른 잡보다 먼저 실패해야 한다 |
| `check` | ruff lint/format · 레지스트리 정합성 · 통제 평면 컴파일 · Control Readiness ≥80 · pytest (Python 3.10 / 3.12) |
| `bootstrap` | `./bootstrap.sh --no-el34 --no-sudo` 가 fresh 러너에서 실제로 도는지 |

로컬에서 같은 것을 돌리려면 `make check`.
