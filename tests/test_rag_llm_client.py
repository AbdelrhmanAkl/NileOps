"""Tests for the local-only LLM client (Ollama / Qwen) using a throw-away local server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.rag.llm import LlmUnavailable, OllamaClient, validate_local_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434/",
        "http://10.1.2.3:11434",
        "http://192.168.1.20:8000",
        "http://ollama:11434",
        "http://gpu-box.local:11434",
    ],
)
def test_local_urls_are_accepted(url):
    assert validate_local_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com", "http://8.8.8.8:11434", "http://example.com", "ftp://localhost", "localhost:11434", ""],
)
def test_external_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_local_url(url)


def serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def make_handler(reply: bytes, status: int = 200, seen: list | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)  # always drain the body, or Windows resets the connection
            if seen is not None:
                seen.append((self.path, json.loads(raw)))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    return Handler


def test_generate_sends_chat_request_and_returns_text():
    seen: list = []
    reply = json.dumps({"message": {"role": "assistant", "content": "المدة 7 أيام [1]"}}).encode()
    server, url = serve(make_handler(reply, seen=seen))
    try:
        text = OllamaClient(url, "qwen2.5:7b-instruct", timeout_seconds=5).generate("sys", "user")
    finally:
        server.shutdown()
    assert text == "المدة 7 أيام [1]"
    path, body = seen[0]
    assert path == "/api/chat"
    assert body["model"] == "qwen2.5:7b-instruct"
    assert body["stream"] is False and body["options"]["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_qwen_thinking_blocks_are_removed():
    reply = json.dumps({"message": {"content": "<think>تفكير داخلي 99</think>\nالإجابة 7 أيام [1]"}}).encode()
    server, url = serve(make_handler(reply))
    try:
        text = OllamaClient(url, "qwen3:8b", timeout_seconds=5).generate("s", "u")
    finally:
        server.shutdown()
    assert text == "الإجابة 7 أيام [1]"


@pytest.mark.parametrize(
    ("reply", "status"),
    [(b"not json", 200), (b'{"message": {}}', 200), (b'{"message": {"content": 5}}', 200), (b"{}", 500)],
)
def test_bad_server_replies_raise_llm_unavailable(reply, status):
    server, url = serve(make_handler(reply, status))
    try:
        with pytest.raises(LlmUnavailable):
            OllamaClient(url, "qwen2.5:7b-instruct", timeout_seconds=5).generate("s", "u")
    finally:
        server.shutdown()


def test_server_down_raises_llm_unavailable():
    server, url = serve(make_handler(b"{}"))
    server.shutdown()
    server.server_close()
    with pytest.raises(LlmUnavailable):
        OllamaClient(url, "qwen2.5:7b-instruct", timeout_seconds=2).generate("s", "u")
