#!/bin/bash

set -e

VAULT_PATH="kv/data/services/wow-ahf"

docker build --build-arg VAULT_PATH="$VAULT_PATH" --no-cache --cpu-period="100000" --cpu-quota="50000" --memory 384m -t locchan:wowahf -f Docker/Dockerfile .

docker builder prune -f -a