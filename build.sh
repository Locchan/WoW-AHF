#!/bin/bash

set -e

docker build --no-cache --cpu-period="100000" --cpu-quota="50000" --memory 384m -t locchan:wow_ahf -f Docker/Dockerfile .
docker builder prune -f -a