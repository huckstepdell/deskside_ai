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

# Launch Jupyter
cd ~/lab/deskside_ai/notebooks && jupyter notebook

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
