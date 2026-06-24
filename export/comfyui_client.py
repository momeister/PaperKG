"""Best-effort ComfyUI image generation for the LaTeX export.

ComfyUI is entirely optional: if the server is not reachable, or anything in the
generation pipeline fails, every function degrades to "no image" without raising, so
the export still succeeds. The default workflow is a minimal SD txt2img graph; the
checkpoint name can be overridden via the ``COMFYUI_CHECKPOINT`` env var.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DEFAULT_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors")


def is_available(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> bool:
    """True if a ComfyUI server answers at ``base_url``."""
    try:
        with urllib.request.urlopen(f"{base_url}/system_stats", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _default_workflow(prompt: str, checkpoint: str, seed: int) -> dict[str, Any]:
    """A minimal ComfyUI txt2img API graph (KSampler + SDXL-style checkpoint)."""
    negative = "text, watermark, blurry, low quality, deformed"
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 22, "cfg": 7.0, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 512, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "sciencekg", "images": ["8", 0]}},
    }


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def generate_image(
    prompt: str,
    out_path: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    checkpoint: str = DEFAULT_CHECKPOINT,
    poll_timeout: float = 180.0,
) -> Path | None:
    """Generate one image for ``prompt`` and save it to ``out_path``.

    Returns the saved path, or ``None`` if ComfyUI is unreachable or anything fails.
    """
    try:
        client_id = uuid.uuid4().hex
        workflow = _default_workflow(prompt, checkpoint, seed=int(time.time()) % 1_000_000)
        queued = _post_json(
            f"{base_url}/prompt", {"prompt": workflow, "client_id": client_id}, timeout=10.0
        )
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            return None

        deadline = time.time() + poll_timeout
        history: dict[str, Any] = {}
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base_url}/history/{prompt_id}", timeout=10.0) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
            except Exception:
                history = {}
            if prompt_id in history:
                break
            time.sleep(1.5)

        outputs = (history.get(prompt_id) or {}).get("outputs") or {}
        for node_out in outputs.values():
            for image in node_out.get("images") or []:
                params = urllib.parse.urlencode({
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                })
                with urllib.request.urlopen(f"{base_url}/view?{params}", timeout=30.0) as img_resp:
                    out_path.write_bytes(img_resp.read())
                return out_path
        return None
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None
