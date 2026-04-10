FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

WORKDIR /

RUN apt-get update && apt-get install -y \
    git wget ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /ComfyUI
WORKDIR /ComfyUI
RUN pip install -r requirements.txt

# Custom nodes
WORKDIR /ComfyUI/custom_nodes
RUN git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    pip install -r ComfyUI-WanVideoWrapper/requirements.txt

RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    pip install -r ComfyUI-VideoHelperSuite/requirements.txt

# RunPod + websocket
RUN pip install runpod websocket-client

# Create model dirs
RUN mkdir -p /ComfyUI/models/checkpoints/rapid && \
    mkdir -p /ComfyUI/models/clip_vision && \
    mkdir -p /ComfyUI/models/loras && \
    mkdir -p /ComfyUI/input && \
    mkdir -p /ComfyUI/output

# Download WAN model at build time
RUN wget -q --show-progress \
    --no-check-certificate \
    "https://huggingface.co/Phr00t/WAN2.2-14B-Rapid-AllInOne/resolve/main/Mega-v9/wan2.2-rapid-mega-aio-nsfw-v9.safetensors" \
    -O /ComfyUI/models/checkpoints/rapid/wan2.2-rapid-mega-aio-nsfw-v9.safetensors

# Download CLIP vision model at build time
# Download CLIP vision model at build time
RUN wget -q --show-progress \
    --no-check-certificate \
    "https://huggingface.co/Comfy-Org/clip_vision_g/resolve/main/clip_vision_g.safetensors" \
    -O /ComfyUI/models/clip_vision/clip_vision_g.safetensors
    
COPY handler.py /handler.py
COPY workflow_i2v.json /workflow_i2v.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /
CMD ["/entrypoint.sh"]
