# OAICopilot Model Settings

Use `LiteLLM` as the provider and `OpenAI` as the API mode for all entries.

| Use | Provider ID | Model ID | Config ID | API Mode | Base URL | Context Length | Max Tokens | Max Completion Tokens | Temperature | Top P | Delay |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|
| NPU chat/agent | LiteLLM | `npu-act-qwen25-coder-0p5b-instruct` | `npu-instruct` | OpenAI | `http://localhost:4000/v1` | 8192 | 2048 | 2048 | 0.2 | 1 | 0 |
| GB10 coding | LiteLLM | `gb10-act-qwen3-coder-next` | `gb10-act` | OpenAI | `http://localhost:4000/v1` | 65536 | 8192 | 8192 | 0.2 | 1 | 0 |
| GB10 planning | LiteLLM | `gb10-plan-qwen38-27b` | `gb10-plan` | OpenAI | `http://localhost:4000/v1` | 65536 | 12288 | 12288 | 0.2 | 1 | 0 |
| Blackwell coding | LiteLLM | `blackwell-act-qwen25-coder-14b` | `blackwell-act` | OpenAI | `http://localhost:4000/v1` | 8192 | 4096 | 4096 | 0.2 | 1 | 0 |
| NPU autocomplete/FIM | LiteLLM | `npu-fim-qwen25-coder-0p5b` | `npu-fim` | OpenAI | `http://localhost:4000/v1` | 8192 | 256 | 256 | 0 | 1 | 0 |
| Blackwell autocomplete/FIM | LiteLLM | `blackwell-fim-qwen25-coder-1p5b` | `blackwell-fim` | OpenAI | `http://localhost:4000/v1` | 8192 | 256 | 256 | 0 | 1 | 0 |

## Additional settings

* Set Supports Vision to `Default (False)` for every model.
* Use the Instruct, GB10, and Blackwell Act models for chat and agent requests.
* Use the FIM models for autocomplete/completion requests.
* OAICopilot connects to LiteLLM on port `4000`; LiteLLM forwards NPU requests to the OpenVINO server on port `4001`.
