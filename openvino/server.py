import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import openvino_genai as ov_genai

ROOT = Path(__file__).resolve().parent
MODELS_ROOT = ROOT / "models"
MODELS_FILE = ROOT / "models.txt"

HOST = os.getenv("OPENVINO_SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("OPENVINO_SERVER_PORT", "4001"))
MAX_BODY_BYTES = 10 * 1024 * 1024

@dataclass(frozen=True)
class ModelSpec:
    name: str
    relative_path: str
    device: str
    task: str
    source_model: str
    weight_format: str

    @property
    def model_path(self) -> Path:
        path = Path(self.relative_path)

        if path.is_absolute():
            return path

        return MODELS_ROOT / path

class ModelRuntime:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self.lock = threading.Lock()

        if not self.spec.model_path.exists():
            raise FileNotFoundError(
                f"Model path does not exist: {self.spec.model_path}"
            )

        print(
            f"Loading {self.spec.name} "
            f"on {self.spec.device}: {self.spec.model_path}",
            flush=True,
        )

        self.pipeline = ov_genai.LLMPipeline(
            str(self.spec.model_path),
            self.spec.device,
        )

        print(
            f"Loaded {self.spec.name} successfully.",
            flush=True,
        )

    def generate(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> str:
        max_tokens = max(1, min(int(max_tokens), 4096))
        temperature = max(0.0, min(float(temperature), 2.0))
        top_p = max(0.01, min(float(top_p), 1.0))

        with self.lock:
            try:
                config = ov_genai.GenerationConfig()
                config.max_new_tokens = max_tokens
                config.top_p = top_p
                config.do_sample = temperature > 0.0

                if config.do_sample:
                    config.temperature = temperature

                result = self.pipeline.generate(prompt, config)

            except (AttributeError, TypeError):
                result = self.pipeline.generate(
                    prompt,
                    max_new_tokens=max_tokens,
                )

        return str(result)

def load_model_specs() -> list[ModelSpec]:
    if not MODELS_FILE.exists():
        raise FileNotFoundError(
            f"models.txt was not found: {MODELS_FILE}"
        )

    specs: list[ModelSpec] = []

    for line_number, raw_line in enumerate(
        MODELS_FILE.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        fields = line.split("|", 8)

        if len(fields) != 9:
            raise ValueError(
                f"Invalid models.txt line {line_number}. "
                "Expected nine pipe-delimited fields."
            )

        name = fields[0].strip()
        relative_path = fields[1].strip()
        device = fields[2].strip().upper()
        task = fields[3].strip().lower()
        source_model = fields[4].strip()
        weight_format = fields[5].strip().lower()

        if not name:
            raise ValueError(
                f"Missing model name on line {line_number}."
            )

        if device not in {"CPU", "GPU", "NPU"}:
            raise ValueError(
                f"Unsupported device '{device}' on line {line_number}."
            )

        if task not in {"fim", "chat"}:
            raise ValueError(
                f"Unsupported task '{task}' on line {line_number}."
            )

        specs.append(
            ModelSpec(
                name=name,
                relative_path=relative_path,
                device=device,
                task=task,
                source_model=source_model,
                weight_format=weight_format,
            )
        )

    if not specs:
        raise ValueError("No models were found in models.txt.")

    return specs

def load_runtimes() -> dict[str, ModelRuntime]:
    specs = load_model_specs()
    runtimes: dict[str, ModelRuntime] = {}

    for spec in specs:
        if spec.task in runtimes:
            raise ValueError(
                f"Only one model per task is currently supported. "
                f"Duplicate task: {spec.task}"
            )

        runtimes[spec.task] = ModelRuntime(spec)

    if "fim" not in runtimes:
        raise ValueError(
            "models.txt must contain one task=fim model."
        )

    if "chat" not in runtimes:
        raise ValueError(
            "models.txt must contain one task=chat model."
        )

    return runtimes

RUNTIMES = load_runtimes()

MODEL_BY_NAME: dict[str, ModelRuntime] = {}

for runtime in RUNTIMES.values():
    model_key = runtime.spec.name.lower()

    if model_key in MODEL_BY_NAME:
        raise ValueError(
            f"Duplicate model name in models.txt: "
            f"{runtime.spec.name}"
        )

    MODEL_BY_NAME[model_key] = runtime

def select_runtime(
    requested_model: str | None,
    default_task: str,
) -> ModelRuntime:
    if not requested_model:
        return RUNTIMES[default_task]

    runtime = MODEL_BY_NAME.get(
        requested_model.strip().lower()
    )

    if runtime is None:
        available = ", ".join(
            runtime.spec.name
            for runtime in MODEL_BY_NAME.values()
        )

        raise ValueError(
            f"Unknown model '{requested_model}'. "
            f"Available models: {available}"
        )

    return runtime

def normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))

        return "".join(parts)

    return str(content)

def build_chat_prompt(
    messages: list[dict[str, Any]],
) -> str:
    if not messages:
        raise ValueError("The messages array cannot be empty.")

    prompt_parts: list[str] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = normalize_message_content(
            message.get("content", "")
        )

        prompt_parts.append(
            f"<|im_start|>{role}\n"
            f"{content}"
            f"<|im_end|>\n"
        )

    prompt_parts.append("<|im_start|>assistant\n")

    return "".join(prompt_parts)

def get_generation_parameters(
    payload: dict[str, Any],
) -> tuple[int, float, float]:
    max_tokens = payload.get(
        "max_tokens",
        payload.get("max_new_tokens", 256),
    )

    temperature = payload.get("temperature", 0.2)
    top_p = payload.get("top_p", 1.0)

    return (
        int(max_tokens),
        float(temperature),
        float(top_p),
    )

def make_chat_response(
    model_name: str,
    output: str,
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": output,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

def make_completion_response(
    model_name: str,
    output: str,
) -> dict[str, Any]:
    return {
        "id": f"cmpl-{uuid.uuid4().hex}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "text": output,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

class OpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_json(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization",
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(
        self,
        message: str,
        status_code: int = 400,
    ) -> None:
        self.send_json(
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                }
            },
            status_code,
        )

    def read_json_body(self) -> dict[str, Any]:
        content_length = int(
            self.headers.get("Content-Length", "0")
        )

        if content_length <= 0:
            raise ValueError("Request body is empty.")

        if content_length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")

        raw_body = self.rfile.read(content_length)

        payload = json.loads(
            raw_body.decode("utf-8")
        )

        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")

        return payload

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization",
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(
                {
                    "status": "ok",
                    "models": [
                        runtime.spec.name
                        for runtime in MODEL_BY_NAME.values()
                    ],
                    "devices": {
                        runtime.spec.name: runtime.spec.device
                        for runtime in MODEL_BY_NAME.values()
                    },
                }
            )
            return

        if self.path == "/v1/models":
            now = int(time.time())

            self.send_json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": runtime.spec.name,
                            "object": "model",
                            "created": now,
                            "owned_by": "local",
                        }
                        for runtime in MODEL_BY_NAME.values()
                    ],
                }
            )
            return

        self.send_error_json(
            "Unknown endpoint.",
            404,
        )

    def do_POST(self) -> None:
        try:
            payload = self.read_json_body()

            if self.path == "/v1/chat/completions":
                self.handle_chat_completion(payload)
                return

            if self.path == "/v1/completions":
                self.handle_text_completion(payload)
                return

            self.send_error_json(
                "Unknown endpoint.",
                404,
            )

        except ValueError as exc:
            self.send_error_json(str(exc), 400)

        except Exception as exc:
            print(
                f"Request failed: {exc}",
                flush=True,
            )

            self.send_error_json(
                "Internal server error.",
                500,
            )

    def handle_chat_completion(
        self,
        payload: dict[str, Any],
    ) -> None:
        if payload.get("stream", False):
            raise ValueError(
                "Streaming is not implemented yet."
            )

        runtime = select_runtime(
            payload.get("model"),
            "chat",
        )

        if runtime.spec.task != "chat":
            raise ValueError(
                f"Model '{runtime.spec.name}' is not a chat model."
            )

        messages = payload.get("messages")

        if not isinstance(messages, list):
            raise ValueError(
                "The messages field must be an array."
            )

        prompt = build_chat_prompt(messages)

        max_tokens, temperature, top_p = (
            get_generation_parameters(payload)
        )

        output = runtime.generate(
            prompt,
            max_tokens,
            temperature,
            top_p,
        )

        self.send_json(
            make_chat_response(
                runtime.spec.name,
                output,
            )
        )

    def handle_text_completion(
        self,
        payload: dict[str, Any],
    ) -> None:
        if payload.get("stream", False):
            raise ValueError(
                "Streaming is not implemented yet."
            )

        runtime = select_runtime(
            payload.get("model"),
            "fim",
        )

        if runtime.spec.task != "fim":
            raise ValueError(
                f"Model '{runtime.spec.name}' is not a FIM model."
            )

        prompt = payload.get("prompt", "")

        if isinstance(prompt, list):
            prompt = prompt[0] if prompt else ""

        if not isinstance(prompt, str) or not prompt:
            raise ValueError(
                "The prompt field must be a non-empty string."
            )

        max_tokens, temperature, top_p = (
            get_generation_parameters(payload)
        )

        output = runtime.generate(
            prompt,
            max_tokens,
            temperature,
            top_p,
        )

        self.send_json(
            make_completion_response(
                runtime.spec.name,
                output,
            )
        )

    def log_message(
        self,
        format_string: str,
        *args: Any,
    ) -> None:
        print(
            f"[server] {self.address_string()} "
            f"{format_string % args}",
            flush=True,
        )

def main() -> None:
    server = ThreadingHTTPServer(
        (HOST, PORT),
        OpenAIHandler,
    )

    server.daemon_threads = True

    print(
        f"OpenAI-compatible server listening on "
        f"http://{HOST}:{PORT}",
        flush=True,
    )

    for runtime in MODEL_BY_NAME.values():
        print(
            f"  {runtime.spec.name} "
            f"({runtime.spec.task}, {runtime.spec.device})",
            flush=True,
        )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.", flush=True)
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
