#!/usr/bin/env bash
# Wrapper script to run clodxy via uv

SCRIPT_DIR="$(cd "$(dirname "$(readlink "${BASH_SOURCE[0]}")")" && pwd)"
uv --directory "$SCRIPT_DIR" run clodxy "$@"
