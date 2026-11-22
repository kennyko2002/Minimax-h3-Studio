---
name: h3-drama-script
description: >-
  將小說或故事段落改寫為完整的 MiniMax H3 分幕影片腳本（每幕 10 秒、不省略劇情、自然轉場），並提供繁體中文翻譯、總幕數與首幀製作清單，以及透過本機 ComfyUI I2V（1344×768 / 768p＋同解析度潛空間精修）生成含繁體中文字幕影片的標準流程。
---

# MiniMax H3 短劇腳本與 768p I2V 生成規範

當使用者要求將故事／小說段落製作成 MiniMax 影片腳本或直接生成影片時，必須嚴格遵守以下規則。

## 1. 腳本拆幕與內容規範

1. **完整保留內容**：絕不為了縮短時間而省略任何對話、動作細節或情緒轉折；依劇情長度自然拆分為多幕。
2. **固定幕長**：每幕固定為 **10 秒**（在 ComfyUI H3 中對應 `243` frames @ 24fps = 10.125 秒），時間戳記嚴格遞增且落於 `00:00.000`–`00:10.000` 內。
3. **自然轉場與首幀判斷（I2V 為主）**：
   - **同場景連續戲份**：使用 **`MiniMaxH3ImageToVideo`（I2V）** 直接將上一幕尾幀接至 `first_frame`，達成第 0 幀像素級無縫接續，並在提示詞中重複鎖定角色五官、髮型與當前衣著／全裸狀態。
   - **需要製作首幀的時機**：僅在 **(a) 第 1 幕開場**、**(b) 重大時空／場景跳轉**（如室內白天切換至夜晚戶外）、**(c) 新空間／新核心角色首次登場**、或 **(d) 連續串接達 3～4 幕時，刻意安排一次「換鏡位／切角度」（如中景雙人切換為過肩特寫、側拍或反打鏡位）並製作新首幀重新錨定**——如此既能中斷 `I2V` 多代尾幀累加造成的變暗變糊，又能藉由自然剪輯切鏡消除重繪首幀的畫面割裂感（若需同時引入多張新圖或固定音色參考，可局部改用 `MiniMaxH3ReferenceToVideo`）。
