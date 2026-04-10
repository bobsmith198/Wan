#!/bin/bash
set -e

VOL="/runpod-volume"
MODELS_DIR="$VOL/models"
mkdir -p "$MODELS_DIR"

# ── WAN model ─────────────────────────────────────────────────
WAN_DEST="/ComfyUI/models/checkpoints/rapid/wan2.2-rapid-mega-aio-nsfw-v9.safetensors"
WAN_CACHE="$MODELS_DIR/wan2.2-rapid-mega-aio-nsfw-v9.safetensors"

if [ ! -f "$WAN_CACHE" ]; then
    echo "Downloading WAN2.2 Rapid AIO model (~14GB, first run only)..."
    for i in 1 2 3; do
      wget -q --show-progress \
              --retry-connrefused \
              --waitretry=10 \
              --tries=3 \
              --no-check-certificate \
              --continue \
          "https://huggingface.co/Phr00t/WAN2.2-14B-Rapid-AllInOne/resolve/main/Mega-v9/wan2.2-rapid-mega-aio-nsfw-v9.safetensors" \
          -O "$WAN_CACHE" && break
    done
    echo "Attempt $i failed, retrying in 10s..."
        sleep 10
else
    echo "WAN model found in cache."
fi
ln -sf "$WAN_CACHE" "$WAN_DEST"

# ── CLIP vision model ──────────────────────────────────────────
CLIP_DEST="/ComfyUI/models/clip_vision/clip_vision_g.safetensors"
CLIP_CACHE="$MODELS_DIR/clip_vision_g.safetensors"

if [ ! -f "$CLIP_CACHE" ]; then
    echo "Downloading CLIP vision model..."
    wget -q --show-progress \
        "https://huggingface.co/Comfy-Org/clip_vision_g/resolve/main/clip_vision_g.safetensors" \
        -O "$CLIP_CACHE"
    echo "CLIP model downloaded."
else
    echo "CLIP model found in cache."
fi
ln -sf "$CLIP_CACHE" "$CLIP_DEST"

# ── LoRAs from network volume ──────────────────────────────────
if [ -d "$VOL/loras" ]; then
    echo "Linking LoRAs from network volume..."
    mkdir -p /ComfyUI/models/loras
    for f in "$VOL/loras"/*; do
        [ -f "$f" ] && ln -sf "$f" "/ComfyUI/models/loras/$(basename $f)" 2>/dev/null || true
    done
    echo "LoRAs linked."
fi

# ── Start ComfyUI ──────────────────────────────────────────────
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
