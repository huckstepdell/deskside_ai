#!/usr/bin/env bash

set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL_LIST_DIR="${SCRIPT_DIR}/../model_lists"

echo "Select the target device:"
echo "  1) Blackwell 4000"
echo "  2) GB10"

while true; do
	printf "Device [1-2]: "
	read -r device

	case "${device}" in
		1) model_list="blackwell_4000.txt" ;;
		2) model_list="gb10.txt" ;;
		*)
			echo "Please enter 1 or 2."
			continue
			;;
	esac

	break
done

export PULL_MODELS="$(<"${MODEL_LIST_DIR}/${model_list}")"
echo "Using model list: ${model_list}"
mkdir -p "/container_data/ollama/ollama-data"
mkdir -p "/container_data/open-webui/open-webui-data"
docker compose pull
docker compose up -d --remove-orphans
