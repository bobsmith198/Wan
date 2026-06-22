import runpod
import os
import websocket
import base64
import json
import uuid
import logging
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import binascii
import time
import shutil
import copy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_ADDRESS = os.getenv('SERVER_ADDRESS', '127.0.0.1')
CLIENT_ID = str(uuid.uuid4())
COMFY_URL = f"http://{SERVER_ADDRESS}:8188"
WS_URL = f"ws://{SERVER_ADDRESS}:8188/ws?clientId={CLIENT_ID}"
COMFY_INPUT = "/ComfyUI/input"
COMFY_MODELS = "/ComfyUI/models"
VOLUME_ROOT = os.getenv('VOLUME_ROOT', '/runpod-volume')
DEFAULT_CKPT = os.getenv('DEFAULT_CKPT', 'wan2.2-rapid-mega-aio-nsfw-v12.safetensors')

# Where each model "type" lives under models/. Used by runtime fetch + symlink.
TYPE_DIRS = {
    'checkpoint': 'checkpoints', 'checkpoints': 'checkpoints',
    'lora': 'loras', 'loras': 'loras',
    'vae': 'vae',
    'clip': 'clip',
    'unet': 'unet', 'diffusion_model': 'unet',
    'controlnet': 'controlnet',
    'clip_vision': 'clip_vision',
    'upscale': 'upscale_models', 'upscale_models': 'upscale_models',
}

# 1x1 white PNG — dummy frame for T2V so LoadImage nodes don't error
BLANK_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

# ─────────────────────────────────────────────────────────────
# Input contract
# ─────────────────────────────────────────────────────────────
# Default (friendly) mode — unchanged from before, fully backward compatible:
#   { "input": { "prompt": "...", "image_url": "...", "seed": 42, ... } }
#
# Switch checkpoint with zero rebuild (must already be on the volume):
#   { "input": { "prompt": "...", "ckpt_name": "some-other-model.safetensors" } }
#
# Fetch a brand-new model by URL (downloaded to volume once, then cached):
#   { "input": { "prompt": "...", "model_url": "https://.../model.safetensors" } }
#   or multiple: "models": [{ "url": "...", "type": "lora", "filename": "x.safetensors" }]
#
# Bring-your-own-workflow (any ComfyUI API-format graph, no rebuild):
#   { "input": { "workflow": { ...api graph... },
#                "overrides": [{ "node": "8", "input": "seed", "value": 123 }] } }
#   Use "@start_image" / "@end_image" as override values to inject uploaded images.
#
# Named workflow stored on the volume (/runpod-volume/workflows/foo.json):
#   { "input": { "workflow_name": "foo", "overrides": [...] } }
#
# Inspect without running (returns the resolved graph):
#   { "input": { ..., "dry_run": true } }
# ─────────────────────────────────────────────────────────────


