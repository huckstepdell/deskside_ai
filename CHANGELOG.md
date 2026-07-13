# Changelog

## 2026-07-13 - Initial Structure

### Added
- **Data Structure**: Implemented four-part directory layout
  - `~/.config/deskside_ai` - Configuration and secrets
  - `~/lab/deskside_ai` - Active runtime data (models, datasets, work, notebooks, volumes, exports)
  - `~/.cache/deskside_ai` - Disposable cache
  - Git repository for reproducible logic

- **Setup Scripts**:
  - `setup-initial.sh` - System packages, directory structure, config templates, and systemd
  - `setup-docker.sh` - Docker Engine, containerd, and Docker Compose

- **Configuration Templates**:
  - `config/.env.example` - API keys template (OpenAI, Anthropic, Hugging Face)
  - `config/paths.env.example` - Path customization template
  - `config/.gitignore` - Protects actual secrets from being committed

- **Documentation**:
  - `README.md` - Quick start and setup instructions
  - `DATA_STRUCTURE.md` - Comprehensive guide with workflows and examples
  - Quick reference section with common commands

### Design Decisions
- **WSL-local storage**: All active data stays in Linux filesystem for better Docker I/O performance
- **No Windows mounts**: Removed all `/mnt/d` references, everything is WSL-native
- **Separation of concerns**: Logic (git) vs secrets (config) vs data (lab) vs cache
- **Security first**: Config templates with actual secrets outside git
- **Automatic setup**: Single script creates entire structure and copies templates

### Directory Structure
```
~/repos/deskside_ai/          # This git repo
├── config/                   # Config templates
│   ├── .env.example
│   ├── paths.env.example
│   └── .gitignore
├── setup-initial.sh
├── setup-docker.sh
├── README.md
├── DATA_STRUCTURE.md
└── CHANGELOG.md

~/.config/deskside_ai/        # Private config (not in git)
├── .env                      # Actual API keys
└── paths.env                 # Path overrides

~/lab/deskside_ai/            # Active runtime data (not in git)
├── models/
├── datasets/
├── work/
├── notebooks/
├── volumes/
└── exports/

~/.cache/deskside_ai/         # Disposable cache (not in git)
```

## Future Enhancements
- Docker Compose files for common AI services
- Example notebooks and templates
- Python helper library for loading config
- Automated backup scripts
- Model management utilities
