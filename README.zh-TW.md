# H3 Drama Studio

[English](README.md) | 繁體中文

這是一個基於 **ComfyUI** 與 **MiniMax-H3** 的輕量級 Flask Web UI，專門用來編排與製作多幕短劇／連續分鏡影片，支援 Markdown 分鏡腳本匯入、I2V / Ref2V 生成、16:9 橫式與 9:16 直式比例、無損尾幀提取＋單幀修復、片段合併、`faster-whisper` 繁體中文字幕辨識／編輯／比對／壓制，以及透過 Tailscale 傳送到手機。

## 主要功能

- **Markdown 分鏡腳本匯入**：自動解析 `.md` 腳本（支援 `## 第 N 幕` 或 `## SN` 標題搭配 ```` ```text ```` / ```` ``` ```` 提示詞區塊），轉為可逐幕編輯的卡片。
- **I2V 與 Ref2V 生成模式**：支援使用自訂首幀、自動無損提取上一幕尾幀（PNG），或搭配多張角色／場景參考圖（Ref2V）。
- **橫式（16:9）與直式（9:16）比例**：
  - Low 預覽模式（`672×384` / `384×672`）
  - High 768p 模式（`1344×768` / `768×1344`，含同解析度潛空間精修）
- **單幀放大／修復**：可選用 `RealESRGAN_x2plus.pth` 修復提取出來的尾幀，減少多輪 I2V 接續時的畫質衰減。
- **合併、字幕與台詞比對**：
  - 使用 `ffmpeg` 一鍵合併所有已生成片段
  - 內建 `faster-whisper` + `OpenCC`（簡轉繁）自動產生 `.srt` 字幕
  - 支援在網頁上直接編輯並儲存 `.srt`、與原腳本 `<d>[Chinese]</d>` 台詞對照，並將字幕壓制進最終 MP4
- **選配 Tailscale 傳送手機**：輸入目標裝置名稱即可透過 `tailscale file cp` 直接把成品傳送到手機。

## 環境需求

1. **ComfyUI** 已在本機啟動（預設 `http://127.0.0.1:8188`），並已安裝 MiniMax-H3 相關模型與節點：
   - `MiniMax-H3`（`diffusion_models`、`text_encoders`、`vae`）
   - `BlockSparseAttention`
   - `ImageUpscaleWithModel` 與 `RealESRGAN_x2plus.pth`（若需使用尾幀單幀修復）
2. **系統工具**：
   - Python 3.10+
   - `ffmpeg` 與 `ffprobe`（需在 `PATH` 中）
   - （選配）`tailscale` CLI（若需傳送影片至手機）

## 快速開始

1. 將本專案放在 ComfyUI 目錄下（例如 `ComfyUI/h3_studio`），或透過環境變數指定路徑。
2. 安裝 Python 相依套件：
   ```bash
   pip install -r requirements.txt
   ```
3. （選配）參考 `.env.example` 設定環境變數：
   ```bash
   export COMFYUI_URL="http://127.0.0.1:8188"
   export COMFYUI_BASE_DIR="/path/to/comfyui"
   export H3_SCRIPTS_DIR="/path/to/scripts"
   export H3_TAILSCALE_TARGET=""
   ```
4. 啟動 Studio：
   ```bash
   ./start.sh
   ```
5. 用瀏覽器開啟 `http://127.0.0.1:7860`。

## 環境變數說明

| 變數名稱 | 預設值 | 說明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI API 位址 |
| `COMFYUI_BASE_DIR` | `h3_studio` 的上一層目錄 | 包含 `input/` 與 `output/` 的 ComfyUI 根目錄 |
| `COMFYUI_INPUT_DIR` | `$COMFYUI_BASE_DIR/input` | 自訂 ComfyUI `input` 目錄 |
| `COMFYUI_OUTPUT_DIR` | `$COMFYUI_BASE_DIR/output` | 自訂 ComfyUI `output` 目錄 |
| `H3_SCRIPTS_DIR` | `./scripts` | 存放 `.md` 分鏡腳本的目錄 |
| `H3_TAILSCALE_TARGET` | *（空）* | 預設的 Tailscale 目標裝置名稱 |
| `H3_STUDIO_HOST` | `127.0.0.1` | Flask 綁定主機 |
| `H3_STUDIO_PORT` | `7860` | Flask 監聽埠號 |

## 分鏡腳本規範

腳本格式與 MiniMax-H3 提示詞撰寫原則請參考 [skills/h3-drama-script/SKILL.md](skills/h3-drama-script/SKILL.md)。
