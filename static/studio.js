let CURRENT_PROJECT = null;
let INPUT_IMAGES = [];
let INPUT_AUDIOS = [];

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "API 請求失敗");
  return data;
}

function toggleHelper() {
  document.getElementById("helperBox").classList.toggle("open");
}

async function copyAntigravityPrompt() {
  const novel = document.getElementById("novelInput").value.trim();
  const fname = document.getElementById("targetMdName").value.trim() || "drama_script.md";
  if (!novel) return alert("請先貼上小說原文");
  const scriptsDir = window.H3_SCRIPTS_DIR || "scripts";
  const text = `請依 h3-drama-script 技能為以下小說產生完整的 MiniMax H3 分鏡腳本（每幕 10 秒、含繁體中文翻譯與首幀清單），並存成 ${scriptsDir}/${fname}：\n\n${novel}`;
  await navigator.clipboard.writeText(text);
  document.getElementById("copyHint").textContent = "已複製！請貼回 Antigravity 對話框生成，完成後點「重新掃描」即可載入。";
}

async function refreshScripts(autoLoadFirst = false) {
  const data = await api("/api/scripts");
  INPUT_IMAGES = data.input_images;
  INPUT_AUDIOS = data.input_audios;

  const sel = document.getElementById("scriptSelect");
  const prevVal = sel.value;
  sel.innerHTML = "";
  data.scripts.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s.path;
    opt.textContent = `[${s.dir}] ${s.name}`;
    sel.appendChild(opt);
  });
  if (prevVal && data.scripts.some(s => s.path === prevVal)) sel.value = prevVal;

  const bgmSel = document.getElementById("bgmSelect");
  const prevBgm = bgmSel.value;
  bgmSel.innerHTML = `<option value="">無額外配樂</option>`;
  INPUT_AUDIOS.forEach(a => {
    const opt = document.createElement("option");
    opt.value = a;
    opt.textContent = a;
    bgmSel.appendChild(opt);
  });
  if (prevBgm) bgmSel.value = prevBgm;

  if (autoLoadFirst && data.scripts.length > 0) {
    await loadSelectedScript();
  }
}

async function loadSelectedScript() {
  const mdPath = document.getElementById("scriptSelect").value;
  if (!mdPath) return;
  try {
    CURRENT_PROJECT = await api("/api/project/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ md_path: mdPath }),
    });
    renderScenes();
    updateFinalPreview();
    if (CURRENT_PROJECT.srt_text) {
      document.getElementById("srtEditor").value = CURRENT_PROJECT.srt_text;
    } else {
      document.getElementById("srtEditor").value = "";
    }
    renderDialogueRef();
  } catch (err) {
    alert(`載入腳本失敗：${err.message}`);
  }
}

function renderDialogueRef() {
  const box = document.getElementById("dialogueRefBox");
  if (!box || !CURRENT_PROJECT) return;
  const items = CURRENT_PROJECT.script_dialogues || [];
  box.textContent = items.map(it => {
    const lines = it.dialogues.length ? it.dialogues.join(" / ") : "（無台詞）";
    return `第 ${String(it.idx).padStart(2, "0")} 幕 [${it.time_range}]：${lines}`;
  }).join("\n");
}

function collectSceneEdits() {
  if (!CURRENT_PROJECT) return;
  CURRENT_PROJECT.scenes.forEach(sc => {
    const pEl = document.getElementById(`prompt_${sc.idx}`);
    const sEl = document.getElementById(`seed_${sc.idx}`);
    const mEl = document.getElementById(`mode_${sc.idx}`);
    const iEl = document.getElementById(`inmode_${sc.idx}`);
    const cEl = document.getElementById(`custom_${sc.idx}`);
    if (pEl) sc.prompt = pEl.value;
    if (sEl) sc.seed = parseInt(sEl.value || "500100", 10);
    if (mEl) sc.node_mode = mEl.value;
    if (iEl) sc.input_mode = iEl.value;
    if (cEl) sc.custom_image = cEl.value;
  });
}

