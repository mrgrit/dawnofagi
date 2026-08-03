"""모델 클라이언트 — EG 가 고른 모델로 실제 호출한다.

라우팅은 **여기서 결정하지 않는다.** EG(`USES_MODEL`)와 통제 평면(`gate.model.policy`)이
정하고, 이 모듈은 그 결과를 실행할 뿐이다 (P2 지시문 §3).

    EG ModelPolicy       →  provider + model id
    ─────────────────────────────────────────────────────────────
    model:opus           →  anthropic  claude-opus-5
    model:sonnet         →  anthropic  claude-sonnet-5
    model:haiku          →  anthropic  claude-haiku-4-5
    model:gptoss         →  ollama     $LOCAL_LLM_MODEL   (사내 GPU)
    model:openlocal      →  ollama     $LOCAL_LLM_MODEL   (사내 GPU)

**L3 자산 관여 시 클라우드 호출은 시도조차 하지 않는다** — pol:l3-local-only 는
"실패하면 막는다"가 아니라 "애초에 보내지 않는다"이다.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# EG ModelPolicy id → (provider, 모델 id 또는 env 키)
MODEL_MAP: dict[str, tuple[str, str]] = {
    # Claude Code 구독(헤드리스 CLI)으로 호출한다 — 종량 API 키를 쓰지 않는다.
    # provider 를 바꿔도 EG 배정(model:opus 등)은 그대로다: 라우팅 결정은
    # 여전히 EG 와 통제 평면이 하고, 이 표는 그 결과를 실행할 뿐이다.
    "model:opus": ("claude-code", "opus"),
    "model:sonnet": ("claude-code", "sonnet"),
    "model:haiku": ("claude-code", "haiku"),
    # 종량 API 키를 쓰는 직접 호출 경로. 배정은 없지만 코드는 남겨둔다.
    # ("anthropic", "claude-opus-5") 처럼 되돌리면 즉시 그 경로로 돈다.
    "model:gptoss": ("ollama", "$LOCAL_LLM_MODEL"),
    "model:openlocal": ("ollama", "$LOCAL_LLM_MODEL"),
    # 판정 전용 — 피감시 모델과 **다른 모델**이어야 한다 (담합 방지).
    # 같은 env 를 쓰면 자기 채점이 되므로 별도 키다.
    "model:judge": ("ollama", "$LOCAL_JUDGE_MODEL"),
}

# claude-code 도 호출은 Anthropic 으로 나간다. 여기 넣지 않으면
# pol:l3-local-only 가 뚫려 L3 자산이 클라우드로 샌다.
CLOUD_PROVIDERS = {"anthropic", "claude-code"}


class LLMError(Exception):
    """모델 호출 실패."""


class PolicyViolation(LLMError):
    """정책이 이 호출을 막았다 — 실패가 아니라 설계된 차단이다."""


@dataclass
class Resolved:
    """EG 라우팅 결과를 실행 가능한 형태로."""

    model_policy_id: str
    provider: str
    model: str
    is_local: bool
    reason: str

    @property
    def is_cloud(self) -> bool:
        return self.provider in CLOUD_PROVIDERS


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def resolve(model_policy_id: str | None, *, touches_l3: bool) -> Resolved:
    """EG ModelPolicy id 를 실행 가능한 (provider, model) 로 푼다."""
    if not model_policy_id:
        raise PolicyViolation(
            "EG 에 USES_MODEL 배정이 없다 — 어떤 모델을 쓸지 결정할 수 없다. "
            "eg/seed/01_foundation.json 에 USES_MODEL 엣지를 추가하라."
        )
    if model_policy_id not in MODEL_MAP:
        raise LLMError(
            f"알 수 없는 ModelPolicy: {model_policy_id} "
            f"(dawn_agents/llm.py 의 MODEL_MAP 에 매핑을 추가하라)"
        )
    provider, model = MODEL_MAP[model_policy_id]
    if model.startswith("$"):
        env = model[1:]
        model = os.getenv(env, "")
        if not model:
            raise LLMError(f"{env} 가 비어 있다 (.env 참조)")

    is_local = provider not in CLOUD_PROVIDERS
    if touches_l3 and not is_local:
        raise PolicyViolation(
            f"pol:l3-local-only — L3 자산 관여인데 EG 가 클라우드 모델"
            f"({model_policy_id}/{model})을 배정했다. 전송하지 않는다."
        )
    return Resolved(
        model_policy_id=model_policy_id,
        provider=provider,
        model=model,
        is_local=is_local,
        reason=("L3 관여 — 로컬 강제" if touches_l3 else "EG USES_MODEL 배정"),
    )


class LLMClient:
    """provider 별 호출. 실패는 숨기지 않는다."""

    def __init__(self, *, timeout: int = 600) -> None:
        self.timeout = timeout
        self._anthropic = None

    # ── anthropic ──────────────────────────────────────────────────────
    def _client(self):
        if self._anthropic is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise LLMError("anthropic 패키지가 없다: pip install anthropic") from exc
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise LLMError(
                    "ANTHROPIC_API_KEY 가 없다 — 클라우드 모델을 호출할 수 없다. "
                    ".env 에 키를 넣거나, EG 의 USES_MODEL 을 로컬 모델로 바꿔라."
                )
            self._anthropic = anthropic.Anthropic(timeout=self.timeout)
        return self._anthropic

    def _call_anthropic(
        self, model: str, system: str, prompt: str, max_tokens: int, effort: str
    ) -> Completion:
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        # effort 는 output_config 안에 들어간다 (top-level 아님)
        if effort:
            kwargs["output_config"] = {"effort": effort}
        # temperature/top_p/top_k 는 Opus 5 / Sonnet 5 에서 400 — 보내지 않는다.

        resp = client.messages.create(**kwargs)

        # 안전 분류기가 거절하면 HTTP 200 + stop_reason="refusal" 로 온다.
        # content[0] 을 무조건 읽으면 여기서 깨진다.
        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            cat = getattr(detail, "category", None) if detail else None
            raise PolicyViolation(
                f"모델이 요청을 거절했다 (stop_reason=refusal, category={cat}). "
                f"이건 오류가 아니라 안전 분류기 판정이다."
            )

        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(
            text=text,
            model=resp.model,
            provider="anthropic",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason or "",
        )

    # ── claude-code (구독 CLI, 헤드리스) ──────────────────────────────
    # Claude Code 는 원래 도구를 든 에이전트 하네스다. 그대로 쓰면 자기 Bash/Read/
    # Write 로 움직여서 이 회사의 gate.yaml 이 통제할 대상이 사라진다.
    # --allowedTools "" 로 도구를 전부 떼어 순수 completion 엔드포인트로 강등한다.
    def _call_claude_code(
        self, model: str, system: str, prompt: str, max_tokens: int
    ) -> Completion:
        cli = os.getenv("CLAUDE_CLI") or os.path.expanduser("~/.local/bin/claude")
        if not os.path.exists(cli):
            raise LLMError(
                f"Claude Code CLI 를 찾지 못했다: {cli} — 설치 후 로그인하거나 "
                f"CLAUDE_CLI 로 경로를 지정하라."
            )
        cmd = [cli, "-p", "--output-format", "json", "--allowedTools", "", "--model", model]
        if system:
            cmd += ["--append-system-prompt", system]
        try:
            r = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude-code 응답 시간 초과 ({self.timeout}s)") from e
        if r.returncode != 0:
            raise LLMError(f"claude-code 종료코드 {r.returncode}: {r.stderr[:300]}")
        try:
            body = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude-code 응답 파싱 실패: {r.stdout[:300]}") from e
        if body.get("is_error"):
            raise LLMError(f"claude-code 오류: {str(body.get('result'))[:300]}")
        usage = body.get("usage") or {}
        return Completion(
            text=body.get("result", ""),
            model=model,
            provider="claude-code",
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            stop_reason=body.get("stop_reason", ""),
        )

    # ── ollama (사내 GPU) ──────────────────────────────────────────────
    def _call_ollama(self, model: str, system: str, prompt: str, max_tokens: int) -> Completion:
        base = os.getenv("LOCAL_LLM_BASE_URL", "").rstrip("/")
        if not base:
            raise LLMError("LOCAL_LLM_BASE_URL 이 비어 있다 (.env 참조)")
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            # 추론(thinking) 모델은 생각에 토큰을 다 쓰고 본문을 못 내는 경우가 있다.
            # 실제로 judge 가 빈 응답을 받아 "판정 불가"로 떨어졌다(2026-08-02).
            # 우리가 쓰는 건 결론이므로 생각 출력은 끈다 — 지원 안 하는 모델은 무시한다.
            "think": False,
            "options": {"num_predict": max_tokens},
        }
        req = urllib.request.Request(
            f"{base}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise LLMError(f"ollama HTTP {e.code}: {e.read(300).decode('utf-8', 'replace')}") from e
        except Exception as e:
            raise LLMError(
                f"사내 GPU 서버에 닿지 못했다 ({type(e).__name__}: {e}). "
                f"VPN 이 연결돼 있는지 확인하라 — make gpu-check. "
                f"L3 업무는 클라우드로 대체하지 않는다."
            ) from e

        return Completion(
            text=body.get("response", ""),
            model=body.get("model", model),
            provider="ollama",
            input_tokens=int(body.get("prompt_eval_count", 0)),
            output_tokens=int(body.get("eval_count", 0)),
            stop_reason=body.get("done_reason", ""),
        )

    # ── 공개 ────────────────────────────────────────────────────────────
    def complete(
        self,
        resolved: Resolved,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2000,
        effort: str = "medium",
    ) -> Completion:
        if resolved.provider == "anthropic":
            return self._call_anthropic(resolved.model, system, prompt, max_tokens, effort)
        if resolved.provider == "claude-code":
            return self._call_claude_code(resolved.model, system, prompt, max_tokens)
        if resolved.provider == "ollama":
            return self._call_ollama(resolved.model, system, prompt, max_tokens)
        raise LLMError(f"알 수 없는 provider: {resolved.provider}")

    def available(self, resolved: Resolved) -> tuple[bool, str]:
        """호출 전에 이 모델을 쓸 수 있는지 확인 (사전 점검용)."""
        if resolved.provider == "anthropic":
            if not os.getenv("ANTHROPIC_API_KEY"):
                return False, "ANTHROPIC_API_KEY 없음"
            return True, ""
        if resolved.provider == "claude-code":
            cli = os.getenv("CLAUDE_CLI") or os.path.expanduser("~/.local/bin/claude")
            if not os.path.exists(cli):
                return False, f"Claude Code CLI 없음 ({cli})"
            return True, ""
        if resolved.provider == "ollama":
            base = os.getenv("LOCAL_LLM_BASE_URL", "").rstrip("/")
            if not base:
                return False, "LOCAL_LLM_BASE_URL 없음"
            try:
                with urllib.request.urlopen(f"{base}/api/tags", timeout=8):
                    return True, ""
            except Exception as e:
                return False, f"사내 GPU 미도달 ({type(e).__name__}) — VPN 확인"
        return False, f"알 수 없는 provider: {resolved.provider}"
