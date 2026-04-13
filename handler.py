import runpod
import os
import websocket
import base64
import json
import uuid
import logging
import urllib.request
import subprocess
import binascii
import time
import shutil
import copy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_ADDRESS = os.getenv('SERVER_ADDRESS', '127.0.0.1')
CLIENT_ID      = str(uuid.uuid4())
COMFY_URL      = f"http://{SERVER_ADDRESS}:8188"
WS_URL         = f"ws://{SERVER_ADDRESS}:8188/ws?clientId={CLIENT_ID}"
COMFY_INPUT    = "/ComfyUI/input"

# 1x1 white PNG — dummy frame for T2V so LoadImage nodes don't error
BLANK_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

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

def write_blank(task_id):
    path = os.path.join(COMFY_INPUT, f"{task_id}_blank.png")
    os.makedirs(COMFY_INPUT, exist_ok=True)
    save_base64(BLANK_B64, path)
    return os.path.basename(path)

def queue_prompt(prompt):
    data   = json.dumps({"prompt": prompt, "client_id": CLIENT_ID}).encode()
    req    = urllib.request.Request(f"{COMFY_URL}/prompt", data=data)
    result = json.loads(urllib.request.urlopen(req).read())
    if 'error' in result:
        raise Exception(
            f"ComfyUI error: {result['error']} | "
            f"node errors: {result.get('node_errors', {})}"
        )
    return result

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
                if data['data'].get('node') is None and \
                   data['data'].get('prompt_id') == prompt_id:
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

    end_image = resolve_image(inp,
        'end_image_path', 'end_image_url', 'end_image_base64',
        task_id, 'end.jpg')

    t2v   = start_image is None and end_image is None
    flf2v = start_image is not None and end_image is not None
    i2v   = start_image is not None and end_image is None
    logger.info(f"Mode: {'T2V' if t2v else 'FLF2V' if flf2v else 'I2V'}")

    prompt = copy.deepcopy(load_workflow('/workflow.json'))

    width      = to_multiple_of_16(inp.get('width',  768))
    height     = to_multiple_of_16(inp.get('height', 768))
    length     = int(inp.get('length',   81))
    steps      = int(inp.get('steps',     4))
    seed       = int(inp.get('seed',     42))
    cfg        = float(inp.get('cfg',   1.0))
    sampler    = inp.get('sampler',   'ipndm')
    scheduler  = inp.get('scheduler', 'beta')
    pos_prompt = inp.get('prompt', '')
    neg_prompt = inp.get('negative_prompt', '')

    # ── Standard patches ─────────────────────────────────────
    prompt['26']['inputs']['ckpt_name']   = 'rapid/wan2.2-rapid-mega-aio-nsfw-v12.safetensors'
    prompt['9']['inputs']['text']         = pos_prompt
    prompt['10']['inputs']['text']        = neg_prompt
    prompt['48']['inputs']['value']       = length
    prompt['32']['inputs']['shift']       = 8
    prompt['8']['inputs']['seed']         = seed
    prompt['8']['inputs']['steps']        = steps
    prompt['8']['inputs']['cfg']          = cfg
    prompt['8']['inputs']['sampler_name'] = sampler
    prompt['8']['inputs']['scheduler']    = scheduler
    prompt['8']['inputs']['denoise']      = 1
    prompt['28']['inputs']['width']       = width
    prompt['28']['inputs']['height']      = height

    # ── Mode patches ──────────────────────────────────────────
    blank = None

    if t2v:
        # strength=0 — WanVaceToVideo ignores control entirely
        # LoadImage nodes still need a valid file so ComfyUI doesn't reject
        prompt['28']['inputs']['strength'] = 0
        blank = write_blank(task_id)
        prompt['16']['inputs']['image'] = blank
        prompt['37']['inputs']['image'] = blank

    elif i2v:
        # strength=1, start frame drives motion, end frame = same image (ignored)
        prompt['28']['inputs']['strength'] = 1
        prompt['16']['inputs']['image']    = start_image
        prompt['37']['inputs']['image']    = start_image

    elif flf2v:
        # strength=1, both frames guide the generation
        prompt['28']['inputs']['strength'] = 1
        prompt['16']['inputs']['image']    = start_image
        prompt['37']['inputs']['image']    = end_image

    # LoRA pairs
    # ── LoRA pairs — patch nodes 56, 57, 58 ──────────────────────
    lora_nodes = ['56', '57', '58']
    lora_pairs = inp.get('lora_pairs', [])
    
    for i, node_id in enumerate(lora_nodes):
        if i < len(lora_pairs):
            pair = lora_pairs[i]
            # Use the 'low' lora as recommended
            lora_name   = pair.get('low', '')
            lora_weight = float(pair.get('low_weight', 1.0))
            prompt[node_id]['inputs']['lora_name']      = lora_name
            prompt[node_id]['inputs']['strength_model'] = lora_weight
            logger.info(f"LoRA slot {i+1}: {lora_name} @ {lora_weight}")
        else:
            # No LoRA for this slot — set empty name and weight 0
            # so the loader passes through without effect
            prompt[node_id]['inputs']['lora_name']      = ''
            prompt[node_id]['inputs']['strength_model'] = 0.0
            logger.info(f"LoRA slot {i+1}: empty (passthrough)")
    # If no loras at all, remove the lora nodes from the prompt
  # and reconnect node 32 directly to node 26
    if not lora_pairs:
        prompt['32']['inputs']['model'] = ['26', 0]
        del prompt['56']
        del prompt['57']
        del prompt['58']
    # Connect WebSocket
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
        # Cleanup temp input files
        for fname in [start_image, end_image, blank]:
            if fname:
                fp = os.path.join(COMFY_INPUT, fname)
                if os.path.exists(fp):
                    os.remove(fp)

    if video_b64:
        return {'video': video_b64}
    return {'error': 'No video output found'}

wait_for_comfyui()
runpod.serverless.start({'handler': handler})