async function saveProject() {
  if (!CURRENT_PROJECT) return;
  collectSceneEdits();
  CURRENT_PROJECT = await api("/api/project/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(CURRENT_PROJECT),
  });
  renderScenes();
}

function renderScenes() {
  const grid = document.getElementById("scenesGrid");
  grid.innerHTML = "";
  const q = document.getElementById("qualitySelect").value;

  CURRENT_PROJECT.scenes.forEach(sc => {
    const card = document.createElement("div");
    card.className = "scene-card";

    const imgOptions = [`<option value="">-- 選擇 input 圖片 --</option>`]
      .concat(INPUT_IMAGES.map(fn => `<option value="${fn}" ${sc.custom_image === fn ? "selected" : ""}>${fn}</option>`))
      .join("");

    const previewFile = sc.input_previews ? sc.input_previews[q] : null;
    const previewHtml = previewFile
      ? `<img src="/files/input/${encodeURIComponent(previewFile)}?t=${Date.now()}" alt="preview" />`
      : `<div class="placeholder">${sc.input_mode === "prev_last" && sc.idx > 1 ? `等待第 ${sc.idx - 1} 幕 (${q}) 生成尾幀...` : "尚未選擇首幀圖片"}</div>`;

    const lowVid = sc.videos?.low;
    const highVid = sc.videos?.high;
    const activeVid = q === "high" ? (highVid || lowVid) : (lowVid || highVid);
    const vidHtml = activeVid
      ? `<video controls src="/files/output/${encodeURIComponent(activeVid)}?t=${Date.now()}"></video>
         <div style="font-size:11px; color:var(--muted); margin-top:4px;">目前顯示: ${activeVid} ${lowVid && highVid ? "(Low/High 皆有)" : ""}</div>`
      : `<div class="placeholder">尚未生成影片</div>`;

    card.innerHTML = `
      <div class="scene-header">
        <span>${sc.title}</span>
        <div class="row">
          <button onclick="runRange(${sc.idx}, ${sc.idx})" class="primary">跑這幕</button>
          <button onclick="runRange(${sc.idx}, ${CURRENT_PROJECT.scenes.length})">從這幕往後</button>
        </div>
      </div>

      <!-- 最上方：提示詞與 Seed -->
      <div class="block">
        <div class="block-label">
          <span>1. 提示詞 (Prompt) & Seed</span>
          <div class="row">
            <input id="seed_${sc.idx}" type="number" value="${sc.seed}" style="width:110px; padding:3px 6px;" />
            <button onclick="randomizeSeed(${sc.idx})" title="隨機 Seed">🎲</button>
          </div>
        </div>
        <textarea id="prompt_${sc.idx}" class="prompt-box">${escapeHtml(sc.prompt)}</textarea>
        ${sc.zh_translation ? `<div class="zh-trans">${escapeHtml(sc.zh_translation)}</div>` : ""}
      </div>

      <!-- 中間：輸入首幀或沿用前一幕尾幀 -->
      <div class="block">
        <div class="block-label">
          <span>2. 輸入畫面來源</span>
          <div class="row">
            ${previewFile ? `<button onclick="enhancePreview('${escapeHtml(previewFile)}')" style="font-size:11px; padding:3px 7px;" title="使用 RealESRGAN 提亮並銳化這張輸入圖">✨ 提亮銳化</button>` : ""}
            <select id="mode_${sc.idx}" onchange="onFieldChange(${sc.idx})">
              <option value="i2v" ${sc.node_mode === "i2v" ? "selected" : ""}>I2V (單圖鎖定)</option>
              <option value="ref2v" ${sc.node_mode === "ref2v" ? "selected" : ""}>Ref2V (參考模式)</option>
            </select>
          </div>
        </div>
        ${sc.recommend_text ? `<div style="font-size:11px; color:#8ab4ff; margin-bottom:6px;">💡 ${escapeHtml(sc.recommend_text)}</div>` : ""}
        <div class="row" style="margin-bottom:6px;">
          <select id="inmode_${sc.idx}" onchange="onFieldChange(${sc.idx})">
            <option value="prev_last" ${sc.input_mode === "prev_last" ? "selected" : ""} ${sc.idx === 1 ? "disabled" : ""}>沿用第 ${Math.max(1, sc.idx - 1)} 幕無損尾幀</option>
            <option value="custom" ${sc.input_mode === "custom" ? "selected" : ""}>使用額外自訂首幀</option>
          </select>
          <select id="custom_${sc.idx}" style="flex:1; ${sc.input_mode === "prev_last" ? "display:none;" : ""}" onchange="onFieldChange(${sc.idx})">${imgOptions}</select>
          <label style="cursor:pointer; font-size:12px; padding:4px 8px; border:1px solid var(--border); border-radius:6px; background:#263044; ${sc.input_mode === "prev_last" ? "display:none;" : ""}">
            上傳圖片
            <input type="file" accept="image/*" style="display:none;" onchange="uploadCustomImg(${sc.idx}, this)" />
          </label>
        </div>
        <div id="preview_${sc.idx}" class="preview-img-wrap">${previewHtml}</div>
      </div>

      <!-- 最下方：該幕影片輸出 -->
      <div class="block">
        <div class="block-label">
          <span>3. 本幕生成結果</span>
          <span>${highVid ? "✅ High" : ""} ${lowVid ? "✅ Low" : ""}</span>
        </div>
        ${vidHtml}
      </div>
    `;
    grid.appendChild(card);
  });
}

