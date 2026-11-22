# H3 Drama Studio

English | [繁體中文說明](README.zh-TW.md)

A lightweight Flask web UI for orchestrating multi-scene **MiniMax H3** drama / short-film workflows on top of **ComfyUI** — supporting Markdown storyboard import, I2V / Ref2V generation, 16:9 landscape & 9:16 portrait aspect ratios, lossless last-frame extraction with optional `RealESRGAN_x2plus` restoration, `ffmpeg` scene merging, Faster-Whisper + OpenCC Traditional Chinese subtitle generation & burning, and optional Tailscale mobile sharing.

## Features

- **Markdown Storyboard Import**: Parses `.md` scripts (`## 第 N 幕` or `## SN` sections with ```` ```text ```` / ```` ``` ```` prompt blocks) into editable per-scene cards.
- **I2V & Ref2V Modes**: Generate clips using custom start frames, auto-extracted lossless PNG last frames from the previous scene, or multi-reference character/scene images.
- **Landscape (16:9) & Portrait (9:16)**:
  - Low preview (`672×384` / `384×672`)
  - High 768p (`1344×768` / `768×1344`, with latent refinement)
- **Single-Frame Upscale / Restore**: Optional `RealESRGAN_x2plus.pth` restoration on extracted last frames to reduce multi-hop I2V degradation.
- **Audio, Subtitles & Merging**:
  - Merge all generated scenes with `ffmpeg`
  - Auto-transcribe dialogue with `faster-whisper` + `OpenCC` (Simplified → Traditional Chinese)
  - Edit `.srt` subtitles in-browser, compare against original script dialogue, and burn subtitles into the final MP4
- **Optional Tailscale Sharing**: Send the finished MP4 directly to a Tailscale device via `tailscale file cp`.

## Prerequisites

1. **ComfyUI** running locally (default `http://127.0.0.1:8188`) with MiniMax-H3 models and custom nodes installed:
   - `MiniMax-H3` (`diffusion_models`, `text_encoders`, `vae`)
   - `BlockSparseAttention`
   - `ImageUpscaleWithModel` + `RealESRGAN_x2plus.pth` (for single-frame restoration)
2. **System tools**:
   - Python 3.10+
   - `ffmpeg` and `ffprobe` on `PATH`
   - (Optional) `tailscale` CLI if using mobile file transfer

## Quick Start

1. Place this directory inside your ComfyUI folder (e.g. `ComfyUI/h3_studio`) or configure paths via environment variables.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. (Optional) Copy `.env.example` and export environment variables:
   ```bash
   export COMFYUI_URL="http://127.0.0.1:8188"
   export COMFYUI_BASE_DIR="/path/to/comfyui"
   export H3_SCRIPTS_DIR="/path/to/scripts"
   export H3_TAILSCALE_TARGET=""
   ```
4. Start the studio:
   ```bash
   ./start.sh
   ```
5. Open `http://127.0.0.1:7860` in your browser.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI API endpoint |
| `COMFYUI_BASE_DIR` | Parent directory of `h3_studio` | ComfyUI root containing `input/` and `output/` |
| `COMFYUI_INPUT_DIR` | `$COMFYUI_BASE_DIR/input` | Override ComfyUI input directory |
| `COMFYUI_OUTPUT_DIR` | `$COMFYUI_BASE_DIR/output` | Override ComfyUI output directory |
| `H3_SCRIPTS_DIR` | `./scripts` | Directory containing `.md` storyboard scripts |
| `H3_TAILSCALE_TARGET` | *(empty)* | Default Tailscale target device name |
| `H3_STUDIO_HOST` | `127.0.0.1` | Flask bind host |
| `H3_STUDIO_PORT` | `7860` | Flask bind port |

## Storyboard Skill

See [skills/h3-drama-script/SKILL.md](skills/h3-drama-script/SKILL.md) for the Markdown storyboard format and MiniMax-H3 prompting guidelines.
