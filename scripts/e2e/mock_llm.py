"""E2E 专用本地模拟 LLM：聊天返回占位回答，编译提示词返回可被 Wiki 解析的 JSON 词条。

仅监听 127.0.0.1，由沙箱随机端口拉起；不访问外网、不写入任何文件。
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_COMPILE_HINTS = ("source_quote", "body_md", "related_titles")


def _is_compile_prompt(system_prompt: str) -> bool:
    return any(hint in system_prompt for hint in _COMPILE_HINTS)


def _extract_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _extract_question(messages: list[dict]) -> str:
    text = _extract_user_text(messages)
    return text[:120]


def _pick_paragraph(text: str) -> str:
    """从用户消息中挑一段足够长的正文，作为词条正文与逐字摘录。"""
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n{2,}", text)]
    paragraphs = [item for item in paragraphs if len(item) >= 40 and not item.startswith("#")]
    if not paragraphs:
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[-200:] if compact else "（E2E 模拟正文）"
    return max(paragraphs, key=len)


def _entry_payload(text: str) -> list[dict[str, Any]]:
    paragraph = _pick_paragraph(text)
    title = "E2E 模拟词条"
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if len(cleaned) >= 6 and not cleaned.startswith("请") and not cleaned.startswith("你"):
            title = cleaned[:40]
            break
    quote = paragraph[:120]
    return [
        {
            "title": title,
            "aliases": ["E2E 测试"],
            "summary": paragraph[:120],
            "body_md": paragraph[:800],
            "tags": ["E2E"],
            "source_section": "",
            "source_quote": quote,
            "related_titles": [],
        }
    ]


class _Handler(BaseHTTPRequestHandler):
    server_version = "NPU-E2E-Mock/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "e2e-mock", "object": "model", "owned_by": "npu-e2e"},
                    ],
                },
            )
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self._send(404, {"error": {"message": "not found"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid json"}})
            return

        messages = payload.get("messages") or []
        system_prompt = next(
            (str(item.get("content") or "") for item in messages if item.get("role") == "system"), ""
        )
        user_text = _extract_user_text(messages)
        if _is_compile_prompt(system_prompt):
            content = json.dumps(_entry_payload(user_text), ensure_ascii=False)
        else:
            content = (
                "（E2E 模拟回答）已收到你的问题："
                f"{_extract_question(messages)}\n\n"
                "这是沙箱内的确定性回复，用于验证界面、引用与状态流转。"
            )

        prompt_tokens = sum(len(str(item.get("content") or "")) for item in messages)
        self._send(
            200,
            {
                "id": f"chatcmpl-e2e-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.get("model") or "e2e-mock",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": len(content),
                    "total_tokens": prompt_tokens + len(content),
                },
            },
        )


class MockLLM:
    def __init__(self, port: int) -> None:
        self.port = port
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "MockLLM":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"
