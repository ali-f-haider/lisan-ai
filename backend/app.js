const EMOTIONS = ["neutral","happy","sad","angry","fearful","surprised","disgusted","shouting","whispering","screaming","yelling","crying","laughing","sarcastic","seductive","narrative","announcer","conversational","depressed","anxious","confident","indifferent","excited","serious","playful","terrified","relieved","thoughtful","mocking","pleading","commanding"];
const CREDIT_USD = 0.01;
const GEMINI_IN_PER_M = 0.30, GEMINI_OUT_PER_M = 2.50;
const AUDIO_TOKENS_PER_SEC = 258;
const VOICE_USD_PER_1K_CHARS = 0.18;
const GEMINI_TEXT_MODELS = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"];
const COIN_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v9"/><path d="M14.5 9.5c0-1-1.1-1.6-2.5-1.6s-2.5.6-2.5 1.6 1.1 1.6 2.5 1.6 2.5.6 2.5 1.6-1.1 1.6-2.5 1.6-2.5-.6-2.5-1.6"/></svg>`;
const NOTIFY_AUTO_CLOSE_MS = 10000;

let segmentsData = [], originalSegments = [];
let speakerVoices = {}, speakerVoiceNames = {};
let speakerChoices = {}, clonedBySpeaker = {};
let voicePools = { male: [], female: [] };
let availableVoices = [];
let segmentOffsets = {};
let currentJobId = null, totalDuration = 0, isVideoUpload = false;
let transcribePollTimer = null, generatePollTimer = null, emotionPollTimer = null;
let previewAudio = null, previewBtnCurrent = null;
window.clonedVoiceIds = [];

function friendly(msg) {
    msg = String(msg || "");
    if (/voice_not_found|was not found/i.test(msg)) {
        return "🎙️ The voice assigned to this speaker no longer exists in your connected voice account (old cloned voices were removed). Re-clone it in Step 3.5 or pick a numbered library voice in Step 4, then try again.";
    }
    if (/gaierror|name resolution|URLError|ConnectionError|Connection aborted|Max retries exceeded|EOF occurred|SSLError|SSL|Network is unreachable|Connection refused|WinError 100|fetch failed|Load failed|Response ended prematurely/i.test(msg)) {
        return "🌐 Connection problem: no internet or the service is unreachable. Check your connection and try again.";
    }
    return msg;
}
function notify(type, msg) {
    const panel = document.getElementById("notifyPanel");
    const div = document.createElement("div");
    div.className = "notify " + type;
    const m = document.createElement("div");
    m.className = "msg";
    m.textContent = (type === "error") ? friendly(msg) : msg;
    const ok = document.createElement("button");
    ok.textContent = "OK";
    ok.onclick = () => div.remove();
    div.appendChild(m); div.appendChild(ok);
    panel.appendChild(div);
    while (panel.children.length > 6) panel.removeChild(panel.firstChild);
    setTimeout(() => { if (div.parentNode) div.remove(); }, NOTIFY_AUTO_CLOSE_MS);
}

function usdToCredits(usd) { return Math.max(0, Math.ceil(usd / CREDIT_USD)); }
function lineCostUsd(text, emotion) { return ((text || "").length + (emotion || "").length + 3) / 1000 * VOICE_USD_PER_1K_CHARS; }
function translateEstimateUsd() {
    const payloadChars = JSON.stringify(segmentsData.map(s => ({ t: s.text, d: Number((s.end - s.start).toFixed(2)) }))).length + 700;
    const tokIn = Math.ceil(payloadChars / 4);
    const tokOut = Math.ceil(segmentsData.reduce((a, s) => a + s.arabic_text.length, 0) / 3) + 2000;
    return tokIn / 1e6 * GEMINI_IN_PER_M + tokOut / 1e6 * GEMINI_OUT_PER_M;
}
function tashkeelEstimateUsd() {
    const chars = segmentsData.reduce((a, s) => a + (s.arabic_text || "").length, 0);
    const tok = Math.ceil(chars * 3) + 400;
    return tok / 1e6 * GEMINI_OUT_PER_M;
}
function emotionsEstimateUsd() {
    const sec = segmentsData.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
    const tok = Math.ceil(sec * AUDIO_TOKENS_PER_SEC) + segmentsData.length * 100;
    return tok / 1e6 * GEMINI_IN_PER_M + Math.ceil(tok * 0.1) / 1e6 * GEMINI_OUT_PER_M;
}
function generateEstimateUsd() {
    return segmentsData.reduce((a, s) => a + lineCostUsd(s.arabic_text, s.emotion), 0);
}
function setBadge(id, credits) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `${COIN_SVG}<strong>${credits}</strong>`;
}
function updateBadges() {
    setBadge("badgeTranscribe", 0);
    setBadge("badgeImport", 0);
    setBadge("badgeSRT", 0);
    setBadge("badgeSBV", 0);
    setBadge("badgeSave", 0);
    setBadge("badgeLoad", 0);
    setBadge("badgeTranslate", usdToCredits(translateEstimateUsd()));
    setBadge("badgeTashkeel", usdToCredits(tashkeelEstimateUsd()));
    setBadge("badgeEmotions", usdToCredits(emotionsEstimateUsd()));
    setBadge("badgeAutoFix", 0);
    setBadge("badgeAutoAssign", 0);
    setBadge("badgePrepareClone", 0);
    setBadge("badgeClone", 0);
    setBadge("badgeGenerate", usdToCredits(generateEstimateUsd()));
    setBadge("badgeMerge", 0);
}
async function fetchUsage() {
    const box = document.getElementById("usageBox");
    if (!box) return;
    if (!currentJobId) { box.classList.add("hidden"); return; }
    try {
        const res = await fetch(`/api/usage/${currentJobId}`);
        if (!res.ok) throw new Error("na");
        const u = await res.json();
        const voiceUsd = (u.eleven_credits || 0) / 1000 * VOICE_USD_PER_1K_CHARS;
        const gemUsd = u.gemini_cost_usd || 0;
        const totalUsd = voiceUsd + gemUsd;
        box.classList.remove("hidden");
        box.innerHTML = `<strong>Actual usage this job:</strong> ≈ <strong>${usdToCredits(totalUsd)} credits</strong> (= $${totalUsd.toFixed(4)}; 1 credit = $0.01).<br>` +
            `Translation & analysis: ${(u.gemini_in_tokens || 0).toLocaleString()} in / ${(u.gemini_out_billable || 0).toLocaleString()} out tokens.<br>` +
            `Voice generation: ${(u.eleven_credits || 0).toLocaleString()} characters.`;
    } catch (e) { box.classList.add("hidden"); }
}

