#!/bin/bash
echo "Scanning all files in 2pdf/"
source ../.venv/bin/activate && python ../run.py --images ./ -pdf -i
