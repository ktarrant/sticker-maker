#!/bin/bash
# Reproducible native Intel/Apple silicon environment. Run from any directory.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
with_ai=1
if [[ "${1:-}" == "--without-ai" && $# -eq 1 ]]; then
    with_ai=0
elif [[ $# -ne 0 ]]; then
    printf 'Usage: %s [--without-ai]\n' "$0" >&2
    exit 2
fi
if [[ "$(uname -s)" != Darwin ]]; then
    printf 'This installer targets macOS. See pyproject.toml for other environments.\n' >&2
    exit 1
fi
processor="$(uname -m)"
os_version="$(sw_vers -productVersion)"
minimum=14
if [[ "$processor" == x86_64 ]]; then
    minimum=13
elif [[ "$processor" != arm64 ]]; then
    printf 'Unsupported processor: %s\n' "$processor" >&2
    exit 1
fi
if (( ${os_version%%.*} < minimum )); then
    printf 'This lockfile requires macOS %s+ on %s; found %s.\n' "$minimum" "$processor" "$os_version" >&2
    exit 1
fi
if ! command -v uv >/dev/null; then
    printf 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
    exit 1
fi
python_processor="$processor"
if [[ "$processor" == arm64 ]]; then python_processor=aarch64; fi
python_request="cpython-3.12-macos-${python_processor}-none"
export SAM2_BUILD_CUDA=0
if (( with_ai )); then
    # Intel's legacy Torch cannot satisfy SAM's isolated build floor. Install
    # locked runtime/build tools first, then build the pinned pure-Python SAM.
    uv sync --locked --python "$python_request" --extra gui --extra sam --no-install-package sam-2
    uv sync --locked --python "$python_request" --extra gui --extra sam --no-build-isolation-package sam-2
    uv run --no-sync sticker-maker models fetch --model small
    uv run --no-sync sticker-maker doctor --require-ai --output output/setup/doctor.json
else
    uv sync --locked --python "$python_request" --extra gui
    uv run --no-sync sticker-maker doctor --output output/setup/doctor.json
fi
uv run --no-sync python scripts/create-example.py
printf '\nReady. Start review with: ./scripts/sticker-maker review output/examples/labels.png\n'
