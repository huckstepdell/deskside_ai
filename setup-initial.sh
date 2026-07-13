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

echo ""
echo "Setting up NVIDIA Container Toolkit..."

# Add NVIDIA Container Toolkit GPG key
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

# Add NVIDIA Container Toolkit repository
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install and configure NVIDIA Container Toolkit
sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker

echo "✓ NVIDIA Container Toolkit installed and configured"

echo ""
echo "Setting up DGX-like development environment..."

# Install development tools
sudo apt install -y tmux htop tree ripgrep fd-find

# Install Python tools in user's default venv
echo "Installing JupyterLab and uv in ~/venvs/default..."
~/venvs/default/bin/pip install --upgrade pip
~/venvs/default/bin/pip install jupyterlab uv

# Add local user binaries to PATH if not already present
if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    echo "✓ Added ~/.local/bin to PATH in ~/.bashrc"
else
    echo "  ~/.local/bin already in PATH"
fi

echo "✓ DGX-like development environment configured"

echo ""
echo "Setting up JupyterLab systemd service..."

# Install the JupyterLab service file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo cp "${SCRIPT_DIR}/jupyterlab.service" /etc/systemd/system/jupyterlab@.service
sudo systemctl daemon-reload

echo "✓ JupyterLab systemd service installed"
echo ""
echo "To enable and start JupyterLab for your user, run:"
echo "  sudo systemctl enable --now jupyterlab@$USER"
echo "  sudo systemctl status jupyterlab@$USER --no-pager"
echo ""
echo "JupyterLab will be available at: http://localhost:8888"
echo "Get the token with: sudo journalctl -u jupyterlab@$USER | grep token"
echo ""
echo "Run 'source ~/.bashrc' to update your PATH in the current session"
