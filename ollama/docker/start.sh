#!/usr/bin/env bash

set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL_LIST_DIR="${SCRIPT_DIR}/../model_lists"
cd -- "${SCRIPT_DIR}"

echo "Select the target device:"
echo "  1) Blackwell 4000"
echo "  2) GB10"

while true; do
	printf "Device [1-2]: "
	read -r device

	case "${device}" in
		1)
			model_list="blackwell_4000.txt"
			compose_args=(-f docker-compose.yml -f docker-compose.blackwell.yml)
			;;
		2)
			model_list="gb10.txt"
			compose_args=(-f docker-compose.yml -f docker-compose.gb10.yml)
			;;
		*)
			echo "Please enter 1 or 2."
			continue
			;;
	esac

	break
done

export PULL_MODELS="$(<"${MODEL_LIST_DIR}/${model_list}")"
export CONTAINER_DATA_DIR="/container_data"
echo "Using model list: ${model_list}"
mkdir -p "/container_data/ollama/ollama-data"
if [[ "${device}" == 2 ]]; then
	mkdir -p "/container_data/open-webui/open-webui-data"
fi
docker compose "${compose_args[@]}" pull
docker compose "${compose_args[@]}" up -d --remove-orphans
