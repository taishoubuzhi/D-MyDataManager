"""HTTP 适配器：外部模型（OpenAI 兼容 / Ollama / llama-server）直接走网络调用。

三个后端协议相近，共用 `_HttpAdapter` 的请求与流式解析；差异只在路径与请求体形状。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Iterator

from app.sdk import storage

from .base import Adapter, AdapterError

__all__ = ["HttpAdapter", "LlamaServerAdapter", "OllamaAdapter", "OpenAICompatAdapter"]

DEFAULT_TIMEOUT = 120.0


class HttpAdapter(Adapter):
    """HTTP 后端的公共部分：拼 URL、带鉴权头、发 JSON、按 SSE 流式读。"""

    name = "http"
    #: 路径模板，子类覆盖
    chat_path = "/v1/chat/completions"
    embedding_path = "/v1/embeddings"
    #: 探活路径（空 = 不做探活）
    health_path = ""

    def __init__(self, record, settings) -> None:
        super().__init__(record, settings)
        self.base_url = str(record.api.get("base_url") or "").rstrip("/")

    # -------------------------------------------------------------- 请求
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self.record.api_headers())
        key = self.settings.secret(str(self.record.api.get("api_key_ref") or ""))
        if key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise AdapterError("外部模型还没有填写接口地址")
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    def _opener(self):
        proxy = self.settings.proxy
        if proxy:
            return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener()

    def _request(self, path: str, body: dict | None, *, timeout: float | None, method: str = "POST"):
        data = storage.dumps(body or {}).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self._url(path), data=data, headers=self._headers(), method=method)
        try:
            return self._opener().open(request, timeout=float(timeout or DEFAULT_TIMEOUT))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                detail = ""
            raise AdapterError(f"接口返回 {exc.code}：{detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"连不上接口：{exc.reason}") from exc
        except TimeoutError as exc:
            raise AdapterError("接口超时") from exc

    def _json(self, path: str, body: dict | None, *, timeout: float | None, method: str = "POST") -> dict:
        with self._request(path, body, timeout=timeout, method=method) as response:
            raw = response.read().decode("utf-8", "replace")
        try:
            payload = storage.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise AdapterError(f"接口返回的不是 JSON：{raw[:200]}") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    def _stream_lines(self, path: str, body: dict, *, timeout: float | None) -> Iterator[dict]:
        """SSE 流：逐行解析 `data: {…}`（本实现各后端都用这个形状）。"""
        with self._request(path, body, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    return
                try:
                    yield storage.loads(chunk)
                except ValueError:
                    continue

    # -------------------------------------------------------------- 生命周期
    def available(self) -> bool:
        return bool(self.base_url)

    def start(self) -> None:
        if not self.base_url:
            raise AdapterError("外部模型还没有填写接口地址")
        if self.health_path:
            try:
                self._json(self.health_path, None, timeout=10.0, method="GET")
            except AdapterError as exc:
                raise AdapterError(f"服务不可用：{exc}") from exc
        self._loaded = True

    def stop(self) -> None:
        self._loaded = False

    # -------------------------------------------------------------- 调用
    def invoke(self, task, payload=None, *, stream=False, timeout=None):
        if not self.loaded:
            self.start()
        body = self._build_body(task, payload or {})
        path = self._path_of(task)
        if stream and self._supports_stream(task):
            return self._iter_text(path, body, timeout=timeout)
        data = self._json(path, body, timeout=timeout)
        return self._extract(task, data)

    def _path_of(self, task: str) -> str:
        if task in ("embedding", "rerank"):
            return self.embedding_path
        return self.chat_path

    def _supports_stream(self, task: str) -> bool:
        return task in ("chat", "completion")

    def _build_body(self, task: str, payload: dict) -> dict:
        raise NotImplementedError

    def _extract(self, task: str, data: dict) -> Any:
        raise NotImplementedError

    def _iter_text(self, path: str, body: dict, *, timeout: float | None) -> Iterator[str]:
        raise NotImplementedError

    def info(self) -> dict:
        return {"adapter": self.name, "backend": self.base_url, "loaded": self.loaded, "model": self.record.api.get("model", "")}


class OpenAICompatAdapter(HttpAdapter):
    """OpenAI 兼容接口（也用于 llama-server / vLLM / LM Studio 等）。"""

    name = "openai_compat"
    chat_path = "/v1/chat/completions"
    embedding_path = "/v1/embeddings"
    health_path = "/v1/models"

    def _build_body(self, task: str, payload: dict) -> dict:
        params = dict(self.record.api_params())
        params.update({key: value for key, value in payload.items() if key != "messages"})
        body = {**params, "model": str(self.record.api.get("model") or "")}
        if task in ("chat", "vision"):
            body["messages"] = payload.get("messages") or [{"role": "user", "content": str(payload.get("prompt") or "")}]
        elif task == "completion":
            body["messages"] = [{"role": "user", "content": str(payload.get("prompt") or "")}]
        elif task in ("embedding", "rerank"):
            body["input"] = payload.get("input") or payload.get("prompt") or ""
        else:
            body.update(payload)
        return body

    def _extract(self, task: str, data: dict) -> Any:
        if task in ("chat", "completion", "vision"):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                return str(message.get("content") or choices[0].get("text") or "")
            return str(data.get("content") or "")
        if task in ("embedding", "rerank"):
            return data.get("data") if "data" in data else data
        return data

    def _iter_text(self, path: str, body: dict, *, timeout: float | None) -> Iterator[str]:
        stream_body = {**body, "stream": True}
        for chunk in self._stream_lines(path, stream_body, timeout=timeout):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or choices[0].get("text")
            if text:
                yield str(text)


class OllamaAdapter(HttpAdapter):
    """本地 Ollama 服务：`/api/chat`、`/api/embeddings`。"""

    name = "ollama"
    chat_path = "/api/chat"
    embedding_path = "/api/embeddings"
    health_path = "/api/tags"

    def __init__(self, record, settings) -> None:
        super().__init__(record, settings)
        if not self.base_url:
            self.base_url = "http://127.0.0.1:11434"

    def _build_body(self, task: str, payload: dict) -> dict:
        model = str(self.record.api.get("model") or "")
        params = dict(self.record.api_params())
        params.update(payload)
        if task in ("chat", "vision"):
            messages = params.pop("messages", [{"role": "user", "content": str(params.pop("prompt", ""))}])
            return {"model": model, "messages": messages, "stream": False, **params}
        if task == "completion":
            return {"model": model, "prompt": str(params.pop("prompt", "")), "stream": False, **params}
        return {"model": model, "prompt": str(params.get("input") or params.get("prompt") or ""), **params}

    def _extract(self, task: str, data: dict) -> Any:
        if isinstance(data.get("message"), dict):
            return str(data["message"].get("content") or "")
        if data.get("response") is not None:
            return str(data.get("response") or "")
        if data.get("embedding") is not None:
            return data.get("embedding")
        return data

    def _iter_text(self, path: str, body: dict, *, timeout: float | None) -> Iterator[str]:
        line_iter = self._stream_lines(path, {**body, "stream": True}, timeout=timeout)
        try:
            for chunk in line_iter:
                message = chunk.get("message")
                if isinstance(message, dict) and message.get("content"):
                    yield str(message["content"])
                elif chunk.get("response"):
                    yield str(chunk["response"])
        except AdapterError:
            # Ollama 流式走的是 NDJSON 而不是 `data:` 前缀，退化为整块返回
            data = self._json(path, {**body, "stream": False}, timeout=timeout)
            text = self._extract("chat", data)
            if text:
                yield str(text)


class LlamaServerAdapter(OpenAICompatAdapter):
    """llama-server（llama.cpp 自带服务）：接口与 OpenAI 兼容，探活用 `/health`。"""

    name = "llama_server"
    chat_path = "/v1/chat/completions"
    embedding_path = "/v1/embeddings"
    health_path = "/health"

    def __init__(self, record, settings) -> None:
        super().__init__(record, settings)
        if not self.base_url:
            self.base_url = "http://127.0.0.1:8080"
