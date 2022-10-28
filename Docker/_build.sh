#!/usr/bin/env bash

set -e

yum -y install gcc-c++ make tar

mkdir -p /opt/deps/python-blizzardapi
cd /opt/deps/python-blizzardapi

wget https://github.com/trevorphillipscoding/python-blizzardapi/tarball/master -O python-blizzardapi.tar.gz
tar -xvzf python-blizzardapi.tar.gz --strip-components=1
python3 ./setup.py build_py
python3 ./setup.py install

rm -rf /opt/deps

cd /opt
python3 /opt/setup.py build_py
python3 /opt/setup.py install

yum -y remove gcc-c++ make tar

rm -rf opt/*

