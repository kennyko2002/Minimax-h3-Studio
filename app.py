#!/usr/bin/env python3
"""MiniMax H3 Drama Studio — Flask backend for 768p I2V/Ref2V storyboard workflow."""

import difflib
import glob
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory
from faster_whisper import WhisperModel
from opencc import OpenCC

STUDIO_DIR = Path(__file__).resolve().parent
COMFY_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
BASE_DIR = Path(os.environ.get("COMFYUI_BASE_DIR", str(STUDIO_DIR.parent)))
INPUT_DIR = Path(os.environ.get("COMFYUI_INPUT_DIR", str(BASE_DIR / "input")))
OUTPUT_DIR = Path(os.environ.get("COMFYUI_OUTPUT_DIR", str(BASE_DIR / "output")))
LORAS_DIR = Path(os.environ.get("COMFYUI_LORAS_DIR", str(BASE_DIR / "models" / "loras")))
BRIDGE_DIR = Path(os.environ.get("H3_SCRIPTS_DIR", str(STUDIO_DIR / "scripts")))
PROJECTS_DIR = STUDIO_DIR / "projects"
WORK_DIR = OUTPUT_DIR / "h3_studio_work"
DEFAULT_TAILSCALE_TARGET = os.environ.get("H3_TAILSCALE_TARGET", "")

for d in (INPUT_DIR, OUTPUT_DIR, BRIDGE_DIR, PROJECTS_DIR, WORK_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(STUDIO_DIR / "templates"), static_folder=str(STUDIO_DIR / "static"))

JOB_QUEUE: queue.Queue = queue.Queue()
STATE_LOCK = threading.Lock()
CANCEL_EVENT = threading.Event()
RUNTIME_STATUS = {
    "busy": False,
    "current_task": None,
    "queued": [],
    "last_error": None,
    "logs": [],
}