async function onFieldChange(idx) {
  await saveProject();
}

async function enhancePreview(filename) {
  const quality = document.getElementById("qualitySelect").value;
  const aspect = document.getElementById("aspectSelect").value;
  await api("/api/image/enhance", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, quality, aspect }),
  });
  renderScenes();
}

function randomizeSeed(idx) {
  document.getElementById(`seed_${idx}`).value = Math.floor(100000000 + Math.random() * 900000000);
  saveProject();
}

async function uploadCustomImg(idx, inputEl) {
  const file = inputEl.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload/image", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) return alert(data.error || "上傳失敗");
  await refreshScripts(false);
  const sc = CURRENT_PROJECT.scenes.find(s => s.idx === idx);
  if (sc) {
    sc.input_mode = "custom";
    sc.custom_image = data.filename;
  }
  await saveProject();
}

async function runRange(fromIdx, toIdx) {
  await saveProject();
  const quality = document.getElementById("qualitySelect").value;
  const aspect = document.getElementById("aspectSelect").value;
  await api("/api/scene/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ md_path: CURRENT_PROJECT.md_path, from_idx: fromIdx, to_idx: toIdx, quality, aspect }),
  });
}

function runAll() {
  if (!CURRENT_PROJECT) return;
  runRange(1, CURRENT_PROJECT.scenes.length);
}

async function cancelAll() {
  await api("/api/scene/cancel", { method: "POST" });
}

async function withLoading(btnId, busyText, statusMsg, fn) {
  const btn = document.getElementById(btnId);
  const st = document.getElementById("postStatus");
  const origText = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = "0.6";
    btn.textContent = busyText;
  }
  if (st) {
    st.style.color = "#ffd166";
    st.textContent = `⏳ ${statusMsg}`;
  }
  try {
    await fn();
    if (st) {
      st.style.color = "#2ecc71";
      st.textContent = `✅ 完成：${statusMsg}`;
    }
  } catch (err) {
    if (st) {
      st.style.color = "#e74c3c";
      st.textContent = `❌ 失敗：${err.message}`;
    }
    alert(err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = "1";
      btn.textContent = origText;
    }
  }
}

async function mergeClips() {
  if (!CURRENT_PROJECT) return;
  const prefer = document.getElementById("mergePrefer").value;
  await withLoading("btnMerge", "⏳ 合併中...", "正在合併全部段落（重新編碼對齊時間軸）...", async () => {
    const data = await api("/api/postprocess/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ md_path: CURRENT_PROJECT.md_path, prefer }),
    });
    CURRENT_PROJECT.merged_video = data.merged_video;
    updateFinalPreview();
  });
}