async function geminiTextCall(key, prompt) {
    let lastErr = "";
    for (const m of GEMINI_TEXT_MODELS) {
        try {
            const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${key}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: prompt }] }],
                    generationConfig: { temperature: 0.2, responseMimeType: "application/json" }
                })
            });
            if (!res.ok) { lastErr = `HTTP ${res.status}`; continue; }
            const data = await res.json();
            const txt = data && data.candidates && data.candidates[0] && data.candidates[0].content
                && data.candidates[0].content.parts && data.candidates[0].content.parts[0]
                ? data.candidates[0].content.parts[0].text : null;
            if (txt) return txt;
            lastErr = "empty response";
        } catch (e) { lastErr = e.message; }
    }
    throw new Error(lastErr || "Gemini call failed");
}
function stripFences(t) {
    t = String(t).trim();
    if (t.startsWith("```")) t = t.split("\n").slice(1).join("\n");
    if (t.endsWith("```")) t = t.replace(/```$/, "");
    return t.trim();
}
async function addTashkeel() {
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!geminiKey) { notify("error", "Enter the Translation AI key in Step 2 first."); return; }
    const targets = segmentsData.filter(s => (s.arabic_text || "").trim().length > 0 && !s.locked);
    if (!targets.length) { notify("error", "No unlocked Arabic text found. Locked lines are skipped."); return; }
    notify("info", "Adding tashkeel to " + targets.length + " unlocked line(s)...");
    const items = targets.map(s => ({ segment_id: s.segment_id, arabic_text: s.arabic_text }));
    const prompt = "You are an Arabic diacritization (tashkeel) engine.\nAdd full, correct Arabic tashkeel (harakat) to each text below.\nSTRICT RULES:\n- Do NOT translate.\n- Do NOT change, add, remove, or reorder any words.\n- Keep punctuation exactly as is.\n- Return ONLY valid JSON array: [{\"segment_id\": \"...\", \"arabic_text\": \"...\"}]\nTexts:\n" + JSON.stringify(items, null, 1);
    try {
        const txt = await geminiTextCall(geminiKey, prompt);
        const arr = JSON.parse(stripFences(txt));
        let done = 0;
        (Array.isArray(arr) ? arr : []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg && item.arabic_text) { seg.arabic_text = item.arabic_text; done++; }
        });
        renderTable();
        notify("success", "Tashkeel added to " + done + " unlocked line(s). Locked lines untouched.");
    } catch (e) {
        notify("error", "Tashkeel failed: " + e.message);
    }
}

function stopPreview() {
    if (previewAudio) { try { previewAudio.pause(); } catch (e) {} previewAudio = null; }
    if (previewBtnCurrent) { previewBtnCurrent.textContent = "▶"; previewBtnCurrent = null; }
}
function previewRow(i, btn) {
    if (!currentJobId) { notify("error", "Transcribe or load a project first."); return; }
    if (previewAudio && previewBtnCurrent === btn) { stopPreview(); return; }
    stopPreview();
    const seg = segmentsData[i];
    let playStart = seg.start, playEnd = seg.end;
    if (!seg.locked && originalSegments.length > 0) {
        const toks = normalizeTokens(seg.text);
        if (toks.length > 0) {
            const cands = findCandidates(toks, buildTokenTimeline(), seg.start);
            if (cands.length > 0) {
                playStart = cands[0].start; playEnd = cands[0].end;
                if (Math.abs(playStart - seg.start) > 0.3 || Math.abs(playEnd - seg.end) > 0.3)
                    notify("info", `Preview plays matched audio ${playStart.toFixed(2)}–${playEnd.toFixed(2)} (row shows ${seg.start}–${seg.end}). Run ✨ Auto-Fix to correct the row.`);
            }
        }
    }
    const a = new Audio(`/api/source/${currentJobId}`);
    previewAudio = a; previewBtnCurrent = btn; btn.textContent = "⏸";
    const startPlaying = () => { try { a.currentTime = Math.max(0, playStart); } catch (e) {} a.play().catch(err => { notify("error", "Preview playback failed: " + err.message); stopPreview(); }); };
    if (a.readyState >= 1) startPlaying(); else a.addEventListener("loadedmetadata", startPlaying, { once: true });
    a.addEventListener("timeupdate", () => { if (a.currentTime >= playEnd) stopPreview(); });
    a.addEventListener("error", () => { notify("error", "Preview unavailable: source audio not found."); stopPreview(); }, { once: true });
}

function secFromStamp(t) { const m = String(t).trim().match(/(\d+):(\d+):(\d+)[,.](\d+)/); if (!m) return 0; return parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 1000; }
function parseSRT(text) { const cues = []; text.replace(/\r/g, "").split(/\n\s*\n/).forEach(b => { const lines = b.split("\n").filter(l => l.trim() !== ""); if (lines.length < 2) return; const ti = lines.findIndex(l => l.includes("-->")); if (ti < 0) return; const p = lines[ti].split("-->"); if (p.length < 2) return; cues.push({ start: secFromStamp(p[0]), end: secFromStamp(p[1]), text: lines.slice(ti + 1).join(" ") }); }); return cues; }
function parseSBV(text) { const cues = []; text.replace(/\r/g, "").split(/\n\s*\n/).forEach(b => { const lines = b.split("\n").filter(l => l.trim() !== ""); if (lines.length < 2) return; const m = lines[0].match(/^([^,]+),([^,]+)$/); if (!m) return; cues.push({ start: secFromStamp(m[1]), end: secFromStamp(m[2]), text: lines.slice(1).join(" ") }); }); return cues; }
function importSubs(evt) {
    const f = evt.target.files[0]; if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
        const cues = f.name.toLowerCase().endsWith(".sbv") ? parseSBV(String(reader.result)) : parseSRT(String(reader.result));
        if (!cues.length) { notify("error", "No cues found in subtitle file."); return; }
        let matched = 0, added = 0;
        cues.forEach((cue, ci) => {
            const isAr = /[\u0600-\u06FF]/.test(cue.text);
            let best = null, bestOv = 0;
            segmentsData.forEach(s => { const ov = Math.min(s.end, cue.end) - Math.max(s.start, cue.start); if (ov > bestOv) { bestOv = ov; best = s; } });
            if (best && bestOv > 0.3 * (cue.end - cue.start)) { if (isAr) best.arabic_text = cue.text; else best.text = cue.text; matched++; }
            else { segmentsData.push({ segment_id: "imp_" + Date.now() + "_" + ci, start: Number(cue.start.toFixed(2)), end: Number(cue.end.toFixed(2)), speaker: "Speaker 1", gender: "male", emotion: "neutral", text: isAr ? "" : cue.text, arabic_text: isAr ? cue.text : "", locked: false }); added++; }
        });
        segmentsData.sort((a, b) => a.start - b.start);
        renderTable();
        notify("success", `Subtitle import: ${matched} line(s) updated, ${added} line(s) added.`);
    };
    reader.readAsText(f); evt.target.value = "";
}
function fmtSRT(sec) { sec = Math.max(0, sec); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60), ms = Math.round((sec - Math.floor(sec)) * 1000); return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`; }
function fmtSBV(sec) { sec = Math.max(0, sec); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60), ms = Math.round((sec - Math.floor(sec)) * 1000); return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`; }
function downloadText(filename, text) { const blob = new Blob([text], { type: "text/plain;charset=utf-8" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(a.href); }
function exportSRT() { const rows = segmentsData.slice().sort((a, b) => a.start - b.start); let out = "", n = 0; rows.forEach(s => { const t = (s.arabic_text || "").trim() || (s.text || "").trim(); if (!t) return; n++; out += `${n}\n${fmtSRT(s.start)} --> ${fmtSRT(s.end)}\n${t}\n\n`; }); if (!n) { notify("error", "Nothing to export."); return; } downloadText("dubbed_subtitles.srt", out); notify("success", "SRT exported."); }
function exportSBV() { const rows = segmentsData.slice().sort((a, b) => a.start - b.start); let out = "", n = 0; rows.forEach(s => { const t = (s.arabic_text || "").trim() || (s.text || "").trim(); if (!t) return; n++; out += `${fmtSBV(s.start)},${fmtSBV(s.end)}\n${t}\n\n`; }); if (!n) { notify("error", "Nothing to export."); return; } downloadText("dubbed_subtitles.sbv", out); notify("success", "SBV exported."); }

function saveProject() {
    downloadText("dubbing_project.json", JSON.stringify({
        app: "ai-dubbing-mvp", version: 1, job_id: currentJobId, total_duration: totalDuration, is_video: isVideoUpload,
        segments: segmentsData, original_segments: originalSegments,
        speaker_voices: speakerVoices, speaker_voice_names: speakerVoiceNames,
        speaker_choices: speakerChoices, cloned_by_speaker: clonedBySpeaker
    }, null, 2));
    notify("success", "Project saved to JSON.");
}
function loadProjectFile(evt) {
    const f = evt.target.files[0]; if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
        try {
            const p = JSON.parse(String(reader.result));
            if (!p || !Array.isArray(p.segments)) throw new Error("Bad project file");
            segmentsData = p.segments.map(s => ({ segment_id: s.segment_id || ("seg_" + Math.random().toString(36).slice(2, 8)), start: Number(s.start) || 0, end: Number(s.end) || 0, speaker: s.speaker || "Speaker 1", gender: s.gender || "male", emotion: s.emotion || "neutral", text: s.text || "", arabic_text: s.arabic_text || "", locked: !!s.locked, words: Array.isArray(s.words) ? s.words : [] }));
            originalSegments = Array.isArray(p.original_segments) ? p.original_segments : [];
            speakerVoices = p.speaker_voices || {};
            speakerVoiceNames = p.speaker_voice_names || {};
            speakerChoices = p.speaker_choices || {};
            clonedBySpeaker = p.cloned_by_speaker || {};
            if (p.job_id) currentJobId = p.job_id;
            if (Number(p.total_duration) > 0) totalDuration = Number(p.total_duration);
            isVideoUpload = !!p.is_video;
            renderTable(); renderSpeakerVoices();
            ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(id => document.getElementById(id).classList.remove("hidden"));
            notify("success", "Project loaded.");
            fetchUsage(); updateBadges();
        } catch (e) { notify("error", "Load failed: " + e.message); }
    };
    reader.readAsText(f); evt.target.value = "";
}

async function startTranscribe() {
    const file = document.getElementById("audioFile").files[0];
    if (!file) { notify("error", "Choose an audio or video file."); return; }
    const form = new FormData();
    form.append("file", file);
    form.append("speaker_count", document.getElementById("speakerCount").value);
    form.append("hf_token", document.getElementById("hfToken").value.trim());
    document.getElementById("progressSection").classList.remove("hidden");
    notify("info", "Uploading and starting transcription...");
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await res.json();
    currentJobId = data.job_id; originalSegments = [];
    speakerVoices = {}; speakerVoiceNames = {}; speakerChoices = {}; clonedBySpeaker = {};
    voicePools = { male: [], female: [] };
    if (transcribePollTimer) clearInterval(transcribePollTimer);
    transcribePollTimer = setInterval(checkTranscribeProgress, 1000);
}
async function checkTranscribeProgress() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/${currentJobId}`);
    const data = await res.json();
    const fill = document.getElementById("progressFill");
    fill.style.width = data.percent + "%";
    fill.textContent = data.percent + "%" + (data.status_text ? " — " + data.status_text : "");
    if (data.is_video !== undefined) isVideoUpload = data.is_video;
    if (data.status === "done") {
        clearInterval(transcribePollTimer);
        segmentsData = data.segments;
        segmentsData.forEach(s => { s.start = Number(Number(s.start).toFixed(2)); s.end = Number(Number(s.end).toFixed(2)); s.locked = false; });
        originalSegments = JSON.parse(JSON.stringify(segmentsData));
        totalDuration = data.full_duration || 0;
        let message = "Transcription complete.";
        if (data.detected_speakers > 0) message += ` Detected speakers: ${data.detected_speakers}.`;
        if (data.warning) notify("error", "⚠️ " + data.warning);
        notify("success", message + " Use 🔒 to protect lines from Auto-Fix, Translate and Tashkeel.");
        renderTable(); renderSpeakerVoices();
        ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(id => document.getElementById(id).classList.remove("hidden"));
        updateBadges(); fetchUsage();
    }
    if (data.status === "error") { clearInterval(transcribePollTimer); notify("error", data.error); }
}

function renderTable() {
    const tbody = document.querySelector("#segmentsTable tbody");
    tbody.innerHTML = "";
    segmentsData.forEach((seg, i) => tbody.appendChild(createRow(seg, i)));
    updateBadges();
}
function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    const mk = (tag) => document.createElement(tag);
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; si.value = seg.end; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);
    const eCell = mk("td"); const eS = mk("select"); EMOTIONS.forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.emotion === v); eS.appendChild(o); }); eS.onchange = () => { segmentsData[i].emotion = eS.value; updateBadges(); }; eCell.appendChild(eS); row.appendChild(eCell);
    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio for this line"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only (current Arabic text, emotion & voice), then rebuild the mix"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line from Auto-Fix, Translate and Tashkeel"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}
function toggleLock(i) {
    segmentsData[i].locked = !segmentsData[i].locked;
    renderTable();
    notify("info", segmentsData[i].locked ? "Line locked: Auto-Fix, Translate and Tashkeel will skip it." : "Line unlocked.");
}
function insertSegmentAfter(i) {
    const cur = segmentsData[i]; if (!cur) return;
    const next = segmentsData[i + 1];
    let start = cur.end, end = start + 3;
    if (next && next.start > start + 0.5) end = next.start;
    segmentsData.splice(i + 1, 0, { segment_id: "manual_" + Date.now(), start: Number(start.toFixed(2)), end: Number(end.toFixed(2)), speaker: cur.speaker, gender: cur.gender, emotion: cur.emotion, text: "", arabic_text: "", locked: false });
    renderTable(); renderSpeakerVoices();
    notify("info", "Manual line inserted. Use ✨ Auto-Fix to sync its time.");
}
function deleteSegment(i) { if (!confirm("Delete this segment?")) return; segmentsData.splice(i, 1); cleanUnusedSpeakerVoices(); renderTable(); renderSpeakerVoices(); }
function cleanUnusedSpeakerVoices() {
    const active = new Set(segmentsData.map(s => s.speaker));
    Object.keys(speakerVoices).forEach(n => { if (!active.has(n)) { delete speakerVoices[n]; delete speakerVoiceNames[n]; delete speakerChoices[n]; delete clonedBySpeaker[n]; } });
}
function updateSpeakerName(i, newName) {
    newName = newName.trim() || `Speaker ${i + 1}`;
    const old = segmentsData[i].speaker;
    segmentsData[i].speaker = newName;
    if (old !== newName) {
        if (speakerVoices[old] && !speakerVoices[newName]) { speakerVoices[newName] = speakerVoices[old]; speakerVoiceNames[newName] = speakerVoiceNames[old]; speakerChoices[newName] = speakerChoices[old]; clonedBySpeaker[newName] = clonedBySpeaker[old]; }
    }
    cleanUnusedSpeakerVoices(); renderSpeakerVoices();
}