def wait_for_comfyui(timeout=300):
    logger.info("Waiting for ComfyUI...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"{COMFY_URL}/", timeout=3)
            logger.info("ComfyUI ready")
            return
        except Exception:
            time.sleep(2)
    raise Exception("ComfyUI did not start in time")


def to_multiple_of_16(v):
    return max(16, int(round(float(v) / 16.0) * 16))


def save_base64(b64_data, out_path):
    clean = b64_data.split(',')[1] if ',' in b64_data else b64_data
    clean = clean.replace('\n', '').replace('\r', '').replace(' ', '')
    unpadded = clean.rstrip('=')
    padded = unpadded + '=' * ((4 - len(unpadded) % 4) % 4)
    try:
        with open(out_path, 'wb') as f:
            f.write(base64.b64decode(padded))
        return out_path
    except (binascii.Error, ValueError) as e:
        raise Exception(f"Base64 decode failed: {e}")


def download_url(url, out_path):
    """Small-file download (images). Short timeout on purpose."""
    result = subprocess.run(
        ['wget', '-O', out_path, '--no-verbose',
         '--user-agent', 'Mozilla/5.0 (compatible)', url],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise Exception(f"Download failed: {result.stderr}")
    return out_path


def download_large(url, out_path, headers=None):
    """Large-file download (models). Resumable, generous timeout.
    Pass headers={'Authorization': 'Bearer ...'} for gated HF repos, or put
    a token query param straight in the URL for civitai etc."""
    cmd = ['wget', '--continue', '-O', out_path, '--no-verbose',
           '--user-agent', 'Mozilla/5.0 (compatible)']
    for k, v in (headers or {}).items():
        cmd += ['--header', f'{k}: {v}']
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise Exception(f"Model download failed: {result.stderr[-400:]}")
    return out_path


def ensure_model(url, mtype='checkpoint', filename=None, headers=None):
    """Download a model to the volume if absent, symlink it into ComfyUI's
    model dir, and return (filename, subdir). No-op if already present."""
    sub = TYPE_DIRS.get(mtype, 'checkpoints')
    if not filename:
        filename = os.path.basename(urllib.parse.urlparse(url).path) or 'model.safetensors'

    vol_dir = os.path.join(VOLUME_ROOT, sub)
    os.makedirs(vol_dir, exist_ok=True)
    vol_path = os.path.join(vol_dir, filename)

    if not os.path.exists(vol_path) or os.path.getsize(vol_path) == 0:
        logger.info(f"Fetching {mtype}: {filename}")
        tmp = vol_path + '.part'
        download_large(url, tmp, headers=headers)
        os.rename(tmp, vol_path)
        logger.info(f"Saved {filename} ({os.path.getsize(vol_path) // (1024 * 1024)} MB)")
    else:
        logger.info(f"Cached {mtype}: {filename}")

    comfy_dir = os.path.join(COMFY_MODELS, sub)
    os.makedirs(comfy_dir, exist_ok=True)
    link = os.path.join(comfy_dir, filename)
    if not os.path.exists(link):
        try:
            os.symlink(vol_path, link)
        except FileExistsError:
            pass
    return filename, sub


def fetch_models(inp):
    """Resolve inp['models'] and inp['model_url'] into downloaded+linked files.
    Returns list of (filename, subdir) tuples for everything fetched."""
    specs = list(inp.get('models', []) or [])
    if inp.get('model_url'):
        specs.append({
            'url': inp['model_url'],
            'type': inp.get('model_type', 'checkpoint'),
            'filename': inp.get('model_filename'),
            'headers': inp.get('model_headers'),
        })

    fetched = []
    for s in specs:
        url = s.get('url')
        if not url:
            continue
        fn, sub = ensure_model(
            url,
            mtype=s.get('type', 'checkpoint'),
            filename=s.get('filename'),
            headers=s.get('headers'),
        )
        fetched.append((fn, sub))
    return fetched


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
    data = json.dumps({"prompt": prompt, "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=data)
    try:
        result = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f"ComfyUI rejected prompt — status {e.code}: {body}")
        raise Exception(f"ComfyUI 400: {body}")
    if 'error' in result:
        raise Exception(
            f"ComfyUI error: {result['error']} | "
            f"node errors: {result.get('node_errors', {})}"
        )
    return result


def get_history(prompt_id):
    with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}") as r:
        return json.loads(r.read())


def collect_outputs(history):
    """Pull the first video out of the history, plus any saved images.
    Handles VHS video nodes ('gifs'/'videos' with fullpath) and SaveImage
    nodes ('images' with filename/subfolder/type)."""
    video_b64 = None
    images = []
    type_base = {
        'output': '/ComfyUI/output',
        'temp': '/ComfyUI/temp',
        'input': '/ComfyUI/input',
    }
    for _node_id, node_output in history.get('outputs', {}).items():
        for key in ('gifs', 'videos'):
            for vid in node_output.get(key, []):
                fp = vid.get('fullpath')
                if fp and os.path.exists(fp) and video_b64 is None:
                    with open(fp, 'rb') as f:
                        video_b64 = base64.b64encode(f.read()).decode('utf-8')
        for img in node_output.get('images', []):
            fn = img.get('filename')
            sub = img.get('subfolder', '')
            base = type_base.get(img.get('type', 'output'), '/ComfyUI/output')
            p = os.path.join(base, sub, fn) if fn else None
            if p and os.path.exists(p):
                with open(p, 'rb') as f:
                    images.append(base64.b64encode(f.read()).decode('utf-8'))
    return video_b64, images


def run_workflow(ws, prompt):
    prompt_id = queue_prompt(prompt)['prompt_id']
    while True:
        msg = ws.recv()
        if isinstance(msg, str):
            data = json.loads(msg)
            if data.get('type') == 'executing':
                d = data['data']
                if d.get('node') is None and d.get('prompt_id') == prompt_id:
                    break
    history = get_history(prompt_id)[prompt_id]
    return collect_outputs(history)


def load_workflow(path):
    with open(path) as f:
        return json.load(f)


def resolve_workflow(inp):
    """Pick the graph to run. Precedence: inline dict > named volume file > baked default."""
    wf = inp.get('workflow')
    if isinstance(wf, dict) and wf:
        return copy.deepcopy(wf), 'inline'

    name = inp.get('workflow_name')
    if name:
        safe = os.path.basename(str(name))
        if not safe.endswith('.json'):
            safe += '.json'
        for base in (os.path.join(VOLUME_ROOT, 'workflows'), '/workflows'):
            p = os.path.join(base, safe)
            if os.path.exists(p):
                logger.info(f"Loading workflow from {p}")
                return load_workflow(p), f'volume:{safe}'
        raise Exception(f"workflow_name '{name}' not found in {VOLUME_ROOT}/workflows")

    return copy.deepcopy(load_workflow('/workflow.json')), 'default'


def set_input(prompt, node, key, value):
    """Set prompt[node]['inputs'][key], no-op with a warning if the node is absent.
    Lets friendly patches degrade gracefully on graphs that don't have that node."""
    if node in prompt and 'inputs' in prompt[node]:
        prompt[node]['inputs'][key] = value
        return True
    logger.warning(f"set_input: node {node} (.{key}) not in workflow, skipped")
    return False


def apply_overrides(prompt, overrides, resolved):
    """Apply a list of {node, input, value} to any graph. Values starting with
    '@' are resolved against `resolved` (e.g. '@start_image' -> uploaded filename)."""
    for ov in overrides or []:
        node = str(ov.get('node'))
        key = ov.get('input')
        val = ov.get('value')
        if isinstance(val, str) and val.startswith('@'):
            val = resolved.get(val[1:], val)
        if node in prompt and 'inputs' in prompt[node]:
            prompt[node]['inputs'][key] = val
            logger.info(f"override: {node}.{key} = {val}")
        else:
            logger.warning(f"override skipped: node {node} not in workflow")


def patch_lora_chain(prompt, lora_pairs):
    """Original WAN LoRA chain logic for nodes 56/57/58, with bypass when unused.
    Skips quietly if those nodes aren't in the loaded graph."""
    lora_nodes = ['56', '57', '58']
    if not all(n in prompt for n in lora_nodes):
        if lora_pairs:
            logger.warning("LoRA nodes 56/57/58 absent in workflow, ignoring lora_pairs")
        return

    if not lora_pairs:
        # No LoRAs — bypass entire chain, connect node 32 directly to node 26
        set_input(prompt, '32', 'model', ['26', 0])
        for n in lora_nodes:
            prompt.pop(n, None)
        logger.info("No LoRAs — bypassing LoRA chain")
        return

    for i, node_id in enumerate(lora_nodes):
        if i < len(lora_pairs):
            pair = lora_pairs[i]
            lora_name = pair.get('low', '') or pair.get('high', '')
            lora_weight = float(pair.get('low_weight', pair.get('high_weight', 1.0)))
            set_input(prompt, node_id, 'lora_name', lora_name)
            set_input(prompt, node_id, 'strength_model', lora_weight)
            logger.info(f"LoRA slot {i + 1}: {lora_name} @ {lora_weight}")
        else:
            # Unused slot — rewire the chain to skip this node
            prev_model = prompt[node_id]['inputs']['model']
            next_nodes = {'56': '57', '57': '58', '58': None}
            next_id = next_nodes[node_id]
            if next_id and next_id in prompt:
                prompt[next_id]['inputs']['model'] = prev_model
            elif node_id == '58':
                set_input(prompt, '32', 'model', prev_model)
            prompt.pop(node_id, None)
            logger.info(f"LoRA slot {i + 1}: unused — bypassed")


def apply_wan_friendly(prompt, inp, fetched_ckpts, resolved):
    """The original WAN2.2 friendly-param layer. Only applied for the stock
    workflow (or when explicitly requested via apply_wan_params)."""
    width = to_multiple_of_16(inp.get('width', 768))
    height = to_multiple_of_16(inp.get('height', 768))
    length = int(inp.get('length', 81))
    steps = int(inp.get('steps', 4))
    seed = int(inp.get('seed', 42))
    cfg = float(inp.get('cfg', 1.0))
    sampler = inp.get('sampler', 'ipndm')
    scheduler = inp.get('scheduler', 'beta')
    denoise = float(inp.get('denoise', 1.0))
    pos_prompt = inp.get('prompt', '')
    neg_prompt = inp.get('negative_prompt', '')

    # Checkpoint: explicit > single freshly-fetched checkpoint > env default
    ckpt = inp.get('ckpt_name')
    if not ckpt and len(fetched_ckpts) == 1:
        ckpt = fetched_ckpts[0]
    ckpt = ckpt or DEFAULT_CKPT

    set_input(prompt, '26', 'ckpt_name', ckpt)
    set_input(prompt, '9', 'text', pos_prompt)
    set_input(prompt, '10', 'text', neg_prompt)
    set_input(prompt, '48', 'value', length)
    set_input(prompt, '32', 'shift', 8)
    set_input(prompt, '8', 'seed', seed)
    set_input(prompt, '8', 'steps', steps)
    set_input(prompt, '8', 'cfg', cfg)
    set_input(prompt, '8', 'sampler_name', sampler)
    set_input(prompt, '8', 'scheduler', scheduler)
    set_input(prompt, '8', 'denoise', denoise)
    set_input(prompt, '28', 'width', width)
    set_input(prompt, '28', 'height', height)

    start_image = resolved.get('start_image')
    end_image = resolved.get('end_image')
    t2v = start_image is None and end_image is None
    i2v = start_image is not None and end_image is None
    flf2v = start_image is not None and end_image is not None
    logger.info(f"Mode: {'T2V' if t2v else 'FLF2V' if flf2v else 'I2V'}")

    blank = None
    if t2v:
        set_input(prompt, '28', 'strength', 0)
        blank = write_blank(resolved['task_id'])
        set_input(prompt, '16', 'image', blank)
        set_input(prompt, '37', 'image', blank)
    elif i2v:
        set_input(prompt, '28', 'strength', 1)
        set_input(prompt, '16', 'image', start_image)
        set_input(prompt, '37', 'image', start_image)
    elif flf2v:
        set_input(prompt, '28', 'strength', 1)
        set_input(prompt, '16', 'image', start_image)
        set_input(prompt, '37', 'image', end_image)

    patch_lora_chain(prompt, inp.get('lora_pairs', []))
    return blank


def handler(job):
    inp = job.get('input', {})
    task_id = str(uuid.uuid4())[:8]
    logger.info(f"Job input keys: {list(inp.keys())}")

    # ── 0. Runtime model / LoRA fetch (no rebuild needed) ────────
    fetched = fetch_models(inp)
    fetched_ckpts = [fn for fn, sub in fetched if sub == 'checkpoints']

    # ── 1. Resolve images (shared by every mode) ─────────────────
    start_image = resolve_image(inp, 'image_path', 'image_url', 'image_base64',
                                task_id, 'start.jpg')
    end_image = resolve_image(inp, 'end_image_path', 'end_image_url', 'end_image_base64',
                              task_id, 'end.jpg')
    resolved = {'start_image': start_image, 'end_image': end_image, 'task_id': task_id}

    # ── 2. Resolve the workflow graph ────────────────────────────
    prompt, source = resolve_workflow(inp)
    logger.info(f"Workflow source: {source}")

    # Friendly WAN params apply to the stock workflow by default. Opt in/out
    # explicitly with apply_wan_params for volume/inline copies of it.
    apply_wan = bool(inp['apply_wan_params']) if 'apply_wan_params' in inp \
        else (source == 'default')

    blank = None
    if apply_wan:
        blank = apply_wan_friendly(prompt, inp, fetched_ckpts, resolved)

    # ── 3. Generic overrides (any mode, applied last so they win) ─
    apply_overrides(prompt, inp.get('overrides'), resolved)

    # ── 4. Dry run: return the resolved graph without executing ──
    if inp.get('dry_run'):
        for fname in [start_image, end_image, blank]:
            if fname:
                fp = os.path.join(COMFY_INPUT, fname)
                if os.path.exists(fp):
                    os.remove(fp)
        return {'workflow': prompt, 'source': source,
                'fetched': fetched_ckpts}

    # ── 5. Queue + run ───────────────────────────────────────────
    ws = websocket.WebSocket()
    for attempt in range(10):
        try:
            ws.connect(WS_URL)
            logger.info(f"WebSocket connected (attempt {attempt + 1})")
            break
        except Exception as e:
            logger.warning(f"WS connect failed ({attempt + 1}/10): {e}")
            if attempt == 9:
                raise
            time.sleep(3)

    try:
        video_b64, images = run_workflow(ws, prompt)
    finally:
        ws.close()

    # Cleanup temp input files
    for fname in [start_image, end_image, blank]:
        if fname:
            fp = os.path.join(COMFY_INPUT, fname)
            if os.path.exists(fp):
                os.remove(fp)

    out = {}
    if video_b64:
        out['video'] = video_b64
    if images:
        out['images'] = images
    if not video_b64 and not images:
        out['error'] = 'No output found'
    if inp.get('return_workflow'):
        out['workflow'] = prompt
        out['source'] = source
    return out


wait_for_comfyui()
runpod.serverless.start({'handler': handler})