# deskside_ai
Demo Repo for Deskside AI Agent

## Quick Start

For detailed information about the data structure philosophy and best practices, see [DATA_STRUCTURE.md](DATA_STRUCTURE.md).

## Setup Instructions

This repository contains setup scripts for configuring Ubuntu WSL for AI development.

### Initial Setup

Run the initial setup script to install base packages, create the AI lab directory structure, set up configuration files, and configure systemd:

```bash
chmod +x setup-initial.sh
./setup-initial.sh
```

This will:
- Install essential packages
- Create directory structure (`~/.config/deskside_ai`, `~/lab/deskside_ai`, `~/.cache/deskside_ai`)
- Copy configuration templates to `~/.config/deskside_ai/`
- Configure systemd for WSL
- Install NVIDIA Container Toolkit for GPU support in Docker
- Install DGX-like development tools (tmux, htop, tree, ripgrep, fd-find)
- Set up JupyterLab and uv in your default Python virtual environment
- Configure JupyterLab systemd service

**After running setup-initial.sh**, you must restart WSL for systemd to take effect:
1. Exit WSL completely
2. In Windows PowerShell, run: `wsl --shutdown`
3. Reopen WSL

### Docker Setup

After restarting WSL, run the Docker setup script:

```bash
chmod +x setup-docker.sh
./setup-docker.sh
```

**After running setup-docker.sh**, you must log out and log back in (or restart WSL again) for Docker group membership to take effect.

Verify Docker installation:
```bash
docker run hello-world
```

### JupyterLab Service

A systemd service is configured to run JupyterLab persistently. To enable and start it:

```bash
sudo systemctl enable --now jupyterlab@$USER
```

Check the service status:
```bash
sudo systemctl status jupyterlab@$USER --no-pager
```

Get the JupyterLab access token:
```bash
sudo journalctl -u jupyterlab@$USER | grep token
```

JupyterLab will be available at: http://localhost:8888

To stop the service:
```bash
sudo systemctl stop jupyterlab@$USER
```

To disable the service from starting on boot:
```bash
sudo systemctl disable jupyterlab@$USER
```

### GPU Support

The setup includes NVIDIA Container Toolkit for GPU acceleration in Docker containers. To verify GPU access in Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

To use GPU in docker compose, add to your service definition:
```yaml
services:
  my-service:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### Configuration

The setup script automatically copies configuration templates to `~/.config/deskside_ai/`.

**Edit your API keys:**
```bash
nano ~/.config/deskside_ai/.env
# or
code ~/.config/deskside_ai/.env
```

Add your actual API keys for the services you plan to use (OpenAI, Anthropic, Hugging Face, etc.).

**(Optional) Customize paths:**
```bash
nano ~/.config/deskside_ai/paths.env
```

**Important:** Never commit the actual `.env` or `paths.env` files to git. Only the `.example` templates in `config/` are versioned.

### Development Tools

The setup installs a DGX-like development environment with:

- **tmux** - Terminal multiplexer for managing multiple sessions
- **htop** - Interactive process viewer
- **tree** - Directory structure visualization
- **ripgrep** (rg) - Fast recursive search tool
- **fd-find** (fd) - Fast alternative to find
- **JupyterLab** - Interactive development environment for notebooks
- **uv** - Fast Python package installer

These tools are installed in your default Python virtual environment at `~/venvs/default`.

## Directory Structure

The setup creates a four-part split for data organization:

### Git Repository (this repo: `~/repos/deskside_ai`)
- Setup scripts
- Docker/Compose files
- Documentation
- Configuration templates (`.env.example`, `paths.env.example`)

### User Configuration (`~/.config/deskside_ai`)
- Private `.env` files with API keys and tokens
- Machine-specific path overrides (`paths.env`)
- **Not stored in git**

### Active Runtime Data (`~/lab/deskside_ai`)
- `models/` - AI models in regular use
- `datasets/` - Active datasets
- `work/` - General working directory
- `notebooks/` - Jupyter notebooks
- `volumes/` - Docker volume data
- `exports/` - Output and results

### Disposable Cache (`~/.cache/deskside_ai`)
- Temporary files
- Download cache
- Build artifacts

This structure keeps the git repo reproducible without storing secrets, while keeping active work on the faster Linux filesystem.

## Quick Reference

### Common Paths
```bash
# Configuration (secrets, API keys)
~/.config/deskside_ai/.env
~/.config/deskside_ai/paths.env

# Active work directories
~/lab/deskside_ai/models/          # AI models
~/lab/deskside_ai/datasets/        # Datasets
~/lab/deskside_ai/work/            # Your projects
~/lab/deskside_ai/notebooks/       # Jupyter notebooks
~/lab/deskside_ai/volumes/         # Docker volumes
~/lab/deskside_ai/exports/         # Results and outputs

# Cache (safe to delete)
~/.cache/deskside_ai/
```

### Common Commands
```bash
# Edit API keys
nano ~/.config/deskside_ai/.env

# Start a new project
cd ~/lab/deskside_ai/work && mkdir my-project

# Check JupyterLab service status
sudo systemctl status jupyterlab@$USER --no-pager

# Search files with ripgrep
rg "pattern" ~/lab/deskside_ai/work

# Find files with fd
fd "filename" ~/lab/deskside_ai

# Monitor system resources
htop

# Check disk usage
du -sh ~/lab/deskside_ai/*

# Clean cache
rm -rf ~/.cache/deskside_ai/*
```

### Using Paths in Scripts
```bash
# Load environment in bash
source ~/.config/deskside_ai/paths.env
echo $MODELS_DIR

# Use in Docker Compose
source ~/.config/deskside_ai/paths.env
docker compose up
```

For detailed workflows and examples, see [DATA_STRUCTURE.md](DATA_STRUCTURE.md).
