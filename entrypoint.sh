#!/bin/bash
set -e

# ── Link LoRAs from network volume ────────────────────────────
if [ -d "/runpod-volume/loras" ]; then
    echo "Linking LoRAs from network volume..."
    mkdir -p /ComfyUI/models/loras
    for f in "/runpod-volume/loras"/*; do
        [ -f "$f" ] && ln -sf "$f" "/ComfyUI/models/loras/$(basename "$f")" 2>/dev/null || true
    done
    echo "LoRAs linked."
fi

# ── Start ComfyUI ─────────────────────────────────────────────
echo "Starting ComfyUI..."
cd /ComfyUI
python main.py \
    --listen 127.0.0.1 \
    --port 8188 \
    --disable-auto-launch \
    --gpu-only \
    --lowvram \
    &

echo "Starting RunPod handler..."
cd /
python -u handler.py
