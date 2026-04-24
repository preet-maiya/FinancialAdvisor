#!/usr/bin/env bash
# run_jobs_loop.sh — Downloads missing models then evaluates all of them,
#                    2 job cycles each. Suppresses Telegram and restores the
#                    DB between models so every model sees identical input state.
#
# Usage:
#   ./run_jobs_loop.sh
#
# Configuration (edit the variables below):
#   PROJECT_DIR      — path to the FinancialAdvisor repo
#   OUTFILE          — where to dump all LLM responses
#   CYCLES_PER_MODEL — job cycles per model (default: 2)
#   MODEL_WARMUP     — seconds to wait after starting a model (default: 180)

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_DIR="${PROJECT_DIR:-.}"
MODELS_DIR="${PROJECT_DIR}/models"
OUTFILE="${OUTFILE:-${PROJECT_DIR}/llm_responses_$(date +%Y%m%d_%H%M%S).log}"
CYCLES_PER_MODEL="${CYCLES_PER_MODEL:-2}"
MODEL_WARMUP="${MODEL_WARMUP:-180}"  # 3 minutes
JOBS=(digest anomaly weekly monthly)

DB_PATH="${PROJECT_DIR}/data/finance.db"
DB_SNAPSHOT="${PROJECT_DIR}/data/finance.db.eval_snapshot"

HF_BASE="https://huggingface.co"

# ── Model registry ─────────────────────────────────────────────────────────────
# Format: "label|profile|service|model_file|hf_repo"
#   label       — human-readable name used in logs and stored as LLAMA_CPP_MODEL
#   profile     — docker compose profile (gemma4 or qwen3)
#   service     — docker compose service name
#   model_file  — GGUF filename inside models/
#   hf_repo     — HuggingFace repo path (org/repo) to download from
MODELS=(
    "gemma4-E2B|gemma4|llama-gemma4|gemma-4-E2B-it-Q4_K_M.gguf|unsloth/gemma-4-E2B-it-GGUF"
    "gemma4-E4B|gemma4|llama-gemma4|gemma-4-E4B-it-Q5_K_M.gguf|unsloth/gemma-4-E4B-it-GGUF"
    "qwen3-0.6B|qwen3|llama-qwen3|Qwen3-0.6B-Q4_K_M.gguf|unsloth/Qwen3-0.6B-GGUF"
    "qwen3-4B|qwen3|llama-qwen3|Qwen3-4B-Q4_K_M.gguf|unsloth/Qwen3-4B-GGUF"
    "qwen3-8B|qwen3|llama-qwen3|Qwen3-8B-Q4_K_M.gguf|unsloth/Qwen3-8B-GGUF"
)
# ──────────────────────────────────────────────────────────────────────────────

cd "$PROJECT_DIR" || { echo "ERROR: PROJECT_DIR not found: $PROJECT_DIR"; exit 1; }

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$OUTFILE"
}

# ── Download ───────────────────────────────────────────────────────────────────

download_models() {
    log "=========================================="
    log "MODEL DOWNLOAD CHECK"
    log "=========================================="

    local any_downloaded=0

    for entry in "${MODELS[@]}"; do
        IFS='|' read -r label profile service model_file hf_repo <<< "$entry"
        local dest="${MODELS_DIR}/${model_file}"

        if [ -f "$dest" ]; then
            log "  [present]  $model_file"
        else
            log "  [missing]  $model_file — downloading from $hf_repo ..."
            local url="${HF_BASE}/${hf_repo}/resolve/main/${model_file}"
            wget --progress=bar:force -O "$dest" "$url"
            if [ $? -ne 0 ]; then
                log "  ERROR: Failed to download $model_file. Remove the partial file and retry."
                rm -f "$dest"
                exit 1
            fi
            log "  [done]     $model_file"
            any_downloaded=1
        fi
    done

    if [ "$any_downloaded" -eq 0 ]; then
        log "All models already present. Skipping downloads."
    fi

    log "=========================================="
    echo "" >> "$OUTFILE"
}

