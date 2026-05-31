FROM wlsdml1114/engui_genai-base_blackwell:1.1

WORKDIR /

RUN apt-get update && apt-get install -y \
    git wget ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/comfyanonymous/ComfyUI.git /ComfyUI
WORKDIR /ComfyUI
RUN pip install -r requirements.txt

WORKDIR /ComfyUI/custom_nodes
RUN git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    pip install -r ComfyUI-WanVideoWrapper/requirements.txt

RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    pip install -r ComfyUI-VideoHelperSuite/requirements.txt

RUN pip install runpod websocket-client

RUN mkdir -p /ComfyUI/models/checkpoints && \
    mkdir -p /ComfyUI/models/loras && \
    mkdir -p /ComfyUI/input && \
    mkdir -p /ComfyUI/output

# Model lives on the network volume at /runpod-volume/loras/
# No download needed — volume is mounted automatically by RunPod

COPY handler.py /handler.py
COPY workflow.json /workflow.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY extra_model_paths.yaml /ComfyUI/extra_model_paths.yaml
WORKDIR /
CMD ["/entrypoint.sh"]
