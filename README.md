# rapid-wan — RunPod Serverless Worker

WAN2.2-14B-Rapid-AllInOne image-to-video generation on RunPod.

## Key differences from standard WAN
- Single checkpoint (no separate VAE/CLIP)
- 4 steps at cfg=1 (2-3x faster than standard)
- sa_solver/beta sampler recommended

## Input
```json
{
  "input": {
    "prompt": "a woman walking through a market",
    "negative_prompt": "blurry, low quality",
    "image_url": "https://...",
    "seed": 42,
    "cfg": 1.0,
    "width": 480,
    "height": 832,
    "length": 81,
    "steps": 4,
    "sampler": "sa_solver",
    "scheduler": "beta"
  }
}
```

## FLF2V (first/last frame)
Add `end_image_url`, `end_image_base64`, or `end_image_path` to guide motion toward an end state.

## LoRAs
Upload `.safetensors` files to `/runpod-volume/loras/` on your network volume.
They are auto-linked into `/ComfyUI/models/loras/` on startup.

## Deploy
1. Push this repo to GitHub
2. Create RunPod Serverless endpoint → select GitHub repo
3. Set GPU to 24GB+ (A100 recommended, works on 16GB too)
4. Attach your network volume if using LoRAs
