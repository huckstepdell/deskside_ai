# DeskSide AI

DeskSide AI is a modular AI stack that combines **Tailscale Aperture**, **Ollama**, and **OpenVINO** to provide a flexible, local-first AI development environment. Tailscale Aperture provides both model routing and the web UI.

## Repository Structure

```
CHANGELOG.md
LICENSE
continue/
    config.yaml
deprecated-litellm/
    .env
    .env.template
    config.yaml
    docker-compose.yml
    oaicopilot.json
    oaicopilot.md
    start.ps1
    stop.ps1
    vscode.copilot.json
deprecated-open-webui/
    .env
    .env.template
    docker-compose.yml
    start.ps1
    stop.ps1
npmplus/
    docker/
        docker-compose.yml
        start.ps1
        stop.ps1
ollama/
    README
    docker/
        docker-compose.blackwell.yml
        docker-compose.gb10.yml
        docker-compose.yml
        start.ps1
        start.sh
        stop.ps1
        stop.sh
    model_lists/
        blackwell_4000.txt
        gb10.txt
        intel_npu.txt
openvino/
    .env
    install.ps1
    models.txt
    server.py
    start.ps1
    stop.ps1
    models/
```

## Components

- **Tailscale Aperture** – Routing layer and web UI. Exposes multiple model tiers (Blackwell, GB10, planning, autocomplete) using service discovery and secure tunnels.
- **Ollama** – Dockerized LLM runtime for GB10 and Blackwell. **Must be running on the GB10 and Blackwell workstations** to serve the models.
- **OpenVINO** – Local NPU model hosting (qwen2.5-coder-0.5b). Started via `openvino/start.ps1`.
- **npmplus** – Node‑based utilities (not detailed here).
- **continue** – Continue.dev configuration for IDE integration.
- **deprecated-litellm** – Legacy LiteLLM routing (replaced by Tailscale Aperture).
- **deprecated-open-webui** – Legacy Open WebUI (replaced by Tailscale Aperture web UI).

## Getting Started

1. **Install prerequisites**
   - Docker Desktop
   - 1Password CLI (`winget install AgileBits.1Password.CLI`)
   - Tailscale (with Aperture enabled)

2. **Configure secrets**
   - Create a `.env` file in `deprecated-litellm/docker` with the 1Password references.

3. **Start the stack**
   ```powershell
   cd c:\Users\colin\repos\deskside_ai\ollama
   .\start.ps1
   ```

4. **Interact**
   - Web UI: Accessible via Tailscale Aperture
   - API endpoints: Accessible via Tailscale Aperture service discovery

## Smoke Tests

Running `start.ps1` will automatically perform smoke tests for the chat and autocomplete endpoints.

## License

MIT License – see `LICENSE`.
