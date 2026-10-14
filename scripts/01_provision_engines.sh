#!/usr/bin/env bash
# Idempotent native engine provisioning for Apple Silicon (Metal API).
# Installs system tools, builds whisper.cpp/llama.cpp with Metal, and
# downloads the required models. Safe to re-run: skips steps whose target
# artifact already exists and whose checksum matches the pinned value.
#
# ADR 0038 removed the local text route and ADR 0046 reinstates it as the
# LlamaCppAdapter of the LLM gateway. llama.cpp is built for llama-completion
# and llama-tokenize (gateway adapter) and llama-mtmd-cli (vision captioning;
# its Qwen3-VL-8B model pair is attested in the actor inventory and was
# provisioned outside this script). Whisper's model is always downloaded; the
# example text model is downloaded only with --with-example-llm-model.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINES_DIR="${ROOT_DIR}/engines"
MODELS_DIR="${ENGINES_DIR}/models"
WHISPER_DIR="${ENGINES_DIR}/whisper.cpp"
LLAMA_DIR="${ENGINES_DIR}/llama.cpp"

# Pinned for supply-chain reproducibility. Bump deliberately after review;
# never track a moving branch here.
WHISPER_REPO_COMMIT="080bbbe85230f624f0b52127f1ae1218247989f9"
LLAMA_REPO_COMMIT="91d2fc387529940230555abd297a8b5e99737d3f"

WHISPER_MODEL_NAME="large-v3-turbo"
WHISPER_MODEL_FILE="${WHISPER_DIR}/models/ggml-${WHISPER_MODEL_NAME}.bin"
WHISPER_MODEL_SHA256="1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"

# Example text model for the LLM gateway's local adapter (opt-in download).
# License Apache-2.0 and shard SHA-256 values verified against the Hugging Face
# model card and LFS file listing on 2026-10-14. The download is pinned to an
# immutable repository revision, never to a moving branch. The upstream repo
# distributes Q4_K_M as 3 GGUF shards (filename:sha256), not a single file.
EXAMPLE_LLM_MODEL_REPOSITORY="Qwen/Qwen2.5-14B-Instruct-GGUF"
EXAMPLE_LLM_MODEL_REVISION="b466e1f8c07172155743e8e1307507d8a4f91fbd"
EXAMPLE_LLM_MODEL_DIR="${MODELS_DIR}/qwen2.5-14b-instruct-q4_k_m"
EXAMPLE_LLM_MODEL_SHARDS=(
    "qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf:a09ea5e7b1eafb1b30b241726c3cc3c905c96f14ad41e246ffa5f44e53904f68"
    "qwen2.5-14b-instruct-q4_k_m-00002-of-00003.gguf:21b9457d079680d284e90ef69607c4b2d8ef64a09d4729cb7b5e1357bdba41ae"
    "qwen2.5-14b-instruct-q4_k_m-00003-of-00003.gguf:c8d37006760a387a35216e070e6664d7da927f10be8eb870fef2e3d4833d9976"
)

install_brew_package() {
    local formula="$1"
    if ! command -v "${formula}" >/dev/null 2>&1; then
        echo "==> Installing ${formula} via Homebrew"
        brew install "${formula}"
    else
        echo "==> ${formula} already installed, skipping"
    fi
}

install_system_dependencies() {
    install_brew_package ffmpeg
    install_brew_package typst
    install_brew_package huggingface-cli
}

sha256_of() {
    shasum -a 256 "$1" | awk '{print $1}'
}

# Hard-fails (and deletes the file) on mismatch. Use only right after a
# download completes, where a mismatch means a corrupted/tampered transfer.
verify_sha256() {
    local file_path="$1"
    local expected_sha256="$2"
    local actual_sha256
    actual_sha256="$(sha256_of "${file_path}")"
    if [ "${actual_sha256}" != "${expected_sha256}" ]; then
        echo "==> FATAL: checksum mismatch for ${file_path}" >&2
        echo "    expected: ${expected_sha256}" >&2
        echo "    actual:   ${actual_sha256}" >&2
        rm -f "${file_path}"
        exit 1
    fi
}

# Soft check for deciding whether a pre-existing local file can be trusted
# as-is. False (not fatal) on a missing file or a mismatch, so callers can
# decide to re-download instead of aborting the whole script.
sha256_matches() {
    local file_path="$1"
    local expected_sha256="$2"
    [ -f "${file_path}" ] && [ "$(sha256_of "${file_path}")" = "${expected_sha256}" ]
}

clone_pinned_commit() {
    local repo_url="$1"
    local target_dir="$2"
    local commit_sha="$3"
    if [ ! -d "${target_dir}/.git" ]; then
        echo "==> Cloning ${repo_url} into ${target_dir}"
        git clone "${repo_url}" "${target_dir}"
    else
        echo "==> ${target_dir} already present, skipping clone"
    fi
    if [ "$(git -C "${target_dir}" rev-parse HEAD)" = "${commit_sha}" ]; then
        echo "==> $(basename "${target_dir}") already at pinned commit ${commit_sha}, skipping checkout"
        return
    fi
    echo "==> Pinning $(basename "${target_dir}") to ${commit_sha}"
    git -C "${target_dir}" fetch --depth 1 origin "${commit_sha}"
    git -C "${target_dir}" checkout --quiet "${commit_sha}"
}