function buildVoicePools(voices) {
    voicePools.male = voices.filter(v => (v.gender || "").toLowerCase() === "male").sort((a, b) => a.voice_id.localeCompare(b.voice_id));
    voicePools.female = voices.filter(v => (v.gender || "").toLowerCase() === "female").sort((a, b) => a.voice_id.localeCompare(b.voice_id));
}
async function ensureVoicePools() {
    if (voicePools.male.length || voicePools.female.length) return true;
    const key = (document.getElementById("apiKey").value || "").trim();
    if (!key) return false;
    try {
        const res = await fetch("/api/voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: key }) });
        const data = await res.json();
        if (data.error || !data.voices) return false;
        availableVoices = data.voices;
        buildVoicePools(availableVoices);
        return true;
    } catch (e) { return false; }
}
async function loadVoiceOptions() {
    const ok = await ensureVoicePools();
    if (ok) notify("success", "Voice options loaded. Pick a voice per speaker below.");
    else notify("error", "Could not load voices. Enter the Voice Engine API key in Step 3 first.");
    renderSpeakerVoices();
}
function applyChoice(name) {
    const ch = speakerChoices[name] || "";
    if (ch === "clone") {
        if (clonedBySpeaker[name]) { speakerVoices[name] = clonedBySpeaker[name]; speakerVoiceNames[name] = "🎙️ Cloned voice"; return; }
    }
    const m = ch.match(/^(male|female):(\d+)$/);
    if (!m) return;
    const g = m[1];
    const pool = voicePools[g] || [];
    if (!pool.length) return;
    let idx = parseInt(m[2], 10) - 1;
    if (idx >= pool.length) idx = pool.length - 1;
    const usedByOthers = new Set();
    Object.keys(speakerVoices).forEach(s => { if (s !== name && speakerVoices[s]) usedByOthers.add(speakerVoices[s]); });
    let j = idx;
    if (usedByOthers.has(pool[j].voice_id)) {
        let found = -1;
        for (let k = 0; k < pool.length; k++) { if (!usedByOthers.has(pool[k].voice_id)) { found = k; break; } }
        if (found >= 0) j = found;
    }
    speakerVoices[name] = pool[j].voice_id;
    speakerChoices[name] = g + ":" + (j + 1);
    speakerVoiceNames[name] = (g === "male" ? "🎲 Male voice " : "🎲 Female voice ") + (j + 1);
}
async function renderSpeakerVoices() {
    const tbody = document.querySelector("#speakerVoicesTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    const hasKey = (document.getElementById("apiKey").value || "").trim().length > 0;
    if (hasKey && !voicePools.male.length && !voicePools.female.length) await ensureVoicePools();
    names.forEach(name => {
        const row = document.createElement("tr");
        const c1 = document.createElement("td"); c1.textContent = name; row.appendChild(c1);
        const c2 = document.createElement("td");
        const sel = document.createElement("select");
        if (clonedBySpeaker[name]) {
            const o = document.createElement("option"); o.value = "clone"; o.textContent = "🎙️ Cloned voice (from video)"; sel.appendChild(o);
        }
        const addGroup = (g, label) => {
            voicePools[g].slice(0, 8).forEach((p, i) => {
                const o = document.createElement("option"); o.value = g + ":" + (i + 1); o.textContent = label + " " + (i + 1); sel.appendChild(o);
            });
        };
        addGroup("male", "🎲 Male voice");
        addGroup("female", "🎲 Female voice");
        if (!clonedBySpeaker[name] && !voicePools.male.length && !voicePools.female.length) {
            const o = document.createElement("option"); o.value = ""; o.textContent = "— Enter Voice Engine key in Step 3, then Load Voice Options —"; sel.appendChild(o);
        }
        sel.value = speakerChoices[name] || "";
        sel.onchange = () => { speakerChoices[name] = sel.value; applyChoice(name); renderSpeakerVoices(); };
        c2.appendChild(sel);
        const info = document.createElement("div"); info.className = "note"; info.textContent = speakerVoiceNames[name] || "";
        c2.appendChild(info);
        row.appendChild(c2);
        tbody.appendChild(row);
    });
}
async function autoAssignVoices() {
    const ok = await ensureVoicePools();
    if (!ok) { notify("error", "Enter the Voice Engine API key in Step 3 first, then try again."); return; }
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    names.forEach(name => {
        if (speakerChoices[name]) { applyChoice(name); return; }
        if (clonedBySpeaker[name]) { speakerChoices[name] = "clone"; applyChoice(name); return; }
        let male = 0, female = 0;
        segmentsData.forEach(s => { if (s.speaker === name) { if (s.gender === "female") female++; else male++; } });
        let g = female > male ? "female" : "male";
        let pool = voicePools[g];
        if (!pool.length) { g = (g === "male" ? "female" : "male"); pool = voicePools[g]; }
        if (!pool.length) return;
        const usedIds = new Set(Object.values(speakerVoices));
        const freeIdx = pool.map((p, i) => i).filter(i => !usedIds.has(pool[i].voice_id));
        const pick = freeIdx.length ? freeIdx[Math.floor(Math.random() * freeIdx.length)] : Math.floor(Math.random() * pool.length);
        speakerChoices[name] = g + ":" + (pick + 1);
        applyChoice(name);
    });
    renderSpeakerVoices();
    notify("success", "Voices auto-assigned. You can change any speaker's voice in the Step 4 table.");
}

function normalizeTokens(t) { return (t || "").toLowerCase().replace(/[^\w\s']/g, "").split(/\s+/).filter(w => w.length > 0); }
function dice(a, b) { if (a === b) return 1; if (a.length < 2 || b.length < 2) return 0; const mA = {}; let nA = 0; for (let i = 0; i < a.length - 1; i++) { const g = a.substr(i, 2); mA[g] = (mA[g] || 0) + 1; nA++; } let nB = 0, hit = 0; for (let i = 0; i < b.length - 1; i++) { const g = b.substr(i, 2); if (mA[g]) { mA[g]--; hit++; } nB++; } return (2 * hit) / (nA + nB); }
function tokenMatch(a, b) { if (a === b) return true; if (a.length < 4 || b.length < 4) return false; return dice(a, b) >= 0.6; }
function buildTokenTimeline() {
    const tl = [];
    originalSegments.slice().sort((a, b) => a.start - b.start).forEach(o => {
        if (o.words && o.words.length > 0) o.words.slice().sort((a, b) => a.start - b.start).forEach(w => normalizeTokens(w.word).forEach(tok => tl.push({ word: tok, tStart: w.start, tEnd: w.end })));
        else { const toks = normalizeTokens(o.text); const n = toks.length; if (!n) return; const span = o.end - o.start; toks.forEach((w, i) => tl.push({ word: w, tStart: o.start + (i / n) * span, tEnd: o.start + ((i + 1) / n) * span })); }
    });
    return tl;
}
function findCandidates(toks, timeline, currentStart) {
    const cands = [];
    const maxAnchor = Math.min(3, toks.length);
    for (let anchor = 0; anchor < maxAnchor; anchor++) {
        for (let p = 0; p < timeline.length; p++) {
            if (!tokenMatch(timeline[p].word, toks[anchor])) continue;
            let ti = anchor, q = p, drops = 0;
            while (q < timeline.length && ti < toks.length) {
                if (tokenMatch(timeline[q].word, toks[ti])) { ti++; q++; }
                else if (drops < 1 && ti + 1 < toks.length && tokenMatch(timeline[q].word, toks[ti + 1])) { ti += 2; drops++; q++; }
                else q++;
            }
            const score = (ti - anchor - drops) / toks.length;
            if (score < 0.6) continue;
            cands.push({ start: timeline[p].tStart, end: timeline[q - 1].tEnd, score });
        }
    }
    cands.sort((a, b) => (b.score - a.score) || (Math.abs(a.start - currentStart) - Math.abs(b.start - currentStart)));
    return cands;
}
function spansOverlap(aS, aE, bS, bE) { const ov = Math.min(aE, bE) - Math.max(aS, bS); return ov > 0 && ov > 0.2 * Math.min(aE - aS, bE - bS); }
function autoFixTiming() {
    if (!segmentsData.length) { notify("error", "No segments to fix."); return; }
    if (!originalSegments.length) { notify("error", "No original transcription available."); return; }
    const timeline = buildTokenTimeline();
    if (!timeline.length) { notify("error", "Original transcription has no words to match."); return; }
    const maxTime = totalDuration > 0 ? totalDuration : timeline[timeline.length - 1].tEnd;
    const pairs = [];
    segmentsData.forEach((seg, idx) => {
        if (seg.locked) return;
        const toks = normalizeTokens(seg.text);
        if (!toks.length) return;
        findCandidates(toks, timeline, seg.start).forEach(c => {
            const cs = Math.max(0, Math.min(c.start, maxTime));
            pairs.push({ idx, start: cs, end: Math.max(cs + 0.3, Math.min(c.end, maxTime)), score: c.score, dist: Math.abs(c.start - seg.start) });
        });
    });
    pairs.sort((a, b) => (b.score - a.score) || (a.dist - b.dist));
    const claimed = []; segmentsData.forEach(seg => { if (seg.locked) claimed.push({ start: seg.start, end: seg.end }); });
    const newTimes = {}; let fixed = 0;
    pairs.forEach(p => {
        if (newTimes[p.idx] !== undefined) return;
        if (claimed.some(c => spansOverlap(p.start, p.end, c.start, c.end))) return;
        newTimes[p.idx] = { start: p.start, end: p.end }; claimed.push({ start: p.start, end: p.end }); fixed++;
    });
    let locked = 0, kept = 0;
    segmentsData.forEach((seg, idx) => {
        if (seg.locked) { locked++; return; }
        if (newTimes[idx]) { seg.start = Number(newTimes[idx].start.toFixed(2)); seg.end = Number(newTimes[idx].end.toFixed(2)); } else kept++;
    });
    segmentsData.sort((a, b) => a.start - b.start);
    const pre = segmentsData.map(s => ({ start: s.start, end: s.end }));
    for (let i = 0; i < segmentsData.length - 1; i++) {
        const a = segmentsData[i], b = segmentsData[i + 1];
        if (a.end > b.start + 0.001) {
            if (a.locked && b.locked) continue;
            if (a.locked) { b.start = Number(a.end.toFixed(2)); if (b.end <= b.start) b.end = Number((b.start + 0.3).toFixed(2)); }
            else { const room = i > 0 ? segmentsData[i - 1].end : 0; if (b.start - 0.3 >= room) { a.end = Number(b.start.toFixed(2)); a.start = Number(Math.max(0, b.start - 0.3).toFixed(2)); } else { a.start = pre[i].start; a.end = pre[i].end; } }
        }
    }
    segmentsData.forEach(seg => { seg.start = Number(Math.max(0, Math.min(seg.start, maxTime)).toFixed(2)); seg.end = Number(Math.max(seg.start + 0.3, Math.min(seg.end, maxTime)).toFixed(2)); });
    renderTable();
    notify("success", `Auto-Fix: ${fixed} line(s) re-synced, ${kept} kept their times, ${locked} locked.`);
}

async function analyzeSpeakers() {
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))].sort();
    const analysis = names.map(name => {
        const segs = segmentsData.filter(s => (s.speaker || "Speaker 1") === name && (s.text || "").trim());
        const total = segs.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
        const total_time = Math.round(total * 10) / 10;
        const n = segs.length;
        let status, message;
        if (total_time < 1.0) {
            status = "bad";
            message = `❌ Cannot clone — only ${total_time}s of speech across ${n} line(s). At least 1 second is required. Use a library voice in Step 4 instead.`;
        } else if (total_time < 3.0) {
            status = "warning";
            message = `⚠️ Barely enough (${total_time}s across ${n} line(s)). The clone will likely sound robotic or unstable. A library voice may sound better.`;
        } else if (total_time < 10.0) {
            status = "warning";
            message = `🟡 Acceptable (${total_time}s across ${n} line(s)). Decent clone, but it may not fully capture the speaker's character.`;
        } else if (total_time < 20.0) {
            status = "good";
            message = `✅ Good (${total_time}s across ${n} line(s)). Enough audio for a natural clone that closely matches the original speaker.`;
        } else {
            status = "good";
            message = `🌟 Excellent (${total_time}s across ${n} line(s)). Best possible clone quality — the voice will sound very close to the original.`;
        }
        return { speaker: name, total_time, num_segments: n, status, message };
    });
    const table = document.getElementById("cloneAnalysisTable");
    const thead = table.querySelector("thead");
    if (thead) thead.innerHTML = `<tr><th style="width:50px">Clone?</th><th style="width:150px">Speaker</th><th style="width:140px">Speech Found</th><th>Quality Guidance</th></tr>`;
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = "";
    analysis.forEach(item => {
        const row = document.createElement("tr");
        const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = item.status !== "bad"; cb.dataset.speaker = item.speaker;
        const c0 = document.createElement("td"); c0.appendChild(cb); row.appendChild(c0);
        const c1 = document.createElement("td"); c1.textContent = item.speaker; row.appendChild(c1);
        const c2 = document.createElement("td"); c2.textContent = `${item.total_time}s (${item.num_segments} line${item.num_segments > 1 ? "s" : ""})`; row.appendChild(c2);
        const c3 = document.createElement("td"); c3.textContent = item.message; c3.style.color = item.status === "good" ? "#2e7d32" : (item.status === "warning" ? "#e65100" : "#c62828"); c3.style.fontSize = "0.9em"; row.appendChild(c3);
        tbody.appendChild(row);
    });
    document.getElementById("cloneAnalysisSection").classList.remove("hidden");
    notify("success", "Review the guidance. Speakers marked ❌ are unchecked automatically.");
}
async function confirmCloning() {
    const apiKey = document.getElementById("apiKey").value.trim();
    if (!apiKey) { notify("error", "Enter the Voice Engine API key in Step 3."); return; }
    if (!currentJobId) { notify("error", "Transcribe first."); return; }
    const selected = [];
    document.querySelectorAll("#cloneAnalysisTable input[type=checkbox]").forEach(cb => { if (cb.checked) selected.push(cb.dataset.speaker); });
    if (!selected.length) { notify("error", "Select at least one speaker to clone."); return; }
    notify("info", `Cloning ${selected.length} voice(s)... this may take a minute.`);
    try {
        const res = await fetch("/api/clone", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId, elevenlabs_api_key: apiKey, segments: segmentsData, speakers_to_clone: selected }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        const cloned = data.cloned_voices || {};
        window.clonedVoiceIds = [];
        let ok = 0;
        Object.keys(cloned).forEach(speaker => {
            const vid = cloned[speaker];
            if (!vid.startsWith("ERROR")) {
                clonedBySpeaker[speaker] = vid;
                speakerChoices[speaker] = "clone";
                window.clonedVoiceIds.push(vid);
                applyChoice(speaker);
                ok++;
            } else notify("error", speaker + ": " + vid);
        });
        renderSpeakerVoices();
        notify("success", `Voices cloned successfully! ${ok} speaker(s) assigned to their cloned voices.`);
        (data.warnings || []).forEach(w => notify("info", "⚠️ " + w));
    } catch (e) { notify("error", e.message); }
}

async function autoTranslate() {
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!geminiKey) { notify("error", "Enter the Translation AI key in Step 2 first."); return; }
    const unlocked = segmentsData.filter(s => !s.locked);
    if (!unlocked.length) { notify("error", "All lines are locked — nothing to translate."); return; }
    notify("info", "Translating " + unlocked.length + " unlocked line(s) to Arabic (locked lines skipped)...");
    try {
        const res = await fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId || "", segments: unlocked, gemini_api_key: geminiKey }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        let matched = 0;
        (data.translated_segments || []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg) { seg.arabic_text = item.arabic_text || ""; if (item.emotion && EMOTIONS.includes(item.emotion)) seg.emotion = item.emotion; matched++; }
        });
        renderTable();
        notify("success", "Translation complete. " + matched + " segments translated. Locked lines untouched.");
        fetchUsage();
    } catch (e) { notify("error", e.message); }
}
async function detectEmotions() {
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!geminiKey) { notify("error", "Enter the Translation AI key in Step 2 first."); return; }
    if (!currentJobId) { notify("error", "Transcribe first."); return; }
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    document.getElementById("emotionProgress").classList.remove("hidden");
    notify("info", "Starting emotion detection...");
    try {
        const res = await fetch("/api/detect_emotions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId, segments: segmentsData, gemini_api_key: geminiKey }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        if (emotionPollTimer) clearInterval(emotionPollTimer);
        emotionPollTimer = setInterval(checkEmotionProgress, 2000);
    } catch (e) { notify("error", e.message); document.getElementById("emotionProgress").classList.add("hidden"); }
}
async function checkEmotionProgress() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/emotions/${currentJobId}`);
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("emotionProgressFill");
    if (typeof data.percent === "number") { fill.style.width = data.percent + "%"; fill.textContent = data.percent + "% — segment " + data.current + "/" + data.total; }
    if (data.status === "done") {
        clearInterval(emotionPollTimer);
        const emotions = data.emotions || {}; let updated = 0;
        segmentsData.forEach(seg => { if (emotions[seg.segment_id] && EMOTIONS.includes(emotions[seg.segment_id])) { seg.emotion = emotions[seg.segment_id]; updated++; } });
        renderTable();
        notify("success", `Emotion detection complete. ${updated} segments updated.`);
        (data.errors || []).forEach(e => notify("info", "⚠️ " + e));
        document.getElementById("emotionProgress").classList.add("hidden");
        fetchUsage();
    }
    if (data.status === "error") { clearInterval(emotionPollTimer); notify("error", data.error); document.getElementById("emotionProgress").classList.add("hidden"); }
}

function ttsProviderChanged() { updateBadges(); }

async function generateAudio() {
    const provider = document.getElementById("ttsProvider").value;
    const voiceKey = document.getElementById("apiKey").value.trim();
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!voiceKey) { notify("error", "Enter the Voice Engine API key in Step 3."); return; }
    const speakersWithText = [...new Set(segmentsData.filter(s => s.arabic_text.trim()).map(s => s.speaker))];
    const missing = speakersWithText.filter(sp => !speakerVoices[sp]);
    if (missing.length) { notify("error", "No voice for: " + missing.join(", ") + ". Pick voices in Step 4 (or Auto-Assign) first."); return; }
    if (!segmentsData.some(s => s.arabic_text.trim().length > 0)) { notify("error", "Fill at least one Arabic translation."); return; }
    document.getElementById("generateButton").disabled = true;
    document.getElementById("genProgress").classList.remove("hidden");
    notify("info", "Starting Arabic audio generation...");
    const payload = {
        job_id: currentJobId || "",
        segments: segmentsData,
        elevenlabs_api_key: voiceKey,
        gemini_api_key: geminiKey,
        tts_provider: provider,
        gemini_voice: document.getElementById("geminiVoice").value,
        default_voice_id: "",
        speaker_voices: speakerVoices,
        tempo_mode: document.getElementById("tempoMode").value,
        duration_mode: document.getElementById("durationMode").value,
        total_duration: totalDuration,
        cloned_voice_ids: window.clonedVoiceIds || []
    };
    try {
        const res = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); document.getElementById("generateButton").disabled = false; return; }
        if (generatePollTimer) clearInterval(generatePollTimer);
        generatePollTimer = setInterval(checkGenerateProgress, 1000);
    } catch (e) { notify("error", e.message); document.getElementById("generateButton").disabled = false; }
}
async function checkGenerateProgress() {
    const res = await fetch("/api/progress/generate");
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("genProgressFill");
    if (typeof data.percent === "number") { fill.style.width = data.percent + "%"; fill.textContent = data.percent + "%"; }
    if (data.status === "done") {
        clearInterval(generatePollTimer);
        document.getElementById("generateButton").disabled = false;
        const r = data.result || {};
        notify("success", "Arabic audio generated and merged.");
        document.getElementById("resultSection").classList.remove("hidden");
        document.getElementById("audioResults").innerHTML = `
            <p>Segments generated: <strong>${r.segments_generated || 0}</strong> | Timing warnings: <strong>${r.tempo_warnings || 0}</strong> | Trimmed: <strong>${r.duration_cuts || 0}</strong></p>
            <p>Final duration: <strong>${r.final_duration || 0}s</strong> | Voice characters used: <strong>${(r.eleven_credits_used || 0).toLocaleString()}</strong></p>
            <audio controls src="/api/download/final_dubbed.mp3?cache=${Date.now()}"></audio>
            <div class="download-buttons"><a href="/api/download/final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download MP3</a></div>`;
        if (isVideoUpload) document.getElementById("mergeSection").classList.remove("hidden");
        fetchUsage(); updateBadges();
    }
    if (data.status === "error") { clearInterval(generatePollTimer); document.getElementById("generateButton").disabled = false; notify("error", data.error); }
}
async function mergeVideo() {
    if (!currentJobId) { notify("error", "No job found."); return; }
    document.getElementById("mergeButton").disabled = true;
    notify("info", "Merging dubbed audio with video and background music...");
    try {
        const res = await fetch("/api/merge_video", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
		job_id: currentJobId,
		enhance_background: document.getElementById("enhanceBackground") ? document.getElementById("enhanceBackground").checked : true
        const data = await res.json();
        document.getElementById("mergeButton").disabled = false;
        if (data.error) { notify("error", data.error); return; }
        const bgNote = data.has_background ? "✅ Background music/sounds mixed with the dubbed vocals." : "⚠️ No background separation available; dubbed vocals only.";
        document.getElementById("videoResults").classList.remove("hidden");
        document.getElementById("videoResults").innerHTML = `
            <h4>🎬 Final Dubbed Video:</h4><p class="note">${bgNote}</p>
            <video controls src="/api/download/final_dubbed_video.mp4?cache=${Date.now()}"></video>
            <div class="download-buttons">
                <a href="/api/download/final_dubbed_video.mp4?cache=${Date.now()}" download="final_dubbed_video.mp4">⬇️ Download Dubbed Video (MP4)</a>
                <a class="blue" href="/api/download/final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download Pure Vocals (MP3)</a>
            </div>`;
        notify("success", "Video merged successfully!");
    } catch (e) { document.getElementById("mergeButton").disabled = false; notify("error", e.message); }
}

async function regenerateLine(i, btn) {
    const seg = segmentsData[i];
    if (!(seg.arabic_text || "").trim()) { notify("error", "This line has no Arabic text yet."); return; }
    const voiceKey = document.getElementById("apiKey").value.trim();
    if (!voiceKey) { notify("error", "Enter the Voice Engine API key in Step 3."); return; }
    const voice_id = speakerVoices[seg.speaker] || "";
    if (!voice_id) { notify("error", "No voice for " + seg.speaker + ". Pick one in Step 4 first."); return; }
    btn.disabled = true; btn.textContent = "⏳";
    notify("info", `Re-speaking line ${i + 1} only...`);
    try {
        const res = await fetch("/api/regenerate_line", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                job_id: currentJobId || "",
                segment: seg,
                segments: segmentsData,
                elevenlabs_api_key: voiceKey,
                voice_id: voice_id,
                tempo_mode: document.getElementById("tempoMode").value,
                duration_mode: document.getElementById("durationMode").value,
                total_duration: totalDuration
            })
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.status !== "success") {
            const msg = (data && (data.error || data.detail)) || ("Server error " + res.status);
            notify("error", "Regenerate failed: " + msg);
            return;
        }
        const usd = lineCostUsd(seg.arabic_text, seg.emotion);
        const cr = Math.max(1, usdToCredits(usd));
        notify("success", `Line ${i + 1} re-spoken: ${data.stretched_duration}s into a ${data.target}s window. Cost ≈ ${cr} credits ($${usd.toFixed(4)}). Final mix rebuilt — play Step 6 at ${seg.start}s to hear it.` + (data.tempo_warning ? " ⚠️ stretched to the limit." : ""));
        const au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        fetchUsage();
    } catch (e) {
        notify("error", e.message);
    } finally {
        btn.disabled = false; btn.textContent = "🔄";
    }
}

