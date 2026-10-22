"""OpenAI 兼容的本地模拟 LLM 服务（仅用于开发/演示，不对外提供能力）。

用途：在没有真实 API Key 的环境里，验证「模型管理 → 真实 HTTP 调用 → 问答链路
fallback=false」这条完整链路。

启动：
    .\.venv\Scripts\python.exe scripts\mock_llm_server.py --port 8010

然后在「模型」页添加 Provider：
    type=openai, base_url=http://127.0.0.1:8010/v1, api_key=sk-test
再添加模型（如 mock-gpt），设为默认即可在对话中看到 fallback=false。

支持：
    GET  /v1/models
    POST /v1/chat/completions
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Windows 控制台默认 cp1252/cp936，统一 UTF-8 输出避免中文日志报错。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover
    pass


def _extract_question(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _extract_context_count(system_prompt: str) -> int:
    return len(re.findall(r"\[(\d{1,2})\]", system_prompt or ""))


class Handler(BaseHTTPRequestHandler):
    server_version = "MockLLM/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print(f"[mock-llm] {self.address_string()} {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "mock-gpt", "object": "model", "owned_by": "npu-assistant"},
                        {"id": "mock-gpt-mini", "object": "model", "owned_by": "npu-assistant"},
                    ],
                },
            )
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self._send_json(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "invalid json"}})
            return

        messages = payload.get("messages") or []
        system_prompt = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
        question = _extract_question(messages)
        context_refs = _extract_context_count(system_prompt)
        content = (
            "【本地模拟模型】已成功收到请求。\n\n"
            f"你的问题是：{question[:120]}\n"
            f"系统提示词中包含 {context_refs} 处知识库编号引用。"
            + ("" if context_refs else "（本次未检索到知识库资料）")
            + "\n\n这是一条用于验证模型接入链路的模拟回复；配置真实 OpenAI 兼容模型后即可获得正式回答。"
        )
        prompt_tokens = sum(len(str(m.get("content") or "")) for m in messages)
        self._send_json(
            200,
            {
                "id": f"chatcmpl-mock-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.get("model") or "mock-gpt",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": len(content),
                    "total_tokens": prompt_tokens + len(content),
                },
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible mock LLM server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock OpenAI server: http://{args.host}:{args.port}/v1  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
