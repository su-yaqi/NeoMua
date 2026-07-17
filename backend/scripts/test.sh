#!/usr/bin/env bash

set -e
set -x

if [ "$#" -eq 0 ]; then
    set -- tests/
fi

coverage run -m pytest "$@"
coverage report
coverage html --title "coverage"