def log_msg(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with STATE_LOCK:
        RUNTIME_STATUS["logs"].append(line)
        RUNTIME_STATUS["logs"] = RUNTIME_STATUS["logs"][-120:]


def project_slug(md_path: str) -> str:
    stem = Path(md_path).stem
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_") or "project"


def project_json_path(md_path: str) -> Path:
    return PROJECTS_DIR / f"{project_slug(md_path)}.json"


def parse_md_script(md_path: str) -> list[dict]:
    """Parse ```text or ``` prompt blocks and Traditional Chinese translations from a script .md."""
    text = Path(md_path).read_text(encoding="utf-8")
    sections = re.split(r"(?=^##\s*(?:第\s*\d+\s*幕|S\d+\b)|^第\s*\d+\s*幕)", text, flags=re.MULTILINE)
    scenes = []
    idx = 0
    for sec in sections:
        m_code = re.search(r"```(?:text)?\s*\n(.*?)\n```", sec, flags=re.DOTALL)
        if not m_code:
            continue
        idx += 1
        prompt = m_code.group(1).strip()
        m_title = re.match(r"^(?:#+\s*)?([^\n]+)", sec.strip())
        title = m_title.group(1).strip() if m_title else f"第 {idx} 幕"
        need_first = ("需製作首幀" in title) or ("新首幀" in title) or ("Referencing <Picture 1>" in prompt) or (idx == 1)
        m_reason = re.search(r"(?:需製作首幀|新首幀)[（(]([^）)]+)[）)]", title)
        if need_first:
            rec_text = f"建議：上傳／選擇自訂首幀（{m_reason.group(1)}）" if m_reason else "建議：上傳／選擇自訂首幀（新場景／新鏡位或開場）"
        else:
            rec_text = f"建議：沿用第 {idx - 1} 幕無損尾幀（同場景連貫）"

        custom_img = ""
        m_img = re.search(r"\*\*首幀[：:]\*\*\s*([^\s（(]+)", sec)
        if m_img and (INPUT_DIR / m_img.group(1)).exists():
            custom_img = m_img.group(1)

        zh_trans = ""
        m_zh = re.search(r"\*\*繁體中文翻譯[：:]\*\*\s*\n?(.*?)(?:\n---|\Z)", sec, flags=re.DOTALL)
        if m_zh:
            zh_trans = m_zh.group(1).strip()

        scenes.append({
            "idx": idx,
            "title": title,
            "prompt": prompt,
            "zh_translation": zh_trans,
            "recommend_text": rec_text,
            "seed": 500100 + idx,
            "node_mode": "i2v",
            "input_mode": "custom" if need_first else "prev_last",
            "custom_image": custom_img,
            "ref_images": [],
        })

    if not scenes:
        blocks = re.findall(r"```(?:text)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
        for idx, blk in enumerate(blocks, 1):
            scenes.append({
                "idx": idx,
                "title": f"第 {idx} 幕",
                "prompt": blk.strip(),
                "zh_translation": "",
                "recommend_text": "建議：上傳／選擇自訂首幀" if idx == 1 else f"建議：沿用第 {idx - 1} 幕無損尾幀",
                "seed": 500100 + idx,
                "node_mode": "i2v",
                "input_mode": "custom" if idx == 1 else "prev_last",
                "custom_image": "",
                "ref_images": [],
            })
    return scenes


def write_prompts_back_to_md(md_path: str, scenes: list[dict]) -> None:
    """Update ```text or ``` blocks in the .md file while preserving surrounding markdown structure."""
    path = Path(md_path)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    blocks = list(re.finditer(r"```(?:text)?\s*\n(.*?)\n```", text, flags=re.DOTALL))
    if len(blocks) != len(scenes):
        return
    out = []
    last_end = 0
    for m, sc in zip(blocks, scenes):
        out.append(text[last_end:m.start(1)])
        out.append(sc["prompt"].strip())
        last_end = m.end(1)
    out.append(text[last_end:])
    path.write_text("".join(out), encoding="utf-8")


def load_or_sync_project(md_path: str) -> dict:
    """Load scenes from .md (source of truth for prompts) and merge saved seeds/modes/outputs."""
    md_scenes = parse_md_script(md_path)
    jpath = project_json_path(md_path)
    saved = {}
    if jpath.exists():
        try:
            saved = json.loads(jpath.read_text(encoding="utf-8"))
        except Exception:
            saved = {}

    saved_by_idx = {s["idx"]: s for s in saved.get("scenes", [])}
    merged = []
    for sc in md_scenes:
        old = saved_by_idx.get(sc["idx"])
        if old:
            for k in ("seed", "node_mode", "input_mode", "custom_image", "ref_images"):
                if k in old and old[k] not in (None, ""):
                    sc[k] = old[k]
        merged.append(sc)

    proj = {
        "md_path": md_path,
        "slug": project_slug(md_path),
        "bgm_path": saved.get("bgm_path", ""),
        "merged_video": saved.get("merged_video", ""),
        "final_sub_video": saved.get("final_sub_video", ""),
        "srt_path": saved.get("srt_path", ""),
        "scenes": merged,
    }
    jpath.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def save_project(proj: dict) -> None:
    jpath = project_json_path(proj["md_path"])
    jpath.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")


def find_scene_videos(slug: str, idx: int) -> dict:
    """Find latest low and high videos for a given scene."""
    res = {"low": None, "high": None}
    for q in ("low", "high"):
        prefix = f"{slug}_s{idx:02d}_{q}"
        matches = sorted(OUTPUT_DIR.glob(f"{prefix}_*.mp4"), key=lambda p: p.stat().st_mtime)
        if matches:
            res[q] = matches[-1].name
    return res


def prev_last_frame_name(slug: str, idx: int, quality: str) -> str:
    return f"{slug}_last_{idx:02d}_{quality}.png"


def extract_last_frame(video_path: Path, out_name: str) -> str:
    dest = INPUT_DIR / out_name
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.08", "-i", str(video_path), "-frames:v", "1", str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return out_name


def enhance_single_frame(img_name: str, width: int = 1344, height: int = 768) -> str:
    """Restore sharpness on a single frame via RealESRGAN_x2plus without altering gamma/brightness."""
    src = INPUT_DIR / img_name
    if not src.exists():
        raise FileNotFoundError(f"找不到圖片：{img_name}")
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": img_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x2plus.pth"}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "tmp_h3_enhance"}},
    }
    _, png_path = submit_comfy_and_wait(f"單幀修復-{img_name}", wf)
    if png_path and png_path.exists():
        import shutil
        shutil.copy2(png_path, src)
        try:
            png_path.unlink()
        except OSError:
            pass
    return img_name


def resolve_dims(quality: str, aspect: str = "landscape") -> tuple[int, int]:
    w, h = (1344, 768) if quality == "high" else (672, 384)
    return (h, w) if aspect == "portrait" else (w, h)


def has_hmpussy_loras() -> bool:
    return (
        (LORAS_DIR / "hmpussy_v6_epoch30.safetensors").exists()
        and (LORAS_DIR / "vagassist_e40.safetensors").exists()
    )