build_if_stale() {
    local engine_dir="$1"
    local binary_path="$2"
    local pinned_commit="$3"
    local built_commit_marker="${engine_dir}/build/.built-commit"

    if [ -x "${binary_path}" ] && [ "$(cat "${built_commit_marker}" 2>/dev/null || true)" = "${pinned_commit}" ]; then
        echo "==> $(basename "${binary_path}") already built at pinned commit ${pinned_commit}, skipping"
        return
    fi
    echo "==> Building $(basename "${engine_dir}") with Metal (GGML_METAL=ON)"
    (cd "${engine_dir}" && cmake -B build -DGGML_METAL=ON && cmake --build build --config Release)
    # Stamped only after a successful build, so a re-run after a failed
    # build correctly retries instead of trusting a stale/missing marker.
    echo "${pinned_commit}" >"${built_commit_marker}"
}

build_whisper_cpp() {
    clone_pinned_commit "https://github.com/ggerganov/whisper.cpp.git" "${WHISPER_DIR}" "${WHISPER_REPO_COMMIT}"
    build_if_stale "${WHISPER_DIR}" "${WHISPER_DIR}/build/bin/whisper-cli" "${WHISPER_REPO_COMMIT}"
}

require_llama_binaries() {
    local binary_name
    for binary_name in llama-completion llama-tokenize llama-mtmd-cli; do
        if [ ! -x "${LLAMA_DIR}/build/bin/${binary_name}" ]; then
            echo "==> FATAL: missing ${binary_name} after the llama.cpp build" >&2
            exit 1
        fi
    done
}

build_llama_cpp() {
    clone_pinned_commit "https://github.com/ggerganov/llama.cpp.git" "${LLAMA_DIR}" "${LLAMA_REPO_COMMIT}"
    build_if_stale "${LLAMA_DIR}" "${LLAMA_DIR}/build/bin/llama-completion" "${LLAMA_REPO_COMMIT}"
    require_llama_binaries
}

download_example_llm_model_shard() {
    local shard_file="$1"
    local shard_sha256="$2"
    local shard_path="${EXAMPLE_LLM_MODEL_DIR}/${shard_file}"
    local shard_url="https://huggingface.co/${EXAMPLE_LLM_MODEL_REPOSITORY}/resolve/${EXAMPLE_LLM_MODEL_REVISION}/${shard_file}"

    if sha256_matches "${shard_path}" "${shard_sha256}"; then
        echo "==> ${shard_file} already present with matching checksum, skipping"
        return
    fi
    echo "==> Downloading ${shard_file}"
    curl --fail --location --proto '=https' --tlsv1.2 --output "${shard_path}.part" "${shard_url}"
    verify_sha256 "${shard_path}.part" "${shard_sha256}"
    mv "${shard_path}.part" "${shard_path}"
}

download_example_llm_model() {
    local shard_entry
    mkdir -p "${EXAMPLE_LLM_MODEL_DIR}"
    for shard_entry in "${EXAMPLE_LLM_MODEL_SHARDS[@]}"; do
        download_example_llm_model_shard "${shard_entry%%:*}" "${shard_entry##*:}"
    done
}

download_whisper_model() {
    if sha256_matches "${WHISPER_MODEL_FILE}" "${WHISPER_MODEL_SHA256}"; then
        echo "==> Whisper model ${WHISPER_MODEL_NAME} already present with matching checksum, skipping"
        return
    fi
    echo "==> Downloading whisper model ${WHISPER_MODEL_NAME}"
    (cd "${WHISPER_DIR}" && bash ./models/download-ggml-model.sh "${WHISPER_MODEL_NAME}")
    verify_sha256 "${WHISPER_MODEL_FILE}" "${WHISPER_MODEL_SHA256}"
}

print_usage() {
    cat <<'USAGE'
Usage: scripts/01_provision_engines.sh [--with-example-llm-model] [-h|--help]

Builds the native engines (whisper.cpp, llama.cpp) and downloads Whisper's
model. Idempotent: steps whose artifact is already in place are skipped.

Options:
  --with-example-llm-model  Also download the example text model
                            (Qwen2.5-14B-Instruct Q4_K_M, about 9 GB) from a
                            pinned revision with per-shard SHA-256 checks.
                            Needs network; otherwise this step is skipped.
  -h, --help                Show this help text and exit.
USAGE
}

parse_arguments() {
    local argument
    WITH_EXAMPLE_LLM_MODEL=0
    for argument in "$@"; do
        case "${argument}" in
            --with-example-llm-model)
                WITH_EXAMPLE_LLM_MODEL=1
                ;;
            -h | --help)
                print_usage
                exit 0
                ;;
            *)
                echo "==> Unknown argument: ${argument}" >&2
                print_usage >&2
                exit 2
                ;;
        esac
    done
}

main() {
    parse_arguments "$@"
    install_system_dependencies
    build_whisper_cpp
    build_llama_cpp
    download_whisper_model
    if [ "${WITH_EXAMPLE_LLM_MODEL}" = "1" ]; then
        download_example_llm_model
    else
        echo "==> Skipping the example LLM model (pass --with-example-llm-model to download it)"
    fi
    echo "==> Provisioning complete"
}

main "$@"
