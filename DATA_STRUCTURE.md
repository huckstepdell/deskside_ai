# Data Structure Guide

## Table of Contents
- [Overview](#overview)
- [Directory Layout](#directory-layout)
- [Getting Started](#getting-started)
- [Best Practices](#best-practices)
- [Working with the Structure](#working-with-the-structure)
- [Why This Structure?](#why-this-structure)

## Overview

This project uses a four-part split to separate concerns between version control, secrets, active work, and disposable data.

## Directory Layout

### 1. Git Repository: `~/repos/deskside_ai`

**Purpose:** Reproducible logic and configuration templates

**Contents:**
- Setup scripts (`setup-initial.sh`, `setup-docker.sh`)
- Docker Compose files
- Documentation
- Configuration templates (`config/.env.example`, `config/paths.env.example`)
- Application code and notebooks (templates)

**Versioned:** Yes, fully tracked in git

### 2. User Configuration: `~/.config/deskside_ai`

**Purpose:** Private secrets and machine-specific settings

**Contents:**
- `.env` - API keys, tokens, credentials
- `paths.env` - Custom path overrides
- Other private configuration files

**Versioned:** No, explicitly ignored by git

**Security:** Never commit these files. Copy from `.example` templates and customize.

### 3. Active Runtime Data: `~/lab/deskside_ai`

**Purpose:** Active working data on fast Linux filesystem

**Subdirectories:**
- `models/` - AI models in regular use (LLMs, embedding models, etc.)
- `datasets/` - Active datasets being processed or analyzed
- `work/` - General working directory for experiments
- `notebooks/` - Jupyter notebooks (active work)
- `volumes/` - Docker volume data (databases, persistent containers)
- `exports/` - Generated results, reports, and outputs

**Versioned:** No, but may contain notebooks/code you selectively commit

**Performance:** Kept on Linux filesystem (ext4) for best I/O performance with Docker

### 4. Disposable Cache: `~/.cache/deskside_ai`

**Purpose:** Temporary files that can be deleted anytime

**Contents:**
- Downloaded model files (before moving to `models/`)
- Build artifacts
- Package caches
- Temporary processing files

**Versioned:** No

**Maintenance:** Can be safely deleted to free space

## Getting Started

### Automatic Setup

The `setup-initial.sh` script creates the entire structure for you:

```bash
./setup-initial.sh
```

This will:
1. Create all three directory structures (`~/.config`, `~/lab`, `~/.cache`)
2. Copy configuration templates from `config/` to `~/.config/deskside_ai/`
3. Set appropriate permissions
4. Configure systemd (if on WSL)

### Manual Verification

Check that everything was created:

```bash
# Verify directories exist
ls -la ~/.config/deskside_ai
ls -la ~/lab/deskside_ai
ls -la ~/.cache/deskside_ai

# Check config files were copied
cat ~/.config/deskside_ai/.env
cat ~/.config/deskside_ai/paths.env
```

### First-Time Configuration

1. **Edit your API keys:**
   ```bash
   nano ~/.config/deskside_ai/.env
   ```
   Add your actual API keys for OpenAI, Anthropic, Hugging Face, etc.

2. **(Optional) Customize paths:**
   ```bash
   nano ~/.config/deskside_ai/paths.env
   ```
   Only needed if you want non-default locations.

3. **Never commit these files:**
   - The actual `.env` and `paths.env` stay in `~/.config/`
   - Only `.example` templates are in git

## Best Practices

### What Goes in Git

✅ **Include:**
- Scripts and automation
- Documentation
- Configuration templates (`.example` files)
- Docker/Compose definitions
- Sample notebooks (sanitized, no secrets)

❌ **Never include:**
- API keys or tokens
- Actual `.env` files (without `.example` suffix)
- Model files or large binaries
- Personal data or PII
- Local path customizations

## Working with the Structure

### Typical Workflows

**Starting a new project:**
```bash
cd ~/lab/deskside_ai/work
mkdir my-new-project
cd my-new-project
# Your work here has fast I/O and can be bind-mounted to containers
```

**Downloading a model:**
```bash
# Download to cache first
cd ~/.cache/deskside_ai
wget https://example.com/model.bin

# Move to active models when ready
mv model.bin ~/lab/deskside_ai/models/
```

**Creating a notebook:**
```bash
cd ~/lab/deskside_ai/notebooks
jupyter notebook
# Your notebooks are in a dedicated location, easy to organize
```

**Exporting results:**
```bash
# Scripts can write to exports directory
python my_script.py --output ~/lab/deskside_ai/exports/results.csv
```

### Docker Volume Mounts

When using Docker Compose, reference paths via environment variables:

```yaml
# docker-compose.yml
services:
  myservice:
    volumes:
      - ${MODELS_DIR:-~/lab/deskside_ai/models}:/models:ro
      - ${WORK_DIR:-~/lab/deskside_ai/work}:/workspace:rw
      - ${VOLUMES_DIR:-~/lab/deskside_ai/volumes}/myservice:/data
```

Source the paths file in your scripts:

```bash
#!/usr/bin/env bash
source ~/.config/deskside_ai/paths.env
docker compose up
```

### Loading Configuration in Python

```python
import os
from pathlib import Path
from dotenv import load_dotenv

# Load API keys
env_path = Path.home() / ".config" / "deskside_ai" / ".env"
load_dotenv(env_path)

# Load paths
paths_env = Path.home() / ".config" / "deskside_ai" / "paths.env"
load_dotenv(paths_env)

# Use them
openai_key = os.getenv("OPENAI_API_KEY")
models_dir = os.getenv("MODELS_DIR", str(Path.home() / "lab" / "deskside_ai" / "models"))
```

### Organizing Your Data

**Models directory structure:**
```
~/lab/deskside_ai/models/
├── llm/
│   ├── llama-3.1-8b/
│   └── mistral-7b/
├── embedding/
│   ├── bge-large/
│   └── e5-mistral/
└── vision/
    └── clip/
```

**Work directory structure:**
```
~/lab/deskside_ai/work/
├── projects/
│   ├── project-alpha/
│   └── project-beta/
├── experiments/
│   └── exp-001-rag-tuning/
└── scratch/
    └── quick-test.py
```

**Notebooks organization:**
```
~/lab/deskside_ai/notebooks/
├── exploratory/
│   └── data-analysis-2026-07.ipynb
├── production/
│   └── model-training.ipynb
└── tutorials/
    └── getting-started.ipynb
```

### Maintenance Tasks

**Clean up cache:**
```bash
# Safe to delete anytime
rm -rf ~/.cache/deskside_ai/*
# Directories will be recreated as needed
```

**Archive old work:**
```bash
# Create archive directory
mkdir -p ~/archives/deskside_ai/2026-q2

# Move completed projects
mv ~/lab/deskside_ai/work/old-project ~/archives/deskside_ai/2026-q2/
mv ~/lab/deskside_ai/exports/old-results-* ~/archives/deskside_ai/2026-q2/
```

**Check disk usage:**
```bash
# See what's using space
du -sh ~/lab/deskside_ai/*
du -sh ~/.cache/deskside_ai

# Models are typically the largest
du -sh ~/lab/deskside_ai/models/*
```

**Update configuration templates:**
```bash
# After updating your .env, create a sanitized template for git
cd ~/repos/deskside_ai
cp ~/.config/deskside_ai/.env config/.env.example
# Edit to replace actual keys with placeholders
nano config/.env.example
git add config/.env.example
git commit -m "Update .env template with new services"
```

### Backup Strategy

**What to backup:**
- ✅ `~/lab/deskside_ai/work/` - Your active projects
- ✅ `~/lab/deskside_ai/notebooks/` - Your notebooks
- ✅ `~/lab/deskside_ai/exports/` - Important results
- ✅ `~/.config/deskside_ai/` - Your configuration (but securely!)
- ❌ `~/lab/deskside_ai/models/` - Can be re-downloaded
- ❌ `~/.cache/deskside_ai/` - Temporary, regeneratable

**Example backup script:**
```bash
#!/usr/bin/env bash
BACKUP_DIR=~/backups/deskside_ai-$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"

# Backup active work
rsync -av ~/lab/deskside_ai/work/ "$BACKUP_DIR/work/"
rsync -av ~/lab/deskside_ai/notebooks/ "$BACKUP_DIR/notebooks/"
rsync -av ~/lab/deskside_ai/exports/ "$BACKUP_DIR/exports/"

# Backup config (encrypted!)
tar czf - ~/.config/deskside_ai | gpg -e -r your@email.com > "$BACKUP_DIR/config.tar.gz.gpg"

echo "Backup complete: $BACKUP_DIR"
```

## Troubleshooting

**Config files not found:**
```bash
# Re-run setup to copy templates
./setup-initial.sh
```

**Docker can't access volumes:**
```bash
# Check paths exist
ls -la ~/lab/deskside_ai/

# Check permissions
ls -ld ~/lab/deskside_ai/*

# Source paths in docker-compose
source ~/.config/deskside_ai/paths.env && docker compose up
```

**Out of disk space:**
```bash
# Check usage
df -h ~
du -sh ~/lab/deskside_ai/* ~/.cache/deskside_ai

# Clean cache
rm -rf ~/.cache/deskside_ai/*

# Move old models to archive
mkdir -p ~/archives/deskside_ai/models
mv ~/lab/deskside_ai/models/old-model ~/archives/deskside_ai/models/
```

---

For quick setup instructions, see [README.md](README.md).
For configuration templates, check the [config/](config/) directory.

### Configuration Pattern

```
# In git repo (template):
~/repos/deskside_ai/config/.env.example

# In user config (actual secrets):
~/.config/deskside_ai/.env
```

Always maintain parallel `.example` templates so others can recreate the structure without your secrets.

### Data Organization

1. **Active work** → `~/lab/deskside_ai`
   - Currently using
   - Needs fast access
   - Bind-mounted into containers

2. **Archives** → Separate archive directory (optional)
   - Old models no longer in use
   - Completed project exports
   - Backup copies
   - Consider a separate `~/archives/deskside_ai` if needed

3. **Temporary** → `~/.cache/deskside_ai`
   - Downloads in progress
   - Build outputs
   - Can be regenerated

### Why This Structure?

1. **Separation of Concerns**
   - Logic (git) vs secrets (config) vs data (lab)
   - Makes repo sharable without exposing credentials

2. **Performance**
   - Active work on native Linux filesystem = faster Docker I/O
   - Avoids cross-filesystem performance penalties

3. **Portability**
   - Anyone can clone the repo and run the setup scripts
   - Custom paths are in `~/.config`, not hardcoded

4. **Security**
   - Secrets never accidentally committed
   - `.gitignore` prevents common mistakes
   - Config directory has its own `.gitignore`
