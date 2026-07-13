do#!/usr/bin/env bash

set -euo pipefail

sudo apt-get update && sudo apt-get upgrade -y
sudo apt install -y curl wget git unzip zip ca-certificates gnupg lsb-release software-properties-common build-essential python3 python3-pip python3-venv jq net-tools

echo "Creating deskside_ai directory structure..."
mkdir -p ~/.config/deskside_ai
mkdir -p ~/lab/deskside_ai/{models,datasets,work,notebooks,volumes,exports}
mkdir -p ~/.cache/deskside_ai

echo "Directory structure created:"
echo "  ~/.config/deskside_ai     - Configuration and secrets"
echo "  ~/lab/deskside_ai         - Active runtime data"
echo "  ~/.cache/deskside_ai      - Disposable caches"

echo ""
echo "Setting up configuration files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOME}/.config/deskside_ai"
REPO_CONFIG_DIR="${SCRIPT_DIR}/config"

# Copy config templates if they don't exist
if [ -f "${CONFIG_DIR}/.env" ]; then
    echo "  ~/.config/deskside_ai/.env already exists, skipping..."
else
    cp "${REPO_CONFIG_DIR}/.env.example" "${CONFIG_DIR}/.env"
    echo "  ✓ Copied .env.example to ~/.config/deskside_ai/.env"
fi

if [ -f "${CONFIG_DIR}/paths.env" ]; then
    echo "  ~/.config/deskside_ai/paths.env already exists, skipping..."
else
    cp "${REPO_CONFIG_DIR}/paths.env.example" "${CONFIG_DIR}/paths.env"
    echo "  ✓ Copied paths.env.example to ~/.config/deskside_ai/paths.env"
fi

echo ""
echo "IMPORTANT: Edit ~/.config/deskside_ai/.env with your API keys before using AI services."
echo ""

if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
    echo "Configuring systemd for WSL..."
    sudo touch /etc/wsl.conf

    if sudo grep -Eq '^\s*\[boot\]\s*$' /etc/wsl.conf; then
        if sudo grep -Eq '^\s*systemd\s*=' /etc/wsl.conf; then
            sudo sed -Ei 's/^\s*systemd\s*=.*/systemd=true/' /etc/wsl.conf
        else
            tmp_file=$(mktemp)
            sudo awk '
                BEGIN { in_boot=0; inserted=0 }
                /^\s*\[boot\]\s*$/ {
                    print
                    in_boot=1
                    next
                }
                in_boot && /^\s*\[/ && !inserted {
                    print "systemd=true"
                    inserted=1
                    in_boot=0
                }
                { print }
                END {
                    if (in_boot && !inserted) {
                        print "systemd=true"
                    }
                }
            ' /etc/wsl.conf > "$tmp_file"
            sudo mv "$tmp_file" /etc/wsl.conf
        fi
    else
        printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
    fi

    echo "systemd is configured. Run 'wsl --shutdown' in Windows PowerShell, then reopen WSL."
fi
