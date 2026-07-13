#!/usr/bin/env bash

set -euo pipefail

echo "Installing Docker for Ubuntu WSL..."

# Add Docker's official GPG key
echo "Adding Docker repository..."
sudo install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
fi

# Add Docker repository to apt sources
UBUNTU_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Update apt package index with Docker repository
sudo apt-get update

# Install Docker packages
echo "Installing Docker Engine, containerd, and Docker Compose..."
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add user to docker group
echo "Adding $USER to docker group..."
sudo usermod -aG docker "$USER"

# Start Docker service
echo "Starting Docker service..."
sudo systemctl start docker
sudo systemctl enable docker

echo ""
echo "Docker installation complete!"
echo ""
echo "IMPORTANT: You must log out and log back in (or restart WSL) for group membership to take effect."
echo "After restarting, verify Docker with: docker run hello-world"