function openTimeline() {
    document.getElementById("timelineSection").classList.remove("hidden");
    renderTimeline();
}
function resetTimeline() {
    segmentOffsets = {};
    renderTimeline();
    notify("info", "Offsets reset.");
}
function renderTimeline() {
    const wrap = document.getElementById("timelineWrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!segmentsData.length) return;
    const total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(s => s.end).concat([1]));
    const W = wrap.clientWidth || 900;
    const scale = W / total;
    const speakers = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    speakers.forEach(spk => {
        const lane = document.createElement("div");
        lane.style.cssText = "position:relative;height:34px;border-bottom:1px solid #37474f;";
        const lab = document.createElement("span");
        lab.textContent = spk;
        lab.style.cssText = "position:absolute;left:4px;top:9px;color:#90a4ae;font-size:11px;z-index:5;pointer-events:none;";
        lane.appendChild(lab);
        segmentsData.forEach((seg, i) => {
            if ((seg.speaker || "Speaker 1") !== spk || !(seg.arabic_text || "").trim()) return;
            const off = segmentOffsets[seg.segment_id] || 0;
            const box = document.createElement("div");
            const left = Math.max(0, (seg.start + off) * scale);
            const width = Math.max(8, (seg.end - seg.start) * scale);
            box.style.cssText = "position:absolute;left:" + left + "px;top:4px;width:" + width + "px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;";
            box.title = "Line " + (i + 1) + ": drag to shift";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function (ev) {
                ev.preventDefault();
                const startX = ev.clientX;
                const startOff = off;
                const move = function (e2) {
                    let no = startOff + (e2.clientX - startX) / scale;
                    no = Math.max(-2, Math.min(2, no));
                    no = Math.max(-seg.start, Math.min(total - seg.end, no));
                    segmentOffsets[seg.segment_id] = no;
                    box.style.left = Math.max(0, (seg.start + no) * scale) + "px";
                    box.textContent = (i + 1) + " (" + (no > 0 ? "+" : "") + Math.round(no * 1000) + "ms)";
                };
                const up = function () {
                    document.removeEventListener("mousemove", move);
                    document.removeEventListener("mouseup", up);
                    renderTimeline();
                };
                document.addEventListener("mousemove", move);
                document.addEventListener("mouseup", up);
            };
            lane.appendChild(box);
        });
        wrap.appendChild(lane);
    });
}
async function confirmTimeline() {
    const offs = {};
    Object.keys(segmentOffsets).forEach(k => { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
    if (!Object.keys(offs).length) { notify("info", "No offsets to apply — drag some blocks first."); return; }
    notify("info", "Rebuilding final audio with your offsets...");
    try {
        const res = await fetch("/api/remix_audio", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: currentJobId || "", segments: segmentsData, offsets: offs, total_duration: totalDuration, duration_mode: document.getElementById("durationMode").value })
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.status !== "success") {
            notify("error", "Remix failed: " + ((data && (data.error || data.detail)) || res.status));
            return;
        }
        notify("success", "New mix built with " + data.segments_generated + " lines. The Step 6 player now uses it.");
        const au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
    } catch (e) { notify("error", e.message); }
}

