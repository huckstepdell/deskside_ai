# DeskSide AI

DeskSide AI is a modular AI stack that combines **Foundry Local**, **Tailscale Aperture**, **Ollama**, and **Open WebUI** to provide a flexible, local-first AI development environment.

## Repository Structure

```
CHANGELOG.md
LICENSE
foundry-local_open-webui/
    docker-compose.yml
    install.ps1
    start.ps1
    stop.ps1
deprecated-litellm/
    docker/
        config.yaml
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
        docker-compose.webui.yml
        docker-compose.yml
        start.ps1
        start.sh
        stop.ps1
        stop.sh
model_lists/
    blackwell_4000.txt
    gb10.txt
    intel_npu.txt
```

## Components

- **Foundry Local** – Local NPU model hosting (qwen2.5-coder-1.5b). Started via `foundry-local_open-webui/start.ps1`.
- **Tailscale Aperture** – Routing layer that exposes multiple model tiers (Blackwell, GB10, planning, autocomplete) using service discovery and secure tunnels.
- **Ollama** – Dockerized LLM runtime for GB10 and Blackwell. **Must be running on the GB10 and Blackwell workstations** to serve the models.
- **Open WebUI** – Web UI for interacting with the models. Started via `foundry-local_open-webui/start.ps1`.
- **npmplus** – Node‑based utilities (not detailed here).
- **ollama** – Additional Docker Compose files for different model setups.
- **model_lists** – Text files listing available models for each tier.

## Getting Started

1. **Install prerequisites**
   - Docker Desktop
   - 1Password CLI (`winget install AgileBits.1Password.CLI`)
   - Foundry Local (via `foundry-local_open-webui/install.ps1`)

2. **Configure secrets**
   - Create a `.env` file in `foundry-local_open-webui` and `deprecated-litellm/docker` with the 1Password references.

4. **Start the stack**
   ```powershell
   cd c:\Users\colin\repos\deskside_ai\foundry-local_open-webui
   .\start.ps1
   ```

5. **Interact**
   - Open WebUI: `http://localhost:8080`
   - API endpoints: Accessible via Tailscale Aperture service discovery

## Smoke Tests

Running `start.ps1` will automatically perform smoke tests for the chat and autocomplete endpoints.

## License

MIT License – see `LICENSE`.
