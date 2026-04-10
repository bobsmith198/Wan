# rapid-wan — RunPod Serverless Worker

WAN2.2-14B-Rapid-AllInOne image-to-video on RunPod Serverless.
Models are downloaded to network volume on first run and cached.

## Setup
- Attach a network volume (recommended 30GB+)
- First cold start downloads ~14GB of models
- Subsequent starts symlink from volume — fast

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

## FLF2V
Add end_image_url / end_image_base64 / end_image_path to guide
motion toward a specific end state.

## LoRAs
Upload .safetensors files to /loras/ on your network volume.
Auto-linked on startup.

## Container disk
Set to 20GB — models live on network volume not in container.