window.addEventListener('DOMContentLoaded', updateBadges);

const MAX_UPLOAD_BYTES = 400 * 1024 * 1024;
const MAX_DURATION_SEC = 60.5;

function probeFileDuration(file) {
    return new Promise((resolve) => {
        try {
            const url = URL.createObjectURL(file);
            const v = document.createElement("video");
            v.preload = "metadata";
            v.onloadedmetadata = () => { const d = v.duration; URL.revokeObjectURL(url); resolve(isFinite(d) ? d : null); };
            v.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
            v.src = url;
        } catch (e) { resolve(null); }
    });
}

async function startTranscribe() {
    const file = document.getElementById("audioFile").files[0];
    if (!file) { notify("error", "Choose an audio or video file."); return; }
    if (file.size > MAX_UPLOAD_BYTES) {
        notify("error", "File too large (" + (file.size / 1073741824).toFixed(2) + " GB). The limit is 1.5 GB — a 1-minute 4K clip is only ≈0.5–0.8 GB.");
        return;
    }
    const dur = await probeFileDuration(file);
    if (dur !== null && dur > MAX_DURATION_SEC) {
        notify("error", "This clip is " + Math.round(dur) + " seconds long. This build accepts up to 60 seconds — please trim it first.");
        return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("speaker_count", document.getElementById("speakerCount").value);
    form.append("hf_token", document.getElementById("hfToken").value.trim());
    document.getElementById("progressSection").classList.remove("hidden");
    notify("info", "Uploading and starting transcription...");
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await res.json();
    currentJobId = data.job_id; originalSegments = [];
    speakerVoices = {}; speakerVoiceNames = {}; speakerChoices = {}; clonedBySpeaker = {};
    voicePools = { male: [], female: [] };
    if (transcribePollTimer) clearInterval(transcribePollTimer);
    transcribePollTimer = setInterval(checkTranscribeProgress, 1000);
}

function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    const mk = (tag) => document.createElement(tag);
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);
    const eCell = mk("td"); const eS = mk("select"); EMOTIONS.slice().sort().forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.emotion === v); eS.appendChild(o); }); eS.onchange = () => { segmentsData[i].emotion = eS.value; updateBadges(); }; eCell.appendChild(eS); row.appendChild(eCell);
    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio for this line"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only (current Arabic text, emotion & voice), then rebuild the mix"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line from Auto-Fix, Translate and Tashkeel"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

(function addNumberColumnAndStyleSuggestions() {
    const tr = document.querySelector("#segmentsTable thead tr");
    if (tr && !tr.querySelector("th.numcol")) {
        const th = document.createElement("th");
        th.className = "numcol";
        th.style.width = "36px";
        th.textContent = "#";
        tr.insertBefore(th, tr.firstChild);
    }
    if (!document.getElementById("emotionList")) {
        const dl = document.createElement("datalist");
        dl.id = "emotionList";
        EMOTIONS.slice().sort().forEach(e => {
            const o = document.createElement("option");
            o.value = e;
            dl.appendChild(o);
        });
        ["confident and calm", "anxious and afraid", "calm but firm", "playful and teasing", "tired and sad", "angry but controlled"].forEach(e => {
            const o = document.createElement("option");
            o.value = e;
            dl.appendChild(o);
        });
        document.body.appendChild(dl);
    }
})();

function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    const mk = (tag) => document.createElement(tag);
    const numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.textContent = i + 1; row.appendChild(numCell);
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);
    const eCell = mk("td"); const eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.title = "Speaking style — combine tags if you want, e.g. 'confident and calm'"; eI.onchange = () => { segmentsData[i].emotion = (eI.value || "neutral").trim(); updateBadges(); }; eCell.appendChild(eI); row.appendChild(eCell);
    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio for this line"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only (current Arabic text, style & voice), then rebuild the mix"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line from Auto-Fix, Translate and Tashkeel"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

async function checkEmotionProgress() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/emotions/${currentJobId}`);
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("emotionProgressFill");
    if (typeof data.percent === "number") { fill.style.width = data.percent + "%"; fill.textContent = data.percent + "% — segment " + data.current + "/" + data.total; }
    if (data.status === "done") {
        clearInterval(emotionPollTimer);
        const emotions = data.emotions || {}; let updated = 0;
        segmentsData.forEach(seg => {
            const v = emotions[seg.segment_id];
            if (v && typeof v === "string" && v.trim().length >= 2) { seg.emotion = v.trim().slice(0, 60); updated++; }
        });
        renderTable();
        notify("success", `Emotion detection complete. ${updated} segments updated.`);
        (data.errors || []).forEach(e => notify("info", "⚠️ " + e));
        document.getElementById("emotionProgress").classList.add("hidden");
        fetchUsage();
    }
    if (data.status === "error") { clearInterval(emotionPollTimer); notify("error", data.error); document.getElementById("emotionProgress").classList.add("hidden"); }
}

async function autoTranslate() {
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!geminiKey) { notify("error", "Enter the Translation AI key in Step 2 first."); return; }
    const unlocked = segmentsData.filter(s => !s.locked);
    if (!unlocked.length) { notify("error", "All lines are locked — nothing to translate."); return; }
    notify("info", "Translating " + unlocked.length + " unlocked line(s) to Arabic (locked lines skipped)...");
    try {
        const res = await fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId || "", segments: unlocked, gemini_api_key: geminiKey }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        let matched = 0;
        (data.translated_segments || []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg) {
                seg.arabic_text = item.arabic_text || "";
                if (item.emotion && typeof item.emotion === "string" && item.emotion.trim().length >= 2) seg.emotion = item.emotion.trim().slice(0, 60);
                matched++;
            }
        });
        renderTable();
        notify("success", "Translation complete. " + matched + " segments translated. Locked lines untouched.");
        fetchUsage();
    } catch (e) { notify("error", e.message); }
}

function sanitizeStyle(v) {
    const parts = String(v || "").toLowerCase().split(/[,+\/;]| and /).map(s => s.trim()).filter(Boolean);
    const kept = [];
    parts.forEach(p => { if (EMOTIONS.includes(p) && !kept.includes(p)) kept.push(p); });
    return kept.join(", ");
}

function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    const mk = (tag) => document.createElement(tag);
    const numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.textContent = i + 1; row.appendChild(numCell);
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);
    const eCell = mk("td"); const eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.placeholder = "tags: confident, calm"; eI.title = "Speaking style — multiple tags from the list, comma separated"; eI.onchange = () => {
        const clean = sanitizeStyle(eI.value) || "neutral";
        if (clean !== eI.value.trim()) notify("info", "Words not in the official style list were removed. Style is now: '" + clean + "'.");
        eI.value = clean;
        segmentsData[i].emotion = clean;
        updateBadges();
    }; eCell.appendChild(eI); row.appendChild(eCell);
    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio for this line"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only (current Arabic text, style & voice), then rebuild the mix"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line from Auto-Fix, Translate and Tashkeel"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

async function checkEmotionProgress() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/emotions/${currentJobId}`);
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("emotionProgressFill");
    if (typeof data.percent === "number") { fill.style.width = data.percent + "%"; fill.textContent = data.percent + "% — segment " + data.current + "/" + data.total; }
    if (data.status === "done") {
        clearInterval(emotionPollTimer);
        const emotions = data.emotions || {}; let updated = 0;
        segmentsData.forEach(seg => {
            const clean = sanitizeStyle(emotions[seg.segment_id]);
            if (clean) { seg.emotion = clean; updated++; }
        });
        renderTable();
        notify("success", `Emotion detection complete. ${updated} segments updated.`);
        (data.errors || []).forEach(e => notify("info", "⚠️ " + e));
        document.getElementById("emotionProgress").classList.add("hidden");
        fetchUsage();
    }
    if (data.status === "error") { clearInterval(emotionPollTimer); notify("error", data.error); document.getElementById("emotionProgress").classList.add("hidden"); }
}

async function autoTranslate() {
    const geminiKey = document.getElementById("geminiApiKey").value.trim();
    if (!geminiKey) { notify("error", "Enter the Translation AI key in Step 2 first."); return; }
    const unlocked = segmentsData.filter(s => !s.locked);
    if (!unlocked.length) { notify("error", "All lines are locked — nothing to translate."); return; }
    notify("info", "Translating " + unlocked.length + " unlocked line(s) to Arabic (locked lines skipped)...");
    try {
        const res = await fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId || "", segments: unlocked, gemini_api_key: geminiKey }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        let matched = 0;
        (data.translated_segments || []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg) {
                seg.arabic_text = item.arabic_text || "";
                const clean = sanitizeStyle(item.emotion);
                if (clean) seg.emotion = clean;
                matched++;
            }
        });
        renderTable();
        notify("success", "Translation complete. " + matched + " segments translated. Locked lines untouched.");
        fetchUsage();
    } catch (e) { notify("error", e.message); }
}

