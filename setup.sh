sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y build-essential git curl wget unzip ca-certificates gnupg lsb-release python3 python3-pip python3-venv

if [ ! -d "/mnt/d/ai-lab" ]; then
    echo "Creating /mnt/d/ai-lab directory structure..."
    sudo mkdir -p /mnt/d/ai-lab/{control,models,workspace,indexes,logs,data}
else
    echo "/mnt/d/ai-lab already exists, skipping creation..."
fi

sudo chown -R $USER:$USER /mnt/d/ai-lab
chmod -R u+rwX /mnt/d/ai-lab