4. **提示詞格式**：
   - 遵守 `h3-prompt-writing` 技能規範，**每幕皆須包含 `subject_definitions:`（定義 `<Picture 1>`、`<Subject 1>`、`<Subject 2>` 的五官、髮型、眼鏡與當前衣著／全裸狀態）、`summary:`、`retention_analysis:`、`detailed_description:`、`overall_soundscape:` 與 `non_diegetic_music:`**。
   - **`summary:` 與動感對齊**：`summary:` 會直接影響模型對全域節奏與運鏡的判斷，須與 `detailed_description:` 的節奏、運鏡及鏡頭數保持一致，不可只寫靜態情節摘要。需要加強動感時，依該幕實際分鏡同步加入 `brisk physical motion`、`shifting camera framing`（若有 `[Shot 2]` 切鏡才加上 `and a cut to a tight reaction close-up`；若不影響下一幕尾幀清晰度，亦可酌加 `slight handheld camera movement`）。
   - I2V 提示詞開頭須帶標準前綴：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`。
   - 中文對白原文保留於 `<d>[Chinese] ...</d>` 內（去除過度驚嘆號 `！` 以維持自然語氣），心聲／旁白使用 `says in an off-screen voiceover: <d>[Chinese] ...</d> while his/her lips remain completely closed`。
   - **男女聲調與配樂統一**：各幕 `overall_soundscape:` 應使用一致的男女聲線描述；若後製要自行混入連續配樂，各幕 `non_diegetic_music:` 統一設為 `None. Pure diegetic ambient sound and natural dialogue only, with no background music.`，避免每 10 秒出現斷裂的背景音樂。
5. **畫面與物理防呆規範**：
   - **單一道具線性軌跡**：同一件道具（如內褲、手機、衣物）在單一 10 秒內只能有一條線性狀態軌跡，不可前半段已拿在手上、後半段又從身上脫下，避免生成兩件重複道具；並明確寫出 `the single ...`。
   - **雙手占用上限**：避免在同一幕讓單一角色同時執行兩項占用雙手的精細動作（如手持電話＋塗乳液），以免長出第三隻手。
   - **表情去誇張化**：害羞／臉紅改用 `looks shy and slightly flustered`、`looks bashful` 等神情描述，避免 `blushes crimson`、`cheeks flushed` 造成臉頰色塊。
   - **禁用否定詞**：`detailed_description` 一律使用正面空間／狀態描述（如 `clear gap of open air`、`completely bare`），避免出現不希望發生的動詞名詞。
   - **防背景亂碼與假字幕**：場景中的招牌、海報、書架、螢幕等應搭配 `shallow depth of field, softly blurred background` 與無字色塊描述；靜態密集對白幕可加上 `clean borderless raw camera footage`，避免模型自己在畫面底部生出假字幕。
6. **回覆必備項目**：
   - 總幕數與總時長
   - 明確列出**哪幾幕需要製作首幀**（其餘沿用前一幕尾幀）
   - 每一幕的 **MiniMax H3 英文腳本** ＋ **繁體中文翻譯**

## 2. 本機 ComfyUI 768p I2V 生成與後製標準流程

1. **ComfyUI 端點與模型**：
   - API：`http://127.0.0.1:8188`
   - 主模型：`minimax_h3_fl2va_pruned_int8_convrot.safetensors` + `pika_h3_flex_fl2va_v1_bf16.safetensors`（6 步 LCM）
   - 文本編碼器：`qwen3vl_32b_minimax_h3_int8_convrot.safetensors`（`CLIPLoader` type=`minimax`）
   - VAE：`minimax_h3_video_vae_int8_convrot.safetensors` 與 `minimax_h3_audio_vae_fp32.safetensors`
2. **解析度與兩段式精修（無放大）**：
   - **快速預覽（`--quality low`）**：直接以 `672×384`、`243 frames` 單段採樣（`ManualSigmas`: `1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0`），快速確認動作與運鏡。
   - **正式輸出（`--quality high`，768p）**：直接以 `1344×768`、`243 frames` 跑第一段採樣後，**不做放大**，直接把潛空間接進第二段 `SamplerCustomAdvanced`（`ManualSigmas`: `0.25, 0.125, 0.0`，即 `denoise 0.25` 兩步精修）提升皮膚質感與五官細節。
   - **任意幕重跑**：腳本應支援 `--scene N`、`--from-scene N --to-scene M --merge-all`、`--merge-only`，並將每幕最後一幀存為無損 PNG 供下一幕 `first_frame` 接續。
3. **語音辨識對齊字幕與合併輸出**：
   - 合併段落時重新編碼音視訊以防止跨段時間軸漂移。
   - 使用 **`faster-whisper`** 逐段對實際生成音軌執行語音辨識取得真實台詞與精確時間軸（開啟低門檻 VAD `threshold=0.25` ＋ `condition_on_previous_text=False`，**不可將整串台詞塞進 `initial_prompt`** 以免靜音外洩或幻覺；過濾純標點與常見幻覺詞、嚴格夾限 `[0, dur]`，並以 `OpenCC("s2twp")` 轉為繁體中文／修正專有名詞同音字；若完全無辨識結果再退回該幕腳本台詞）。
   - 腳本保留 `--use-existing-srt` 參數，讓使用者手動微調 `.srt` 後直接壓制、不再重跑 Whisper；並提供 `--bgm` 參數供合併時混入統一連續配樂。
   - `ffmpeg` 輸出維持原生 `1344×768`（不額外升頻或裁切），使用 `-crf 20 -maxrate 8M -bufsize 16M` 控制檔案大小。