async function ensureVoicePools() {
    if (voicePools.male.length || voicePools.female.length) return true;
    try {
        const res = await fetch("/api/voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        const data = await res.json();
        if (data.error || !data.voices) return false;
        availableVoices = data.voices;
        buildVoicePools(availableVoices);
        return true;
    } catch (e) { return false; }
}
async function loadVoiceOptions() {
    const ok = await ensureVoicePools();
    if (ok) notify("success", "Voice options loaded. Pick a voice per speaker below.");
    else notify("error", "Could not load the voice library. Check the server configuration.");
    renderSpeakerVoices();
}
async function autoAssignVoices() {
    const ok = await ensureVoicePools();
    if (!ok) { notify("error", "Voice library unavailable. Check the server configuration."); return; }
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    names.forEach(name => {
        if (speakerChoices[name]) { applyChoice(name); return; }
        if (clonedBySpeaker[name]) { speakerChoices[name] = "clone"; applyChoice(name); return; }
        let male = 0, female = 0;
        segmentsData.forEach(s => { if (s.speaker === name) { if (s.gender === "female") female++; else male++; } });
        let g = female > male ? "female" : "male";
        let pool = voicePools[g];
        if (!pool.length) { g = (g === "male" ? "female" : "male"); pool = voicePools[g]; }
        if (!pool.length) return;
        const usedIds = new Set(Object.values(speakerVoices));
        const freeIdx = pool.map((p, i) => i).filter(i => !usedIds.has(pool[i].voice_id));
        const pick = freeIdx.length ? freeIdx[Math.floor(Math.random() * freeIdx.length)] : Math.floor(Math.random() * pool.length);
        speakerChoices[name] = g + ":" + (pick + 1);
        applyChoice(name);
    });
    renderSpeakerVoices();
    notify("success", "Voices auto-assigned. You can change any speaker's voice in the Step 4 table.");
}
async function confirmCloning() {
    if (!currentJobId) { notify("error", "Transcribe first."); return; }
    const selected = [];
    document.querySelectorAll("#cloneAnalysisTable input[type=checkbox]").forEach(cb => { if (cb.checked) selected.push(cb.dataset.speaker); });
    if (!selected.length) { notify("error", "Select at least one speaker to clone."); return; }
    notify("info", `Cloning ${selected.length} voice(s)... this may take a minute.`);
    try {
        const res = await fetch("/api/clone", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId, segments: segmentsData, speakers_to_clone: selected }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        const cloned = data.cloned_voices || {};
        window.clonedVoiceIds = [];
        let ok = 0;
        Object.keys(cloned).forEach(speaker => {
            const vid = cloned[speaker];
            if (!vid.startsWith("ERROR")) { clonedBySpeaker[speaker] = vid; speakerChoices[speaker] = "clone"; window.clonedVoiceIds.push(vid); applyChoice(speaker); ok++; }
            else notify("error", speaker + ": " + vid);
        });
        renderSpeakerVoices();
        notify("success", `Voices cloned successfully! ${ok} speaker(s) assigned to their cloned voices.`);
        (data.warnings || []).forEach(w => notify("info", "⚠️ " + w));
    } catch (e) { notify("error", e.message); }
}
async function autoTranslate() {
    const unlocked = segmentsData.filter(s => !s.locked);
    if (!unlocked.length) { notify("error", "All lines are locked — nothing to translate."); return; }
    notify("info", "Translating " + unlocked.length + " unlocked line(s) to Arabic (locked lines skipped)...");
    try {
        const res = await fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId || "", segments: unlocked }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        let matched = 0;
        (data.translated_segments || []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg) { seg.arabic_text = item.arabic_text || ""; const clean = sanitizeStyle(item.emotion); if (clean) seg.emotion = clean; matched++; }
        });
        renderTable();
        notify("success", "Translation complete. " + matched + " segments translated. Locked lines untouched.");
        fetchUsage();
    } catch (e) { notify("error", e.message); }
}
async function detectEmotions() {
    if (!currentJobId) { notify("error", "Transcribe first."); return; }
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    document.getElementById("emotionProgress").classList.remove("hidden");
    notify("info", "Starting emotion detection...");
    try {
        const res = await fetch("/api/detect_emotions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: currentJobId, segments: segmentsData }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        if (emotionPollTimer) clearInterval(emotionPollTimer);
        emotionPollTimer = setInterval(checkEmotionProgress, 2000);
    } catch (e) { notify("error", e.message); document.getElementById("emotionProgress").classList.add("hidden"); }
}
async function addTashkeel() {
    const targets = segmentsData.filter(s => (s.arabic_text || "").trim().length > 0 && !s.locked);
    if (!targets.length) { notify("error", "No unlocked Arabic text found. Locked lines are skipped."); return; }
    notify("info", "Adding tashkeel to " + targets.length + " unlocked line(s)...");
    try {
        const res = await fetch("/api/tashkeel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: targets.map(s => ({ segment_id: s.segment_id, arabic_text: s.arabic_text })) }) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); return; }
        let done = 0;
        (data.items || []).forEach(item => {
            const seg = segmentsData.find(s => s.segment_id === item.segment_id);
            if (seg && item.arabic_text) { seg.arabic_text = item.arabic_text; done++; }
        });
        renderTable();
        notify("success", "Tashkeel added to " + done + " unlocked line(s). Locked lines untouched.");
    } catch (e) { notify("error", "Tashkeel failed: " + e.message); }
}
async function generateAudio() {
    const speakersWithText = [...new Set(segmentsData.filter(s => s.arabic_text.trim()).map(s => s.speaker))];
    const missing = speakersWithText.filter(sp => !speakerVoices[sp]);
    if (missing.length) { notify("error", "No voice for: " + missing.join(", ") + ". Pick voices in Step 4 (or Auto-Assign) first."); return; }
    if (!segmentsData.some(s => s.arabic_text.trim().length > 0)) { notify("error", "Fill at least one Arabic translation."); return; }
    document.getElementById("generateButton").disabled = true;
    document.getElementById("genProgress").classList.remove("hidden");
    notify("info", "Starting Arabic audio generation...");
    const payload = {
        job_id: currentJobId || "",
        segments: segmentsData,
        tts_provider: document.getElementById("ttsProvider").value,
        gemini_voice: document.getElementById("geminiVoice").value,
        speaker_voices: speakerVoices,
        tempo_mode: document.getElementById("tempoMode").value,
        duration_mode: document.getElementById("durationMode").value,
        total_duration: totalDuration,
        cloned_voice_ids: window.clonedVoiceIds || []
    };
    try {
        const res = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const data = await res.json();
        if (data.error) { notify("error", data.error); document.getElementById("generateButton").disabled = false; return; }
        if (generatePollTimer) clearInterval(generatePollTimer);
        generatePollTimer = setInterval(checkGenerateProgress, 1000);
    } catch (e) { notify("error", e.message); document.getElementById("generateButton").disabled = false; }
}
async function regenerateLine(i, btn) {
    const seg = segmentsData[i];
    if (!(seg.arabic_text || "").trim()) { notify("error", "This line has no Arabic text yet."); return; }
    const voice_id = speakerVoices[seg.speaker] || "";
    if (!voice_id) { notify("error", "No voice for " + seg.speaker + ". Pick one in Step 4 first."); return; }
    btn.disabled = true; btn.textContent = "⏳";
    notify("info", `Re-speaking line ${i + 1} only...`);
    try {
        const res = await fetch("/api/regenerate_line", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                job_id: currentJobId || "",
                segment: seg,
                segments: segmentsData,
                voice_id: voice_id,
                tempo_mode: document.getElementById("tempoMode").value,
                duration_mode: document.getElementById("durationMode").value,
                total_duration: totalDuration
            })
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.status !== "success") {
            notify("error", "Regenerate failed: " + ((data && (data.error || data.detail)) || res.status));
            return;
        }
        const usd = lineCostUsd(seg.arabic_text, seg.emotion);
        const cr = Math.max(1, usdToCredits(usd));
        notify("success", `Line ${i + 1} re-spoken: ${data.stretched_duration}s into a ${data.target}s window. Cost ≈ ${cr} credits ($${usd.toFixed(4)}). Final mix rebuilt — play Step 6 at ${seg.start}s to hear it.` + (data.tempo_warning ? " ⚠️ stretched to the limit." : ""));
        const au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        fetchUsage();
    } catch (e) {
        notify("error", e.message);
    } finally {
        btn.disabled = false; btn.textContent = "🔄";
    }
}