async function runAsr() {
  if (!CURRENT_PROJECT) return;
  const prefer = document.getElementById("mergePrefer").value;
  await withLoading("btnAsr", "⏳ 辨識中...", "正在逐幕執行 Whisper 語音辨識（約需 20～60 秒）...", async () => {
    const data = await api("/api/postprocess/asr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ md_path: CURRENT_PROJECT.md_path, prefer }),
    });
    document.getElementById("srtEditor").value = data.srt_text;
  });
}

async function saveSrt() {
  if (!CURRENT_PROJECT) return;
  const srt_text = document.getElementById("srtEditor").value;
  await withLoading("btnSaveSrt", "⏳ 儲存中...", "正在儲存字幕檔 .srt...", async () => {
    const data = await api("/api/postprocess/save_srt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ md_path: CURRENT_PROJECT.md_path, srt_text }),
    });
    CURRENT_PROJECT.srt_text = srt_text;
  });
}

async function burnSubtitles() {
  if (!CURRENT_PROJECT) return;
  const srt_text = document.getElementById("srtEditor").value;
  const bgm_name = document.getElementById("bgmSelect").value;
  await withLoading("btnBurn", "⏳ 壓制中...", "正在將字幕與配樂壓制成片...", async () => {
    const data = await api("/api/postprocess/burn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ md_path: CURRENT_PROJECT.md_path, srt_text, bgm_name }),
    });
    CURRENT_PROJECT.final_sub_video = data.final_sub_video;
    updateFinalPreview();
  });
}

function updateFinalPreview() {
  if (!CURRENT_PROJECT) return;
  const vname = CURRENT_PROJECT.final_sub_video || CURRENT_PROJECT.merged_video || "";
  document.getElementById("finalVideoName").value = vname;
  if (vname) {
    document.getElementById("finalVideo").src = `/files/output/${encodeURIComponent(vname)}?t=${Date.now()}`;
  }
}

async function sendTailscale() {
  const filename = document.getElementById("finalVideoName").value.trim();
  const target = document.getElementById("tailscaleTarget").value.trim();
  if (!filename) return alert("尚無可傳送的影片");
  if (!target) return alert("請先輸入 Tailscale 目標裝置名稱");
  await withLoading("btnTailscale", "⏳ 傳送中...", `正在透過 Tailscale 傳送至 ${target}...`, async () => {
    await api("/api/share/tailscale", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, target }),
    });
  });
}

function escapeHtml(str) {
  return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

let LAST_BUSY = false;
let LAST_TASK = null;
async function pollStatus() {
  try {
    const md = CURRENT_PROJECT ? encodeURIComponent(CURRENT_PROJECT.md_path) : "";
    const st = await api(`/api/status?md_path=${md}`);
    const pill = document.getElementById("statusPill");
    if (st.busy) {
      pill.className = "status-pill busy";
      pill.textContent = `執行中：${st.current_task || ""} (排隊 ${st.queued.length})`;
    } else {
      pill.className = "status-pill";
      pill.textContent = st.last_error ? `錯誤：${st.last_error}` : "待機中";
    }
    document.getElementById("logBox").textContent = (st.logs || []).join("\n") || "等待操作...";

    // Refresh cards both when a task finishes and when the active shot advances (so prev_last previews show while running)
    if (st.project && ((LAST_BUSY && !st.busy) || (st.current_task && st.current_task !== LAST_TASK))) {
      CURRENT_PROJECT = st.project;
      renderScenes();
    }
    LAST_BUSY = st.busy;
    LAST_TASK = st.current_task;
  } catch (e) {}
}

document.getElementById("qualitySelect").addEventListener("change", () => {
  if (CURRENT_PROJECT) renderScenes();
});

refreshScripts(true);
setInterval(pollStatus, 3000);
