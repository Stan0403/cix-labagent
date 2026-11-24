"""Bounded, explicit HTTP adapters. No API call is made until the user configures one."""
import json
import time
from urllib.parse import urlparse

import httpx

from .models import DomainError


class ProviderError(DomainError):
    def __init__(self, message):
        super().__init__(message, 503)


def endpoint(base: str, path: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ProviderError("模型服务地址未配置或无效，请在 .env 中填写服务根地址（通常以 /v1 结尾）")
    if parsed.query or parsed.fragment:
        raise ProviderError("模型服务根地址不能含查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ProviderError("非本机模型服务必须使用 HTTPS，避免泄露令牌和实验数据")
    return base.rstrip("/") + "/" + path


class Gateway:
    def __init__(self, settings, client=None):
        self.settings = settings
        # trust_env=False prevents accidental forwarding to a globally configured proxy.
        self.client = client or httpx.Client(timeout=settings.http_timeout, trust_env=False,
                                              follow_redirects=False)

    def close(self):
        self.client.close()

    def request(self, base, key, path, **kwargs):
        if self.settings.local_only and urlparse(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ProviderError("端侧模式仅允许 O6 本机模型服务，不会调用云端模型")
        headers = {"Authorization": "Bearer " + key} if key else {}
        try:
            with self.client.stream("POST", endpoint(base, path), headers=headers, **kwargs) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ProviderError("模型响应过大，已拒绝")
                return json.loads(body)
        except httpx.TimeoutException as exc:
            raise ProviderError("模型请求超时；未使用模拟结果替代，请检查服务或调整超时") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"模型服务返回 HTTP {exc.response.status_code}；检查地址、密钥、模型和接口兼容性") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("模型服务不可连接或响应不是有效 JSON；未写入推测结果") from exc

    def chat(self, messages, tools=None, vision=False, *, response_format=None, decision=False, conversation=False):
        s = self.settings
        base, key, model = ((s.vlm_base_url, s.vlm_api_key, s.vlm_model) if vision else
                            (s.llm_base_url, s.llm_api_key, s.llm_model))
        if not base or not model:
            raise ProviderError("视觉模型未配置" if vision else "对话模型未配置：请填写 LLM_BASE_URL 和 LLM_MODEL")
        payload = {"model": model, "messages": messages, "max_tokens": 256}
        if response_format is not None:
            payload.update(response_format=response_format,max_tokens=384,temperature=0,
                           chat_template_kwargs={"enable_thinking":False})
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if conversation:
            payload.update(cache_prompt=True, temperature=0,
                           chat_template_kwargs={"enable_thinking":False})
        if decision:
            payload.update(max_tokens=s.agent_max_tokens, cache_prompt=True, temperature=0,
                           chat_template_kwargs={"enable_thinking": False})
        started = time.perf_counter()
        result = self.request(base, key, "chat/completions", json=payload)
        try:
            choice = result["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError()
            if decision or conversation:
                if choice.get("finish_reason") == "length":
                    if decision:
                        raise ProviderError("模型行动输出被截断，未执行；请重试或调整决策输出上限")
                message = {**message, "_performance": {
                    "http_seconds": round(time.perf_counter() - started, 4),
                    "prompt_chars": sum(len(m.get("content") or "") for m in messages),
                    "max_tokens": payload["max_tokens"],
                    "usage": result.get("usage"),
                    "timings": result.get("timings"),
                }}
            return message
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("模型响应不符合 Chat Completions 格式") from exc

    def transcribe(self, data: bytes, filename: str, mime: str):
        s = self.settings
        if not s.asr_base_url or not s.asr_model:
            raise ProviderError("语音服务未配置，请填写 ASR_BASE_URL / ASR_MODEL；也可直接输入文本")
        result = self.request(s.asr_base_url, s.asr_api_key, "audio/transcriptions",
                              data={"model": s.asr_model}, files={"file": (filename, data, mime)})
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ProviderError("语音服务响应中缺少 text 字段")
        return result["text"][:4000]


def json_content(content):
    if not isinstance(content, str):
        raise ProviderError("模型未返回结构化文本")
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError) as exc:
        raise ProviderError("模型返回的结构化结果无效；未更新实验状态") from exc