function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    const mk = (tag) => document.createElement(tag);
    const numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.textContent = i + 1; row.appendChild(numCell);
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);

    const eCell = mk("td");
    const eWrap = mk("div"); eWrap.style.cssText = "display:flex;gap:3px;align-items:center;";
    const eS = mk("select");
    const blank = mk("option"); blank.value = ""; blank.textContent = "＋ add…"; eS.appendChild(blank);
    EMOTIONS.slice().sort().forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    ["confident, calm", "anxious, afraid", "calm, firm", "playful, teasing", "tired, sad", "angry, controlled"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    eS.title = "Pick a style tag to add it (you can combine several)";
    eS.onchange = () => {
        if (!eS.value) return;
        const merged = (seg.emotion ? seg.emotion + ", " : "") + eS.value;
        const clean = sanitizeStyle(merged) || "neutral";
        seg.emotion = clean; eI.value = clean; eS.selectedIndex = 0; updateBadges();
    };
    const eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.placeholder = "tags: confident, calm"; eI.title = "Speaking style — multiple tags from the list, comma separated"; eI.style.flex = "1"; eI.style.minWidth = "90px";
    eI.onchange = () => {
        const clean = sanitizeStyle(eI.value) || "neutral";
        if (clean !== eI.value.trim()) notify("info", "Words not in the official style list were removed. Style is now: '" + clean + "'.");
        eI.value = clean; seg.emotion = clean; updateBadges();
    };
    eWrap.appendChild(eS); eWrap.appendChild(eI); eCell.appendChild(eWrap); row.appendChild(eCell);

    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio for this line"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only (current Arabic text, style & voice), then rebuild the mix"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line from Auto-Fix, Translate and Tashkeel"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

async function startTranscribe() {
    const file = document.getElementById("audioFile").files[0];
    if (!file) { notify("error", "Choose an audio or video file first."); return; }
    if (file.size > MAX_UPLOAD_BYTES) {
        notify("error", "File too large (" + (file.size / 1073741824).toFixed(2) + " GB). The limit is 1.5 GB — a 1-minute 4K clip is only ≈0.5–0.8 GB.");
        return;
    }
    const dur = await probeFileDuration(file);
    if (dur !== null && dur > MAX_DURATION_SEC) {
        notify("error", "This clip is " + Math.round(dur) + " seconds long. This build accepts up to 60 seconds — please trim it first.");
        return;
    }
    const speakerCount = parseInt(document.getElementById("speakerCount").value, 10) || 2;
    const form = new FormData();
    form.append("file", file);
    form.append("speaker_count", speakerCount);
    document.getElementById("progressSection").classList.remove("hidden");
    notify("info", "Uploading and starting transcription...");
    try {
        const res = await fetch("/api/transcribe", { method: "POST", body: form });
        const data = await res.json();
        currentJobId = data.job_id; originalSegments = [];
        speakerVoices = {}; speakerVoiceNames = {}; speakerChoices = {}; clonedBySpeaker = {};
        voicePools = { male: [], female: [] };
        if (transcribePollTimer) clearInterval(transcribePollTimer);
        transcribePollTimer = setInterval(checkTranscribeProgress, 1000);
    } catch (e) {
        notify("error", "Transcribe failed: " + e.message);
    }
}

// =====================================================================
// CONSOLIDATED OVERRIDES v2 — the ONLY patch layer in this file.
// Future UI fixes are edited INSIDE this block, never appended below.
// =====================================================================

// ---------- User header (name, credits, logout) ----------
(function loadUserInfo() {
    fetch("/api/user/info").then(function (r) { return r.json(); }).then(function (data) {
        var nameEl = document.getElementById("userName");
        var credEl = document.getElementById("creditsDisplay");
        var credNum = document.getElementById("creditsNum");
        if (nameEl) nameEl.textContent = data.name || "";
        if (!data.is_guest && credEl && credNum) {
            credNum.textContent = data.credits;
            credEl.style.display = "inline-block";
        }
    }).catch(function () {});
})();

function doLogout() {
    fetch("/api/logout", { method: "POST" }).then(function () {
        window.location.href = "/login";
    }).catch(function () { window.location.href = "/login"; });
}

function refreshCredits() {
    fetch("/api/user/info").then(function (r) { return r.json(); }).then(function (data) {
        var credNum = document.getElementById("creditsNum");
        if (credNum && !data.is_guest) credNum.textContent = data.credits;
    }).catch(function () {});
}

// ---------- File upload button label ----------
function onFileSelected(input) {
    var label = document.getElementById("fileUploadText");
    if (!label) return;
    if (input.files && input.files[0]) {
        var f = input.files[0];
        var sizeMB = (f.size / (1024 * 1024)).toFixed(1);
        label.textContent = f.name + " (" + sizeMB + " MB)";
    } else {
        label.textContent = "Choose File";
    }
}

// ---------- Enhance-background toggle (stores preference; backend wiring comes next) ----------
var _origGenerateAudio = generateAudio;
generateAudio = function () {
    var enhance = document.getElementById("enhanceBackground");
    window._enhanceBackground = enhance ? enhance.checked : false;
    _origGenerateAudio();
};

// ---------- Friendly waiting messages (separate line, never replaces %) ----------
var friendlyWaitMessages = [
    "Still working — large videos can take a few minutes on CPU.",
    "The system is processing audio carefully. Please keep this page open.",
    "Speaker detection and vocal separation are the slowest steps.",
    "If the percentage is moving, everything is fine.",
    "High-quality processing takes longer, but gives better results.",
    "Please do not refresh the page while processing.",
    "The server is still alive. Waiting for the next processing update.",
    "Some steps stay at one percentage for a while, especially speaker detection.",
    "Almost all AI audio tasks are slower on CPU servers.",
    "Thank you for your patience — processing is continuing."
];
var friendlyMsgIndex = 0;
var friendlyTimer = null;

function ensureFriendlyLine(progressTextId) {
    var main = document.getElementById(progressTextId);
    if (!main) return null;
    var id = progressTextId + "_friendly";
    var existing = document.getElementById(id);
    if (existing) return existing;
    var p = document.createElement("p");
    p.id = id;
    p.style.cssText = "text-align:center;font-size:12px;color:#9ca3af;margin-top:4px;min-height:18px;";
    p.textContent = friendlyWaitMessages[0];
    main.insertAdjacentElement("afterend", p);
    return p;
}

function startFriendlyMessages(progressTextId) {
    var friendly = ensureFriendlyLine(progressTextId);
    if (!friendly) return;
    if (friendlyTimer) clearInterval(friendlyTimer);
    friendlyMsgIndex = 0;
    friendly.textContent = friendlyWaitMessages[0];
    friendlyTimer = setInterval(function () {
        friendlyMsgIndex = (friendlyMsgIndex + 1) % friendlyWaitMessages.length;
        friendly.textContent = friendlyWaitMessages[friendlyMsgIndex];
    }, 60000);
}

function stopFriendlyMessages(progressTextId) {
    if (friendlyTimer) { clearInterval(friendlyTimer); friendlyTimer = null; }
    var el = document.getElementById(progressTextId + "_friendly");
    if (el) el.remove();
}

function safePercent(value, fallback) {
    var n = Number(value);
    if (isNaN(n)) return fallback || 0;
    return Math.max(fallback || 0, Math.min(100, n));
}

// ---------- Transcribe progress (percentage always visible + real backend step) ----------
var transcribeLastPercent = 0;

checkTranscribeProgress = async function () {
    if (!currentJobId) return;
    try {
        var res = await fetch("/api/progress/" + currentJobId + "?t=" + Date.now());
        var data = await res.json();
        var fill = document.getElementById("progressFill");
        var txt = document.getElementById("progressText");

        var percent = safePercent(data.percent, transcribeLastPercent);
        if (percent >= transcribeLastPercent) transcribeLastPercent = percent;

        if (fill) fill.style.width = transcribeLastPercent + "%";
        var statusText = data.status_text || data.message || data.status || "Processing...";
		if (txt) txt.textContent = transcribeLastPercent + "% — " + statusText;    if (txt) txt.textContent = transcribeLastPercent + "% — " + statusText + " | Job: " + currentJobId.substring(0, 8);

        if (data.status === "processing") startFriendlyMessages("progressText");
        if (data.is_video !== undefined) isVideoUpload = data.is_video;

        if (data.status === "done") {
            stopFriendlyMessages("progressText");
            clearInterval(transcribePollTimer);
            transcribeLastPercent = 0;
            if (fill) fill.style.width = "100%";
            if (txt) txt.textContent = "100% — Transcription complete.";

            segmentsData = data.segments || [];
            segmentsData.forEach(function (s) {
                s.start = Number(Number(s.start).toFixed(2));
                s.end = Number(Number(s.end).toFixed(2));
                s.locked = false;
            });
            originalSegments = JSON.parse(JSON.stringify(segmentsData));
            totalDuration = data.full_duration || 0;

            var message = "Transcription complete.";
            if (data.detected_speakers > 0) message += " Detected speakers: " + data.detected_speakers + ".";
            if (data.warning) notify("error", "⚠️ " + data.warning);
            notify("success", message);

            renderTable();
            renderSpeakerVoices();
            ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.classList.remove("hidden");
            });
            updateBadges();
            fetchUsage();
        }

        if (data.status === "error") {
            stopFriendlyMessages("progressText");
            clearInterval(transcribePollTimer);
            transcribeLastPercent = 0;
            if (txt) txt.textContent = "Error — " + (data.error || "Unknown error");
            notify("error", data.error || "Transcription failed.");
        }
    } catch (e) {
        var t = document.getElementById("progressText");
        if (t) t.textContent = transcribeLastPercent + "% — Still waiting for server response...";
        console.error("Progress check failed:", e);
    }
};

// ---------- Generate progress ----------
var generateLastPercent = 0;

checkGenerateProgress = async function () {
    try {
        var res = await fetch("/api/progress/generate?t=" + Date.now());
        var data = await res.json();
        if (!data || data.status === "not_found") return;

        var fill = document.getElementById("genProgressFill");
        var txt = document.getElementById("genProgressText");

        var percent = safePercent(data.percent, generateLastPercent);
        if (percent >= generateLastPercent) generateLastPercent = percent;

        if (fill) fill.style.width = generateLastPercent + "%";
        var statusText = data.status_text || data.message || data.status || "Generating...";
        if (txt) txt.textContent = generateLastPercent + "% — " + statusText;

        if (data.status === "processing") startFriendlyMessages("genProgressText");

        if (data.status === "done") {
            stopFriendlyMessages("genProgressText");
            clearInterval(generatePollTimer);
            generateLastPercent = 0;
            if (fill) fill.style.width = "100%";
            if (txt) txt.textContent = "100% — Audio generation complete.";

            var btn = document.getElementById("generateButton");
            if (btn) btn.disabled = false;

            var r = data.result || {};
            notify("success", "Arabic audio generated and merged.");
            document.getElementById("resultSection").classList.remove("hidden");
            document.getElementById("audioResults").innerHTML =
                '<p>Segments generated: <strong>' + (r.segments_generated || 0) + '</strong> | Timing warnings: <strong>' + (r.tempo_warnings || 0) + '</strong> | Trimmed: <strong>' + (r.duration_cuts || 0) + '</strong></p>' +
                '<p>Final duration: <strong>' + (r.final_duration || 0) + 's</strong> | Voice characters used: <strong>' + ((r.eleven_credits_used || 0).toLocaleString()) + '</strong></p>' +
                '<audio controls src="/api/download/final_dubbed.mp3?cache=' + Date.now() + '"></audio>' +
                '<div class="download-buttons"><a href="/api/download/final_dubbed.mp3?cache=' + Date.now() + '" download="final_dubbed.mp3">⬇️ Download MP3</a></div>';

            if (isVideoUpload) document.getElementById("mergeSection").classList.remove("hidden");
            fetchUsage();
            updateBadges();
            refreshCredits();
        }

        if (data.status === "error") {
            stopFriendlyMessages("genProgressText");
            clearInterval(generatePollTimer);
            generateLastPercent = 0;
            var btn2 = document.getElementById("generateButton");
            if (btn2) btn2.disabled = false;
            if (txt) txt.textContent = "Error — " + (data.error || "Unknown error");
            notify("error", data.error || "Audio generation failed.");
        }
    } catch (e) {
        var t2 = document.getElementById("genProgressText");
        if (t2) t2.textContent = generateLastPercent + "% — Still waiting for server response...";
        console.error("Generate progress check failed:", e);
    }
};

// ---------- Safety: remove a duplicated "#" header column if one ever appears ----------
(function removeDuplicateNumCol() {
    var tr = document.querySelector("#segmentsTable thead tr");
    if (!tr) return;
    var ths = tr.querySelectorAll("th");
    if (ths.length >= 2 && ths[0].textContent.trim() === "#" && ths[1].textContent.trim() === "#") {
        ths[1].remove();
    }
})();