# ── DB helpers ─────────────────────────────────────────────────────────────────

snapshot_db() {
    if [ ! -f "$DB_PATH" ]; then
        log "WARNING: DB not found at $DB_PATH — snapshot skipped."
        return
    fi
    cp "$DB_PATH" "$DB_SNAPSHOT"
    log "DB snapshot saved → $DB_SNAPSHOT"
}

restore_db() {
    if [ ! -f "$DB_SNAPSHOT" ]; then
        log "WARNING: DB snapshot not found at $DB_SNAPSHOT — restore skipped."
        return
    fi
    cp "$DB_SNAPSHOT" "$DB_PATH"
    log "DB restored from snapshot → $DB_PATH"
}

# ── Job runner ─────────────────────────────────────────────────────────────────

run_job() {
    local job="$1"
    local label="$2"
    local sep="================================================================"
    log ">>> START JOB: $job (model: $label)"
    echo "$sep" >> "$OUTFILE"

    # TELEGRAM_BOT_TOKEN blanked → telegram.py skips all sends during eval
    docker compose run --rm \
        -e LLAMA_CPP_MODEL="$label" \
        -e TELEGRAM_BOT_TOKEN="" \
        financeadvisor python run_job.py "$job" 2>&1 | tee -a "$OUTFILE"
    local status="${PIPESTATUS[0]}"

    echo "$sep" >> "$OUTFILE"
    echo "" >> "$OUTFILE"
    if [ "$status" -eq 0 ]; then
        log "<<< END JOB: $job [SUCCESS]"
    else
        log "<<< END JOB: $job [FAILED — exit $status, continuing...]"
    fi
    echo "" >> "$OUTFILE"
}

run_model() {
    local label="$1"
    local profile="$2"
    local service="$3"
    local model_file="$4"

    log "=========================================="
    log "MODEL: $label  ($model_file)"
    log "=========================================="

    restore_db

    log "Starting $service (profile: $profile)..."
    LLAMA_MODEL_FILE="$model_file" docker compose --profile "$profile" up -d "$service"
    if [ $? -ne 0 ]; then
        log "ERROR: Failed to start $service. Skipping $label."
        return 1
    fi

    log "Waiting ${MODEL_WARMUP}s for model to load..."
    sleep "$MODEL_WARMUP"
    log "Ready. Starting job cycles."

    for (( CYCLE=1; CYCLE<=CYCLES_PER_MODEL; CYCLE++ )); do
        log "---------- $label — CYCLE $CYCLE of $CYCLES_PER_MODEL ----------"
        for job in "${JOBS[@]}"; do
            run_job "$job" "$label"
        done
        log "Cycle $CYCLE complete for $label."
    done

    log "Stopping and removing $service..."
    docker compose stop "$service"
    docker compose rm -f "$service"
    log "$service stopped and removed."
    echo "" >> "$OUTFILE"
}

# ── Main ───────────────────────────────────────────────────────────────────────

log "=========================================="
log "EVAL LOOP STARTED"
log "Project dir      : $PROJECT_DIR"
log "Output file      : $OUTFILE"
log "Cycles per model : $CYCLES_PER_MODEL"
log "Model warmup     : ${MODEL_WARMUP}s"
log "Jobs             : ${JOBS[*]}"
log "Telegram         : SUPPRESSED during eval"
log "=========================================="
echo "" >> "$OUTFILE"

download_models

snapshot_db

for entry in "${MODELS[@]}"; do
    IFS='|' read -r label profile service model_file hf_repo <<< "$entry"
    run_model "$label" "$profile" "$service" "$model_file"
done

log "=========================================="
log "EVAL LOOP FINISHED"
log "DB snapshot kept at : $DB_SNAPSHOT"
log "Output saved to     : $OUTFILE"
log "=========================================="
