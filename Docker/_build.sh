#!/usr/bin/env bash

set -e

cd /opt
python3 /opt/setup.py build_py
python3 /opt/setup.py install

rm -rf opt/*