// ---------- Segments table row (locked = yellow, speaker groups alternate) ----------
function createRow(seg, i) {
    var row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    if (!seg.locked) {
        var groupIdx = 0;
        for (var j = 1; j <= i; j++) { if (segmentsData[j].speaker !== segmentsData[j - 1].speaker) groupIdx++; }
        row.style.background = groupIdx % 2 === 0 ? "#ffffff" : "#f8fafc";
    }
    var mk = function (tag) { return document.createElement(tag); };

    var numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.style.fontWeight = "600"; numCell.textContent = i + 1; row.appendChild(numCell);

    var startCell = mk("td"); var si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = function () { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);

    var endCell = mk("td"); var ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = function () { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);

    var spCell = mk("td"); var spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = function () { updateSpeakerName(i, spI.value); }; spCell.appendChild(spI); row.appendChild(spCell);

    var gCell = mk("td"); var gS = mk("select"); ["male", "female"].forEach(function (v) { var o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = function () { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);

    var eCell = mk("td");
    var eWrap = mk("div"); eWrap.style.cssText = "display:flex;gap:3px;align-items:center;";
    var eS = mk("select"); eS.style.width = "auto"; eS.style.minWidth = "60px"; eS.style.flex = "none";
    var blank = mk("option"); blank.value = ""; blank.textContent = "＋"; eS.appendChild(blank);
    EMOTIONS.slice().sort().forEach(function (v) { var o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    ["confident, calm", "anxious, afraid", "calm, firm", "playful, teasing", "tired, sad", "angry, controlled"].forEach(function (v) { var o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    eS.onchange = function () {
        if (!eS.value) return;
        var merged = (seg.emotion ? seg.emotion + ", " : "") + eS.value;
        var clean = sanitizeStyle(merged) || "neutral";
        seg.emotion = clean; eI.value = clean; eS.selectedIndex = 0; updateBadges();
    };
    var eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.placeholder = "tags…"; eI.style.flex = "1"; eI.style.minWidth = "80px";
    eI.onchange = function () {
        var clean = sanitizeStyle(eI.value) || "neutral";
        if (clean !== eI.value.trim()) notify("info", "Words not in the official list were removed. Style: '" + clean + "'.");
        eI.value = clean; seg.emotion = clean; updateBadges();
    };
    eWrap.appendChild(eS); eWrap.appendChild(eI); eCell.appendChild(eWrap); row.appendChild(eCell);

    var enCell = mk("td"); var enT = mk("textarea"); enT.value = seg.text; enT.onchange = function () { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);

    var arCell = mk("td"); var arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = function () { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);

    var aCell = mk("td");
    var pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio"; pb.onclick = function () { previewRow(i, pb); };
    var rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak this line only"; rb.onclick = function () { regenerateLine(i, rb); };
    var ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = function () { insertSegmentAfter(i); };
    var db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = function () { deleteSegment(i); };
    var lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked" : "Lock this line"; lb.onclick = function () { toggleLock(i); };
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

// ---------- Clone analysis (computed in frontend, always populates) ----------
async function analyzeSpeakers() {
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    var names = []; var seen = {};
    segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    names.sort();
    var analysis = names.map(function (name) {
        var segs = segmentsData.filter(function (s) { return (s.speaker || "Speaker 1") === name && (s.text || "").trim(); });
        var total = segs.reduce(function (a, s) { return a + Math.max(0, s.end - s.start); }, 0);
        var total_time = Math.round(total * 10) / 10;
        var n = segs.length;
        var status, message;
        if (total_time < 1.0) { status = "bad"; message = "❌ Cannot clone — only " + total_time + "s across " + n + " line(s). Need ≥1s."; }
        else if (total_time < 3.0) { status = "warning"; message = "⚠️ Barely enough (" + total_time + "s, " + n + " line(s)). Clone may sound robotic."; }
        else if (total_time < 10.0) { status = "warning"; message = "🟡 Acceptable (" + total_time + "s, " + n + " line(s)). Decent clone."; }
        else if (total_time < 20.0) { status = "good"; message = "✅ Good (" + total_time + "s, " + n + " line(s)). Natural clone expected."; }
        else { status = "good"; message = "🌟 Excellent (" + total_time + "s, " + n + " line(s)). Best quality clone."; }
        return { speaker: name, total_time: total_time, num_segments: n, status: status, message: message };
    });
    var tbody = document.querySelector("#cloneAnalysisTable tbody");
    tbody.innerHTML = "";
    analysis.forEach(function (item) {
        var row = document.createElement("tr");
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = item.status !== "bad"; cb.dataset.speaker = item.speaker;
        var c0 = document.createElement("td"); c0.style.textAlign = "center"; c0.appendChild(cb); row.appendChild(c0);
        var c1 = document.createElement("td"); c1.textContent = item.speaker; row.appendChild(c1);
        var c2 = document.createElement("td"); c2.textContent = item.total_time + "s (" + item.num_segments + " line" + (item.num_segments > 1 ? "s" : "") + ")"; row.appendChild(c2);
        var c3 = document.createElement("td"); c3.textContent = item.message; c3.style.color = item.status === "good" ? "#059669" : (item.status === "warning" ? "#d97706" : "#dc2626"); c3.style.fontSize = "0.9em"; row.appendChild(c3);
        tbody.appendChild(row);
    });
    document.getElementById("cloneAnalysisSection").classList.remove("hidden");
    notify("success", "Review the guidance below. Speakers marked ❌ are unchecked automatically.");
}

// ---------- Step 4 voices ----------
async function ensureVoicePools() {
    if (voicePools.male.length || voicePools.female.length) return true;
    try {
        var res = await fetch("/api/voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        var data = await res.json();
        if (data.error || !data.voices) return false;
        availableVoices = data.voices;
        buildVoicePools(availableVoices);
        return true;
    } catch (e) { return false; }
}

async function renderSpeakerVoices() {
    var tbody = document.querySelector("#speakerVoicesTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    var names = []; var seen = {};
    segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    if (!names.length) return;
    if (!voicePools.male.length && !voicePools.female.length) await ensureVoicePools();
    names.forEach(function (name) {
        var row = document.createElement("tr");
        var c1 = document.createElement("td"); c1.textContent = name; c1.style.fontWeight = "600"; row.appendChild(c1);
        var c2 = document.createElement("td");
        var sel = document.createElement("select"); sel.style.width = "100%";
        if (clonedBySpeaker[name]) { var oc = document.createElement("option"); oc.value = "clone"; oc.textContent = "🎙️ Cloned voice (from video)"; sel.appendChild(oc); }
        var addGroup = function (g, label) {
            (voicePools[g] || []).slice(0, 8).forEach(function (p, i) {
                var o = document.createElement("option"); o.value = g + ":" + (i + 1); o.textContent = label + " " + (i + 1); sel.appendChild(o);
            });
        };
        addGroup("male", "🎲 Male voice");
        addGroup("female", "🎲 Female voice");
        if (!clonedBySpeaker[name] && !voicePools.male.length && !voicePools.female.length) {
            var oe = document.createElement("option"); oe.value = ""; oe.textContent = "— Click 'Load Voice Options' first —"; sel.appendChild(oe);
        }
        sel.value = speakerChoices[name] || "";
        sel.onchange = function () { speakerChoices[name] = sel.value; applyChoice(name); renderSpeakerVoices(); };
        c2.appendChild(sel);
        var info = document.createElement("div");
        info.style.cssText = "font-size:12px;color:#6b7280;margin-top:4px;";
        info.textContent = speakerVoiceNames[name] || "";
        c2.appendChild(info);
        row.appendChild(c2);
        tbody.appendChild(row);
    });
}

async function loadVoiceOptions() {
    var ok = await ensureVoicePools();
    if (ok) { notify("success", "Voice options loaded. Pick a voice per speaker below."); renderSpeakerVoices(); }
    else notify("error", "Could not load the voice library.");
}

async function autoAssignVoices() {
    var ok = await ensureVoicePools();
    if (!ok) { notify("error", "Voice library unavailable."); return; }
    var names = []; var seen = {};
    segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    names.forEach(function (name) {
        if (speakerChoices[name]) { applyChoice(name); return; }
        if (clonedBySpeaker[name]) { speakerChoices[name] = "clone"; applyChoice(name); return; }
        var male = 0, female = 0;
        segmentsData.forEach(function (s) { if (s.speaker === name) { if (s.gender === "female") female++; else male++; } });
        var g = female > male ? "female" : "male";
        var pool = voicePools[g];
        if (!pool.length) { g = (g === "male" ? "female" : "male"); pool = voicePools[g]; }
        if (!pool.length) return;
        var usedIds = {};
        Object.values(speakerVoices).forEach(function (v) { usedIds[v] = true; });
        var freeIdx = pool.map(function (p, i) { return i; }).filter(function (i) { return !usedIds[pool[i].voice_id]; });
        var pick = freeIdx.length ? freeIdx[Math.floor(Math.random() * freeIdx.length)] : Math.floor(Math.random() * pool.length);
        speakerChoices[name] = g + ":" + (pick + 1);
        applyChoice(name);
    });
    renderSpeakerVoices();
    notify("success", "Voices auto-assigned. Change any speaker's voice in the Step 4 table.");
}

// ---------- Timeline: yellow number band + original-start lines + ruler + drag ----------
function renderTimeline() {
    var wrap = document.getElementById("timelineWrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!segmentsData.length) return;
    var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
    var W = wrap.clientWidth || 900;
    var scale = W / total;

    var markerBand = document.createElement("div");
    markerBand.style.cssText = "position:relative;height:18px;background:#d4e157;";
    wrap.appendChild(markerBand);

    var ruler = document.createElement("div");
    ruler.style.cssText = "position:relative;height:24px;border-bottom:1px solid #475569;background:#0f172a;";
    var step = total <= 10 ? 1 : total <= 30 ? 5 : 10;
    for (var t = 0; t <= total; t += step) {
        var mark = document.createElement("div");
        mark.style.cssText = "position:absolute;left:" + (t * scale) + "px;top:0;height:100%;border-left:1px solid #475569;";
        var tlabel = document.createElement("span");
        tlabel.textContent = t + "s";
        tlabel.style.cssText = "position:absolute;left:3px;top:3px;color:#94a3b8;font-size:10px;white-space:nowrap;";
        mark.appendChild(tlabel);
        ruler.appendChild(mark);
    }
    wrap.appendChild(ruler);

    var speakers = []; var sSeen = {};
    segmentsData.forEach(function (s) { var n = s.speaker || "Speaker 1"; if (!sSeen[n]) { sSeen[n] = true; speakers.push(n); } });

    speakers.forEach(function (spk, li) {
        var lane = document.createElement("div");
        lane.style.cssText = "position:relative;height:34px;border-bottom:1px solid #334155;background:" + (li % 2 === 0 ? "#1e293b" : "#1a2332") + ";";
        var lab = document.createElement("span");
        lab.textContent = spk;
        lab.style.cssText = "position:absolute;left:4px;top:9px;color:#64748b;font-size:11px;z-index:5;pointer-events:none;";
        lane.appendChild(lab);

        segmentsData.forEach(function (seg, i) {
            if ((seg.speaker || "Speaker 1") !== spk || !(seg.arabic_text || "").trim()) return;
            var off = segmentOffsets[seg.segment_id] || 0;
            var box = document.createElement("div");
            var left = Math.max(0, (seg.start + off) * scale);
            var width = Math.max(8, (seg.end - seg.start) * scale);
            box.style.cssText = "position:absolute;left:" + left + "px;top:4px;width:" + width + "px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;z-index:2;";
            box.title = "Line " + (i + 1) + " — drag to shift. Yellow line = where this line starts in the ORIGINAL video.";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function (ev) {
                ev.preventDefault();
                var startX = ev.clientX;
                var startOff = off;
                var sameLane = segmentsData.filter(function (s, idx) { return idx !== i && (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim(); });
                var move = function (e2) {
                    var no = startOff + (e2.clientX - startX) / scale;
                    no = Math.max(-2, Math.min(2, no));
                    if (seg.start + no < 0) no = -seg.start;
                    if (seg.end + no > total) no = total - seg.end;
                    for (var k = 0; k < sameLane.length; k++) {
                        var nb = sameLane[k];
                        var nbOff = segmentOffsets[nb.segment_id] || 0;
                        var nbStart = nb.start + nbOff;
                        var nbEnd = nb.end + nbOff;
                        if (seg.start + no < nbEnd && seg.end + no > nbStart) {
                            if (no > startOff) { no = nbStart - seg.end; } else { no = nbEnd - seg.start; }
                        }
                    }
                    segmentOffsets[seg.segment_id] = no;
                    box.style.left = Math.max(0, (seg.start + no) * scale) + "px";
                    box.textContent = (i + 1) + " (" + (no > 0 ? "+" : "") + Math.round(no * 1000) + "ms)";
                };
                var up = function () {
                    document.removeEventListener("mousemove", move);
                    document.removeEventListener("mouseup", up);
                    renderTimeline();
                };
                document.addEventListener("mousemove", move);
                document.addEventListener("mouseup", up);
            };
            lane.appendChild(box);
        });
        wrap.appendChild(lane);
    });

    var overlay = document.createElement("div");
    overlay.style.cssText = "position:absolute;left:0;right:0;top:18px;bottom:0;pointer-events:none;z-index:6;";
    segmentsData.forEach(function (seg, i) {
        var x = seg.start * scale;
        var line = document.createElement("div");
        line.style.cssText = "position:absolute;left:" + x + "px;top:0;bottom:0;width:1px;background:rgba(212,225,87,0.8);";
        overlay.appendChild(line);
        var num = document.createElement("div");
        num.textContent = (i + 1);
        num.title = "Line " + (i + 1) + " — original start: " + seg.start + "s";
        num.style.cssText = "position:absolute;left:" + x + "px;top:-18px;transform:translateX(-50%);color:#1a1a2e;font-size:10px;font-weight:700;line-height:18px;padding:0 2px;";
        overlay.appendChild(num);
    });
    wrap.appendChild(overlay);
}