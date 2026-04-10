#!/bin/bash
set -e

# Link LoRAs from network volume if present
if [ -d "/runpod-volume/loras" ]; then
    mkdir -p /ComfyUI/models/loras
    ln -sf /runpod-volume/loras/* /ComfyUI/models/loras/ 2>/dev/null || true
    echo "LoRAs linked from network volume"
fi

echo "Starting ComfyUI on port 8188..."
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
