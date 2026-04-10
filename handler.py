import runpod
import os
import websocket
import base64
import json
import uuid
import logging
import urllib.request
import urllib.parse
import subprocess
import binascii
import time
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_ADDRESS = os.getenv('SERVER_ADDRESS', '127.0.0.1')
CLIENT_ID      = str(uuid.uuid4())
COMFY_URL      = f"http://{SERVER_ADDRESS}:8188"
WS_URL         = f"ws://{SERVER_ADDRESS}:8188/ws?clientId={CLIENT_ID}"
COMFY_INPUT    = "/ComfyUI/input"

def wait_for_comfyui(timeout=300):
    logger.info("Waiting for ComfyUI...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"{COMFY_URL}/", timeout=3)
            logger.info("ComfyUI ready")
            return
        except:
            time.sleep(2)
    raise Exception("ComfyUI did not start in time")

def to_multiple_of_16(v):
    return max(16, int(round(float(v) / 16.0) * 16))

def save_base64(b64_data, out_path):
    clean    = b64_data.split(',')[1] if ',' in b64_data else b64_data
    clean    = clean.replace('\n','').replace('\r','').replace(' ','')
    unpadded = clean.rstrip('=')
    padded   = unpadded + '=' * ((4 - len(unpadded) % 4) % 4)
    try:
        with open(out_path, 'wb') as f:
            f.write(base64.b64decode(padded))
        return out_path
    except (binascii.Error, ValueError) as e:
        raise Exception(f"Base64 decode failed: {e}")

def download_url(url, out_path):
    result = subprocess.run(
        ['wget', '-O', out_path, '--no-verbose',
         '--user-agent', 'Mozilla/5.0 (compatible)', url],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise Exception(f"Download failed: {result.stderr}")
    return out_path

def resolve_image(inp, key_path, key_url, key_b64, task_id, filename):
    os.makedirs(COMFY_INPUT, exist_ok=True)
    out = os.path.join(COMFY_INPUT, f"{task_id}_{filename}")
    if key_path in inp:
        shutil.copy(inp[key_path], out)
        return os.path.basename(out)
    elif key_url in inp:
        download_url(inp[key_url], out)
        return os.path.basename(out)
    elif key_b64 in inp:
        save_base64(inp[key_b64], out)
        return os.path.basename(out)
    return None

def queue_prompt(prompt):
    data = json.dumps({"prompt": prompt, "client_id": CLIENT_ID}).encode()
    req  = urllib.request.Request(f"{COMFY_URL}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())

def get_history(prompt_id):
    with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}") as r:
        return json.loads(r.read())

def run_workflow(ws, prompt):
    prompt_id = queue_prompt(prompt)['prompt_id']
    while True:
        msg = ws.recv()
        if isinstance(msg, str):
            data = json.loads(msg)
            if data.get('type') == 'executing':
                node = data['data'].get('node')
                pid  = data['data'].get('prompt_id')
                if node is None and pid == prompt_id:
                    break
    history = get_history(prompt_id)[prompt_id]
    for node_id, node_output in history['outputs'].items():
        if 'gifs' in node_output:
            for vid in node_output['gifs']:
                with open(vid['fullpath'], 'rb') as f:
                    return base64.b64encode(f.read()).decode('utf-8')
    return None

def load_workflow(path):
    with open(path) as f:
        return json.load(f)

def handler(job):
    inp     = job.get('input', {})
    task_id = str(uuid.uuid4())[:8]
    logger.info(f"Job input keys: {list(inp.keys())}")

    start_image = resolve_image(inp,
        'image_path', 'image_url', 'image_base64',
        task_id, 'start.jpg')
    if not start_image:
        return {'error': 'No input image provided'}

    end_image = resolve_image(inp,
        'end_image_path', 'end_image_url', 'end_image_base64',
        task_id, 'end.jpg')

    flf2v = end_image is not None
    logger.info(f"Mode: {'FLF2V' if flf2v else 'I2V'}")

    prompt = load_workflow('/workflow_i2v.json')

    width     = to_multiple_of_16(inp.get('width',  480))
    height    = to_multiple_of_16(inp.get('height', 832))
    length    = int(inp.get('length',   81))
    steps     = int(inp.get('steps',     4))
    seed      = int(inp.get('seed',     42))
    cfg       = float(inp.get('cfg',   1.0))
    sampler   = inp.get('sampler',   'sa_solver')
    scheduler = inp.get('scheduler', 'beta')
    pos_prompt = inp.get('prompt', '')
    neg_prompt = inp.get('negative_prompt', '')

    # Node 1 — CheckpointLoaderSimple
    prompt['1']['widgets_values'] = ['rapid/wan2.2-i2v-rapid-aio.safetensors']

    # Node 5 — Positive prompt
    prompt['5']['widgets_values'] = [pos_prompt]

    # Node 4 — Negative prompt
    prompt['4']['widgets_values'] = [neg_prompt]

    # Node 10 — LoadImage start frame
    prompt['10']['widgets_values'] = [start_image, 'image']

    # Node 9 — WanImageToVideo (width, height, length, noise_aug)
    prompt['9']['widgets_values'] = [width, height, length, 1]

    # Node 3 — KSampler
    prompt['3']['widgets_values'] = [seed, 'fixed', steps, cfg, sampler, scheduler, 1]

    # Node 2 — ModelSamplingSD3 shift
    prompt['2']['widgets_values'] = [8.0]

    # FLF2V — inject end image as a second LoadImage node
    if flf2v and end_image:
        max_id = str(max(int(k) for k in prompt.keys()) + 1)
        end_link_id = 99
        prompt[max_id] = {
            "inputs": [],
            "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [end_link_id], "slot_index": 0}],
            "class_type": "LoadImage",
            "widgets_values": [end_image, "image"]
        }
        # Wire end_image into WanImageToVideo node 9
        node9_inputs = prompt['9'].get('inputs', [])
        has_end = any(i.get('name') == 'end_image' for i in node9_inputs)
        if not has_end:
            node9_inputs.append({"name": "end_image", "type": "IMAGE", "link": end_link_id})
        logger.info(f"FLF2V: end image node {max_id} wired")

    # LoRA pairs
    lora_pairs = inp.get('lora_pairs', [])[:4]
    if lora_pairs:
        logger.warning("LoRA pairs provided — ensure files are in /ComfyUI/models/loras/")

    ws = websocket.WebSocket()
    for attempt in range(10):
        try:
            ws.connect(WS_URL)
            logger.info(f"WebSocket connected (attempt {attempt+1})")
            break
        except Exception as e:
            logger.warning(f"WS connect failed ({attempt+1}/10): {e}")
            if attempt == 9:
                raise
            time.sleep(3)

    try:
        video_b64 = run_workflow(ws, prompt)
    finally:
        ws.close()
        for fname in [start_image, end_image]:
            if fname:
                fp = os.path.join(COMFY_INPUT, fname)
                if os.path.exists(fp):
                    os.remove(fp)

    if video_b64:
        return {'video': video_b64}
    return {'error': 'No video output found'}

wait_for_comfyui()
runpod.serverless.start({'handler': handler})