def get_hmpenis_lora() -> str | None:
    for name in ("PenisV2_minimax-h3_epoch60.safetensors", "HMPenis_v2_e35.safetensors"):
        if (LORAS_DIR / name).exists():
            return name
    return None


def build_comfy_workflow(
    slug: str,
    scene: dict,
    quality: str,
    first_img: str,
    aspect: str = "landscape",
    use_hmpussy: bool = False,
    use_hmpenis: bool = False,
) -> dict:
    width, height = resolve_dims(quality, aspect)
    prompt_text = scene["prompt"].strip()
    node_mode = scene.get("node_mode", "i2v")
    if node_mode == "i2v" and not prompt_text.startswith("For the target video"):
        prompt_text = (
            "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
            + prompt_text
        )

    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "pika_h3_flex_fl2va_v1_bf16.safetensors", "strength_model": 1.0}},
    }
    model_ref = ["2", 0]
    if use_hmpussy and has_hmpussy_loras():
        wf["20"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model_ref, "lora_name": "hmpussy_v6_epoch30.safetensors", "strength_model": 0.35}}
        wf["21"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["20", 0], "lora_name": "vagassist_e40.safetensors", "strength_model": 1.0}}
        model_ref = ["21", 0]
    penis_lora = get_hmpenis_lora()
    if use_hmpenis and penis_lora:
        wf["22"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model_ref, "lora_name": penis_lora, "strength_model": 1.0}}
        model_ref = ["22", 0]

    wf.update({
        "3": {"class_type": "BlockSparseAttention", "inputs": {"model": model_ref, "selection": "sol-attn", "selection.tau": 1.3, "start_percent": 0.2, "end_percent": 1.0, "dense_blocks": "", "min_tokens": 12288, "extra_tokens": 256, "sink_conditioning": "exact_kv_and_rows", "verbose": False}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "type": "minimax", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "8": {"class_type": "BasicGuider", "inputs": {"model": ["3", 0], "conditioning": ["7", 0]}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "lcm"}},
        "10": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0"}},
        "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(scene["seed"])}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["8", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}},
    })

    if node_mode == "ref2v":
        ref_list = [first_img] + [r for r in scene.get("ref_images", []) if r and r != first_img]
        ref_inputs = {
            "clip": ["4", 0], "vae": ["5", 0], "audio_vae": ["6", 0],
            "prompt": prompt_text, "width": width, "height": height, "length": 243, "ref_image_size": "match",
        }
        for i, rimg in enumerate(ref_list[:4], start=1):
            nid = str(30 + i)
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": rimg}}
            ref_inputs[f"ref_images.ref_image_{i}"] = [nid, 0]
        wf["7"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": ref_inputs}
    else:
        wf["31"] = {"class_type": "LoadImage", "inputs": {"image": first_img}}
        wf["7"] = {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["4", 0], "vae": ["5", 0], "first_frame": ["31", 0],
                "prompt": prompt_text, "width": width, "height": height, "length": 243,
            },
        }

    final_latent = "12"
    if quality == "high":
        wf["24"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.25, 0.125, 0.0"}}
        wf["25"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["8", 0], "sampler": ["9", 0], "sigmas": ["24", 0], "latent_image": ["12", 0]}}
        final_latent = "25"

    prefix = f"{slug}_s{scene['idx']:02d}_{quality}"
    raw_last_prefix = f"{slug}_rawlast_{scene['idx']:02d}_{quality}"
    wf.update({
        "13": {"class_type": "VAEDecode", "inputs": {"samples": [final_latent, 0], "vae": ["5", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": [final_latent, 0], "vae": ["6", 0]}},
        "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "audio": ["14", 0], "fps": 24}},
        "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}},
        "17": {"class_type": "ImageFromBatch", "inputs": {"image": ["13", 0], "batch_index": -1, "length": 1}},
        "18": {"class_type": "SaveImage", "inputs": {"images": ["17", 0], "filename_prefix": raw_last_prefix}},
    })
    return wf


def submit_comfy_and_wait(label: str, wf: dict) -> tuple[Path, Path | None]:
    payload = json.dumps({"prompt": wf}).encode("utf-8")
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    pid = resp["prompt_id"]
    log_msg(f"[{label}] 已送出 ComfyUI 任務 (prompt_id={pid[:8]})")
    t0 = time.time()
    while True:
        if CANCEL_EVENT.is_set():
            raise RuntimeError(f"[{label}] 已由使用者取消")
        time.sleep(2)
        hist = json.loads(urllib.request.urlopen(f"{COMFY_URL}/history/{pid}").read().decode("utf-8"))
        if pid in hist:
            status = hist[pid].get("status", {})
            if status.get("status_str") == "error":
                if CANCEL_EVENT.is_set():
                    raise RuntimeError(f"[{label}] 已由使用者取消")
                raise RuntimeError(f"[{label}] ComfyUI 執行失敗: {json.dumps(status)}")
            outputs = hist[pid].get("outputs", {})
            if outputs:
                video_path = None
                png_path = None
                for nid, node_out in outputs.items():
                    for key in ("videos", "gifs", "images"):
                        for item in node_out.get(key) or []:
                            fname = item.get("filename", "")
                            fpath = OUTPUT_DIR / item.get("subfolder", "") / fname
                            if fname.lower().endswith(".mp4"):
                                video_path = fpath
                            elif fname.lower().endswith(".png"):
                                png_path = fpath
                if video_path or (png_path and status.get("completed", True)):
                    out_name = video_path.name if video_path else png_path.name
                    log_msg(f"[{label}] 完成 ({time.time()-t0:.1f}s) -> {out_name}")
                    return video_path, png_path


def resolve_input_image(slug: str, scene: dict, quality: str) -> str:
    idx = scene["idx"]
    if scene.get("input_mode") == "prev_last" and idx > 1:
        cand = prev_last_frame_name(slug, idx - 1, quality)
        if (INPUT_DIR / cand).exists():
            return cand
        # Fallback: extract from previous scene's latest video of same quality (or other quality)
        vids = find_scene_videos(slug, idx - 1)
        vname = vids.get(quality) or vids.get("high") or vids.get("low")
        if vname:
            return extract_last_frame(OUTPUT_DIR / vname, cand)
        raise FileNotFoundError(f"第 {idx} 幕設定沿用第 {idx-1} 幕尾幀，但第 {idx-1} 幕尚未生成影片")
    cimg = scene.get("custom_image", "").strip()
    if not cimg or not (INPUT_DIR / cimg).exists():
        raise FileNotFoundError(f"第 {idx} 幕尚未選擇有效的自訂首幀圖 ({cimg})")
    return cimg


def worker_loop() -> None:
    while True:
        task = JOB_QUEUE.get()
        try:
            with STATE_LOCK:
                RUNTIME_STATUS["busy"] = True
                RUNTIME_STATUS["last_error"] = None
                RUNTIME_STATUS["current_task"] = task["label"]
                if task["label"] in RUNTIME_STATUS["queued"]:
                    RUNTIME_STATUS["queued"].remove(task["label"])

            md_path = task["md_path"]
            proj = load_or_sync_project(md_path)
            slug = proj["slug"]
            quality = task["quality"]
            aspect = task.get("aspect", "landscape")
            scene_idx = task["idx"]
            scene = next(s for s in proj["scenes"] if s["idx"] == scene_idx)
            use_hmpussy = bool(scene.get("use_hmpussy", False))
            use_hmpenis = bool(scene.get("use_hmpenis", False))

            first_img = resolve_input_image(slug, scene, quality)
            tags = []
            if use_hmpussy and has_hmpussy_loras():
                tags.append("HMPussy")
            if use_hmpenis and get_hmpenis_lora():
                tags.append("HMPenis")
            lora_tag = f" + {'+'.join(tags)}" if tags else ""
            log_msg(f"第 {scene_idx} 幕 ({quality}/{aspect}{lora_tag}) 使用輸入圖: {first_img}")
            wf = build_comfy_workflow(
                slug, scene, quality, first_img, aspect=aspect,
                use_hmpussy=use_hmpussy, use_hmpenis=use_hmpenis,
            )
            out_video, raw_png = submit_comfy_and_wait(f"第{scene_idx}幕-{quality}", wf)

            last_png = prev_last_frame_name(slug, scene_idx, quality)
            if raw_png and raw_png.exists():
                import shutil
                shutil.copy2(raw_png, INPUT_DIR / last_png)
                log_msg(f"已從 VAEDecode 直接存成無損尾幀 -> {last_png}")
            else:
                extract_last_frame(out_video, last_png)
                log_msg(f"已擷取第 {scene_idx} 幕尾幀 -> {last_png}")
            w, h = resolve_dims(quality, aspect)
            enhance_single_frame(last_png, w, h)
            log_msg(f"已完成尾幀 RealESRGAN 修復 -> {last_png}")
        except Exception as exc:
            log_msg(f"錯誤：{exc}")
            with STATE_LOCK:
                RUNTIME_STATUS["last_error"] = str(exc)
        finally:
            with STATE_LOCK:
                RUNTIME_STATUS["busy"] = False
                RUNTIME_STATUS["current_task"] = None
            JOB_QUEUE.task_done()


threading.Thread(target=worker_loop, daemon=True).start()


def fmt_srt(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_duration(vpath: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(vpath)]
    ).decode().strip()
    return float(out)


def clean_zh_text(txt: str, cc: OpenCC) -> str:
    return cc.convert(txt.strip())


def _norm_zh(txt: str) -> str:
    return re.sub(r"[\s\W_]+", "", txt or "")


def align_asr_with_script(seg_items: list[tuple[float, float, str]], script_lines: list[str]) -> list[tuple[float, float, str]]:
    """Correct Whisper ASR homophones/typos using matching <d>[Chinese]</d> lines from the scene script."""
    if not seg_items or not script_lines:
        return seg_items

    if len(seg_items) == len(script_lines):
        out = []
        for (s_t, e_t, txt), scr in zip(seg_items, script_lines):
            ratio = difflib.SequenceMatcher(None, _norm_zh(txt), _norm_zh(scr)).ratio()
            out.append((s_t, e_t, scr if ratio >= 0.45 else txt))
        return out

    if len(script_lines) == 1:
        scr = script_lines[0]
        joined = "".join(t for _, _, t in seg_items)
        if difflib.SequenceMatcher(None, _norm_zh(joined), _norm_zh(scr)).ratio() >= 0.45:
            return [(seg_items[0][0], seg_items[-1][1], scr)]

    if len(seg_items) == 1 and len(script_lines) > 1:
        s_t, e_t, txt = seg_items[0]
        joined_scr = "".join(script_lines)
        if difflib.SequenceMatcher(None, _norm_zh(txt), _norm_zh(joined_scr)).ratio() >= 0.45:
            total_len = max(1, sum(len(_norm_zh(x)) or 1 for x in script_lines))
            cur = s_t
            out = []
            for idx, scr in enumerate(script_lines):
                w = (len(_norm_zh(scr)) or 1) / total_len
                nxt = e_t if idx == len(script_lines) - 1 else min(e_t, cur + (e_t - s_t) * w)
                out.append((cur, nxt, scr))
                cur = nxt
            return out

    # General monotonic best-match per ASR segment
    out = []
    min_j = 0
    for s_t, e_t, txt in seg_items:
        best_j, best_r = -1, 0.0
        nt = _norm_zh(txt)
        for j in range(min_j, len(script_lines)):
            r = difflib.SequenceMatcher(None, nt, _norm_zh(script_lines[j])).ratio()
            if r > best_r:
                best_r, best_j = r, j
        if best_j >= 0 and best_r >= 0.5:
            out.append((s_t, e_t, script_lines[best_j]))
            min_j = best_j + 1
        else:
            out.append((s_t, e_t, txt))
    return out


@app.route("/")
def index():
    return render_template(
        "index.html",
        scripts_dir=str(BRIDGE_DIR),
        default_tailscale_target=DEFAULT_TAILSCALE_TARGET,
        has_hmpussy=has_hmpussy_loras(),
        has_hmpenis=bool(get_hmpenis_lora()),
    )


@app.route("/files/input/<path:filename>")
def serve_input(filename):
    return send_from_directory(str(INPUT_DIR), filename)


@app.route("/files/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(str(OUTPUT_DIR), filename)


@app.route("/api/scripts", methods=["GET"])
def api_list_scripts():
    files = []
    for folder in (BRIDGE_DIR, OUTPUT_DIR):
        if folder.exists():
            for p in folder.glob("*.md"):
                files.append({"path": str(p), "name": p.name, "dir": folder.name, "mtime": p.stat().st_mtime})
    files.sort(key=lambda x: x["mtime"], reverse=True)
    inputs = sorted([p.name for p in INPUT_DIR.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])
    audios = sorted([p.name for p in INPUT_DIR.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac")])
    return jsonify({"scripts": files, "input_images": inputs, "input_audios": audios})


@app.route("/api/project/load", methods=["POST"])
def api_load_project():
    data = request.json or {}
    md_path = data.get("md_path", "")
    if not md_path or not Path(md_path).exists():
        return jsonify({"error": "找不到指定的 .md 腳本檔案"}), 404
    proj = load_or_sync_project(md_path)
    if not proj["scenes"]:
        return jsonify({
            "error": "腳本解析結果為 0 幕！請確認每幕標題為「## 第 N 幕」或「## SN」，且英文提示詞有包在 ```text ... ``` 或 ``` ... ``` 程式碼區塊內。"
        }), 400
    return jsonify(enrich_project(proj))


@app.route("/api/project/save", methods=["POST"])
def api_save_project():
    data = request.json or {}
    md_path = data.get("md_path", "")
    if not md_path:
        return jsonify({"error": "缺少 md_path"}), 400
    proj = load_or_sync_project(md_path)
    incoming_by_idx = {s["idx"]: s for s in data.get("scenes", [])}
    for sc in proj["scenes"]:
        inc = incoming_by_idx.get(sc["idx"])
        if inc:
            for k in ("prompt", "seed", "node_mode", "input_mode", "custom_image", "ref_images", "use_hmpussy", "use_hmpenis"):
                if k in inc:
                    sc[k] = inc[k]
    if "bgm_path" in data:
        proj["bgm_path"] = data["bgm_path"]
    save_project(proj)
    write_prompts_back_to_md(md_path, proj["scenes"])
    return jsonify(enrich_project(proj))


def enrich_project(proj: dict) -> dict:
    slug = proj["slug"]
    script_dialogues = []
    cur_sec = 0
    for sc in proj["scenes"]:
        idx = sc["idx"]
        vids = find_scene_videos(slug, idx)
        sc["videos"] = vids
        # Preview which input image will be used for low / high
        previews = {}
        for q in ("low", "high"):
            if sc.get("input_mode") == "prev_last" and idx > 1:
                cand = prev_last_frame_name(slug, idx - 1, q)
                previews[q] = cand if (INPUT_DIR / cand).exists() else None
            else:
                cimg = sc.get("custom_image", "")
                previews[q] = cimg if cimg and (INPUT_DIR / cimg).exists() else None
        sc["input_previews"] = previews

        d_lines = [d.strip() for d in re.findall(r"<d>\[Chinese\]\s*(.*?)</d>", sc.get("prompt", "")) if d.strip()]
        script_dialogues.append({
            "idx": idx,
            "time_range": f"{cur_sec // 60:02d}:{cur_sec % 60:02d} ~ {(cur_sec + 10) // 60:02d}:{(cur_sec + 10) % 60:02d}",
            "dialogues": d_lines,
        })
        cur_sec += 10

    proj["script_dialogues"] = script_dialogues
    srt_file = OUTPUT_DIR / f"{slug}.srt"
    proj["srt_text"] = srt_file.read_text(encoding="utf-8") if srt_file.exists() else ""
    return proj


@app.route("/api/upload/image", methods=["POST"])
def api_upload_image():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "未收到檔案"}), 400
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", f.filename)
    dest = INPUT_DIR / safe
    f.save(str(dest))
    return jsonify({"filename": safe})


@app.route("/api/image/enhance", methods=["POST"])
def api_enhance_image():
    data = request.json or {}
    filename = data.get("filename", "").strip()
    quality = data.get("quality", "high")
    aspect = data.get("aspect", "landscape")
    w, h = resolve_dims(quality, aspect)
    if not filename:
        return jsonify({"error": "沒有可修復的圖片"}), 400
    try:
        enhance_single_frame(filename, w, h)
        log_msg(f"已手動修復提亮圖片 -> {filename}")
        return jsonify({"ok": True, "filename": filename})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/scene/run", methods=["POST"])
def api_run_scenes():
    data = request.json or {}
    md_path = data.get("md_path", "")
    from_idx = int(data.get("from_idx", 1))
    to_idx = int(data.get("to_idx", from_idx))
    quality = data.get("quality", "low")
    aspect = data.get("aspect", "landscape")
    if quality not in ("low", "high"):
        return jsonify({"error": "quality 必須為 low 或 high"}), 400
    if aspect not in ("landscape", "portrait"):
        return jsonify({"error": "aspect 必須為 landscape 或 portrait"}), 400

    proj = load_or_sync_project(md_path)
    CANCEL_EVENT.clear()
    queued_labels = []
    for sc in proj["scenes"]:
        if from_idx <= sc["idx"] <= to_idx:
            label = f"第{sc['idx']}幕 ({quality}/{aspect})"
            with STATE_LOCK:
                RUNTIME_STATUS["queued"].append(label)
            JOB_QUEUE.put({"md_path": md_path, "idx": sc["idx"], "quality": quality, "aspect": aspect, "label": label})
            queued_labels.append(label)
    return jsonify({"queued": queued_labels})


@app.route("/api/scene/cancel", methods=["POST"])
def api_cancel_scenes():
    CANCEL_EVENT.set()
    # Drain pending jobs from our queue
    while not JOB_QUEUE.empty():
        try:
            JOB_QUEUE.get_nowait()
            JOB_QUEUE.task_done()
        except queue.Empty:
            break
    with STATE_LOCK:
        RUNTIME_STATUS["queued"].clear()
    # Interrupt ComfyUI running job and clear ComfyUI pending queue
    for endpoint, body in [("/interrupt", {}), ("/queue", {"clear": True})]:
        try:
            req = urllib.request.Request(
                f"{COMFY_URL}{endpoint}",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass
    log_msg("已停止並清除排隊任務")
    return jsonify({"ok": True})


@app.route("/api/status", methods=["GET"])
def api_status():
    md_path = request.args.get("md_path", "")
    with STATE_LOCK:
        st = {
            "busy": RUNTIME_STATUS["busy"],
            "current_task": RUNTIME_STATUS["current_task"],
            "queued": list(RUNTIME_STATUS["queued"]),
            "last_error": RUNTIME_STATUS["last_error"],
            "logs": list(RUNTIME_STATUS["logs"][-40:]),
        }
    if md_path and Path(md_path).exists():
        st["project"] = enrich_project(load_or_sync_project(md_path))
    return jsonify(st)


@app.route("/api/postprocess/merge", methods=["POST"])
def api_merge():
    data = request.json or {}
    md_path = data.get("md_path", "")
    prefer = data.get("prefer", "high")  # high or low
    proj = load_or_sync_project(md_path)
    slug = proj["slug"]

    clips = []
    for sc in proj["scenes"]:
        vids = find_scene_videos(slug, sc["idx"])
        chosen = vids["high"] or vids["low"] if prefer == "high" else vids["low"] or vids["high"]
        if not chosen:
            return jsonify({"error": f"第 {sc['idx']} 幕尚未生成任何影片"}), 400
        clips.append(OUTPUT_DIR / chosen)

    concat_txt = WORK_DIR / f"{slug}_concat.txt"
    concat_txt.write_text("\n".join(f"file '{c}'" for c in clips) + "\n", encoding="utf-8")
    merged_name = f"{slug}_merged_{prefer}.mp4"
    merged_path = OUTPUT_DIR / merged_name

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", "32000", str(merged_path),
        ],
        check=True,
    )
    proj["merged_video"] = merged_name
    save_project(proj)
    log_msg(f"已完成合併 ({len(clips)} 幕) -> {merged_name}")
    return jsonify({"merged_video": merged_name, "clip_count": len(clips)})


@app.route("/api/postprocess/asr", methods=["POST"])
def api_asr():
    data = request.json or {}
    md_path = data.get("md_path", "")
    prefer = data.get("prefer", "high")
    proj = load_or_sync_project(md_path)
    slug = proj["slug"]

    model = WhisperModel("small", device="cpu", compute_type="int8")
    cc = OpenCC("s2twp")
    hallucinations = ("點贊", "訂閱", "鈴鐺", "詞曲", "李宗盛", "字幕製作", "Zither", "Amara", "Ming")

    lines = []
    sidx = 1
    cur_t = 0.0
    for sc in proj["scenes"]:
        vids = find_scene_videos(slug, sc["idx"])
        chosen = vids["high"] or vids["low"] if prefer == "high" else vids["low"] or vids["high"]
        if not chosen:
            continue
        vp = OUTPUT_DIR / chosen
        dur = get_duration(vp)
        wav = WORK_DIR / f"{slug}_s{sc['idx']:02d}.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(vp), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(str(wav), "rb") as wvf:
            audio_np = np.frombuffer(wvf.readframes(wvf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

        script_lines = [d.strip() for d in re.findall(r"<d>\[Chinese\]\s*(.*?)</d>", sc["prompt"]) if d.strip()]
        segments, _ = model.transcribe(
            audio_np, language="zh", vad_filter=True,
            vad_parameters=dict(threshold=0.25, min_silence_duration_ms=250),
            condition_on_previous_text=False, no_speech_threshold=0.6, log_prob_threshold=-1.0,
            initial_prompt="繁體中文對話",
        )
        seg_items = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.7:
                continue
            txt = clean_zh_text(seg.text, cc)
            if not txt or len(txt) > 80 or not re.sub(r"[\W_]+", "", txt) or any(h in txt for h in hallucinations):
                continue
            s_t = max(0.0, min(float(seg.start), dur - 0.2))
            e_t = max(0.0, min(float(seg.end), dur - 0.05))
            if e_t > s_t + 0.15:
                seg_items.append((s_t, e_t, txt))

        if seg_items and script_lines:
            seg_items = align_asr_with_script(seg_items, script_lines)
        elif not seg_items and script_lines:
            span = max(1.0, (dur - 1.0) / len(script_lines))
            for li, ltxt in enumerate(script_lines):
                s_t = 0.5 + li * span
                e_t = min(dur - 0.2, s_t + span - 0.1)
                if e_t > s_t + 0.1:
                    seg_items.append((s_t, e_t, ltxt))

        for s_t, e_t, txt in seg_items:
            lines.append(f"{sidx}\n{fmt_srt(cur_t + s_t)} --> {fmt_srt(cur_t + e_t)}\n{txt}\n")
            sidx += 1
        cur_t += dur

    srt_text = "\n".join(lines)
    srt_path = OUTPUT_DIR / f"{slug}.srt"
    srt_path.write_text(srt_text, encoding="utf-8")
    proj["srt_path"] = srt_path.name
    save_project(proj)
    log_msg(f"語音辨識完成 -> {srt_path.name} ({sidx-1} 條字幕)")
    return jsonify({"srt_text": srt_text, "srt_name": srt_path.name})


@app.route("/api/postprocess/save_srt", methods=["POST"])
def api_save_srt():
    data = request.json or {}
    md_path = data.get("md_path", "")
    srt_text = data.get("srt_text", "")
    proj = load_or_sync_project(md_path)
    slug = proj["slug"]
    srt_path = OUTPUT_DIR / f"{slug}.srt"
    srt_path.write_text(srt_text, encoding="utf-8")
    proj["srt_path"] = srt_path.name
    save_project(proj)
    log_msg(f"已儲存修改後的字幕 -> {srt_path.name}")
    return jsonify({"ok": True, "srt_name": srt_path.name})


@app.route("/api/postprocess/burn", methods=["POST"])
def api_burn():
    data = request.json or {}
    md_path = data.get("md_path", "")
    srt_text = data.get("srt_text", "")
    bgm_name = data.get("bgm_name", "").strip()
    proj = load_or_sync_project(md_path)
    slug = proj["slug"]

    merged_name = proj.get("merged_video")
    if not merged_name or not (OUTPUT_DIR / merged_name).exists():
        return jsonify({"error": "請先執行「合併段落」再壓制字幕"}), 400

    srt_path = OUTPUT_DIR / f"{slug}.srt"
    srt_path.write_text(srt_text, encoding="utf-8")
    final_name = f"{slug}_final_sub.mp4"
    final_path = OUTPUT_DIR / final_name

    vf = f"subtitles={srt_path}:force_style='FontName=Noto Sans CJK HK,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,MarginV=24'"
    if bgm_name and (INPUT_DIR / bgm_name).exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(OUTPUT_DIR / merged_name), "-stream_loop", "-1", "-i", str(INPUT_DIR / bgm_name),
                "-filter_complex", "[1:a]volume=0.18[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v", "-map", "[aout]", "-vf", vf,
                "-c:v", "libx264", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k", str(final_path),
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(OUTPUT_DIR / merged_name), "-vf", vf,
                "-c:v", "libx264", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M", "-preset", "medium",
                "-c:a", "copy", str(final_path),
            ],
            check=True,
        )
    proj["final_sub_video"] = final_name
    proj["srt_path"] = srt_path.name
    save_project(proj)
    log_msg(f"字幕壓制完成 -> {final_name}")
    return jsonify({"final_sub_video": final_name})


@app.route("/api/share/tailscale", methods=["POST"])
def api_tailscale():
    data = request.json or {}
    filename = data.get("filename", "")
    target = (data.get("target") or DEFAULT_TAILSCALE_TARGET).strip()
    if not target:
        return jsonify({"error": "請輸入 Tailscale 目標裝置名稱"}), 400
    fpath = OUTPUT_DIR / filename
    if not filename or not fpath.exists():
        return jsonify({"error": f"找不到影片檔案：{filename}"}), 404
    subprocess.run(["tailscale", "file", "cp", str(fpath), f"{target}:"], check=True)
    log_msg(f"已透過 Tailscale 傳送 {filename} 至 {target}")
    return jsonify({"ok": True, "filename": filename, "target": target})


if __name__ == "__main__":
    host = os.environ.get("H3_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("H3_STUDIO_PORT", "7860"))
    app.run(host=host, port=port, debug=False, threaded=True)

