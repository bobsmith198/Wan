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

# Create model dirs — models downloaded at runtime via entrypoint
RUN mkdir -p /ComfyUI/models/checkpoints/rapid && \
    mkdir -p /ComfyUI/models/clip_vision && \
    mkdir -p /ComfyUI/models/loras && \
    mkdir -p /ComfyUI/input && \
    mkdir -p /ComfyUI/output

COPY handler.py /handler.py
COPY workflow_i2v.json /workflow_i2v.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /
CMD ["/entrypoint.sh"]
