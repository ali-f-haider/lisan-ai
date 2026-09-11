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


// Compute the "allowed" time window for a line (same logic the server uses to trim/fade).
// Returns { allowedStart, allowedEnd } in seconds. Anything beyond allowedEnd is the faded/cut part.
function allowedWindowFor(seg) {
    var sorted = segmentsData.slice().sort(function (a, b) { return a.start - b.start; });
    var idx = -1;
    for (var k = 0; k < sorted.length; k++) { if (sorted[k].segment_id === seg.segment_id) { idx = k; break; } }
    if (idx < 0) return { allowedStart: seg.start, allowedEnd: seg.end };
    var nextStart = (idx + 1 < sorted.length) ? sorted[idx + 1].start : (totalDuration > 0 ? totalDuration : seg.end + 5);
    var allowedEnd = Math.min(seg.end, nextStart - 0.005); // 5ms guard, matches server
    return { allowedStart: seg.start, allowedEnd: Math.max(allowedEnd, seg.start + 0.05) };
}



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
	ok.onclick = () => { div.remove(); if (!panel.children.length) panel.style.display = "none"; };
    div.appendChild(m); div.appendChild(ok);
	panel.appendChild(div); panel.style.display = "flex";
    while (panel.children.length > 6) panel.removeChild(panel.firstChild);
	setTimeout(() => { if (div.parentNode) { div.remove(); if (!panel.children.length) panel.style.display = "none"; } }, NOTIFY_AUTO_CLOSE_MS);
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
	}) });
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

// --- Progress bar text restoration ---
const _origCheckTranscribe = checkTranscribeProgress;
checkTranscribeProgress = async function() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/${currentJobId}`);
    const data = await res.json();
    const fill = document.getElementById("progressFill");
    const txt = document.getElementById("progressText");
    if (fill && txt) {
        fill.style.width = data.percent + "%";
        txt.textContent = data.percent + "%" + (data.status_text ? " — " + data.status_text : "");
    }
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
};

const _origCheckGen = checkGenerateProgress;
checkGenerateProgress = async function() {
    const res = await fetch("/api/progress/generate");
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("genProgressFill");
    const txt = document.getElementById("genProgressText");
    if (fill && txt && typeof data.percent === "number") {
        fill.style.width = data.percent + "%";
        txt.textContent = data.percent + "%";
    }
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
};

// --- Alternating row colors per speaker group ---
function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    // Alternate background by speaker group
    const prevSpeaker = i > 0 ? segmentsData[i - 1].speaker : null;
    if (seg.speaker !== prevSpeaker) {
        // Count how many speaker-group transitions before this index
        let groupIdx = 0;
        for (let j = 1; j <= i; j++) { if (segmentsData[j].speaker !== segmentsData[j-1].speaker) groupIdx++; }
        row.style.background = groupIdx % 2 === 0 ? "#ffffff" : "#f8fafc";
    } else {
        // Same speaker as previous → inherit
        let groupIdx = 0;
        for (let j = 1; j <= i; j++) { if (segmentsData[j].speaker !== segmentsData[j-1].speaker) groupIdx++; }
        row.style.background = groupIdx % 2 === 0 ? "#ffffff" : "#f8fafc";
    }
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
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak THIS line only"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked: Auto-Fix, Translate and Tashkeel skip this line" : "Lock this line"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

// --- Fix clone analysis table population ---
async function analyzeSpeakers() {
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))].sort();
    const analysis = names.map(name => {
        const segs = segmentsData.filter(s => (s.speaker || "Speaker 1") === name && (s.text || "").trim());
        const total = segs.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
        const total_time = Math.round(total * 10) / 10;
        const n = segs.length;
        let status, message;
        if (total_time < 1.0) { status = "bad"; message = `❌ Cannot clone — only ${total_time}s of speech across ${n} line(s). At least 1 second is required. Use a library voice in Step 4 instead.`; }
        else if (total_time < 3.0) { status = "warning"; message = `⚠️ Barely enough (${total_time}s across ${n} line(s)). The clone will likely sound robotic. A library voice may sound better.`; }
        else if (total_time < 10.0) { status = "warning"; message = `🟡 Acceptable (${total_time}s across ${n} line(s)). Decent clone, but may not fully capture the speaker's character.`; }
        else if (total_time < 20.0) { status = "good"; message = `✅ Good (${total_time}s across ${n} line(s)). Enough audio for a natural clone.`; }
        else { status = "good"; message = `🌟 Excellent (${total_time}s across ${n} line(s)). Best possible clone quality.`; }
        return { speaker: name, total_time, num_segments: n, status, message };
    });
    const tbody = document.querySelector("#cloneAnalysisTable tbody");
    tbody.innerHTML = "";
    analysis.forEach(item => {
        const row = document.createElement("tr");
        const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = item.status !== "bad"; cb.dataset.speaker = item.speaker;
        const c0 = document.createElement("td"); c0.appendChild(cb); row.appendChild(c0);
        const c1 = document.createElement("td"); c1.textContent = item.speaker; row.appendChild(c1);
        const c2 = document.createElement("td"); c2.textContent = `${item.total_time}s (${item.num_segments} line${item.num_segments > 1 ? "s" : ""})`; row.appendChild(c2);
        const c3 = document.createElement("td"); c3.textContent = item.message; c3.style.color = item.status === "good" ? "#059669" : (item.status === "warning" ? "#d97706" : "#dc2626"); c3.style.fontSize = "0.9em"; row.appendChild(c3);
        tbody.appendChild(row);
    });
    document.getElementById("cloneAnalysisSection").classList.remove("hidden");
    notify("success", "Review the guidance. Speakers marked ❌ are unchecked automatically.");
}

// --- Timeline with ruler + overlap prevention ---
function renderTimeline() {
    const wrap = document.getElementById("timelineWrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!segmentsData.length) return;
    const total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(s => s.end).concat([1]));
    const W = wrap.clientWidth || 900;
    const scale = W / total;

    // Time ruler
    const ruler = document.createElement("div");
    ruler.style.cssText = "position:relative;height:24px;border-bottom:1px solid #475569;background:#0f172a;";
    const step = total <= 10 ? 1 : total <= 30 ? 5 : 10;
    for (let t = 0; t <= total; t += step) {
        const mark = document.createElement("div");
        mark.style.cssText = `position:absolute;left:${t * scale}px;top:0;height:100%;border-left:1px solid #475569;`;
        const label = document.createElement("span");
        label.textContent = t + "s";
        label.style.cssText = "position:absolute;left:3px;top:3px;color:#94a3b8;font-size:10px;white-space:nowrap;";
        mark.appendChild(label);
        ruler.appendChild(mark);
    }
    wrap.appendChild(ruler);

    const speakers = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    speakers.forEach((spk, li) => {
        const lane = document.createElement("div");
        lane.style.cssText = `position:relative;height:34px;border-bottom:1px solid #334155;background:${li % 2 === 0 ? "#1e293b" : "#1a2332"};`;
        const lab = document.createElement("span");
        lab.textContent = spk;
        lab.style.cssText = "position:absolute;left:4px;top:9px;color:#64748b;font-size:11px;z-index:5;pointer-events:none;";
        lane.appendChild(lab);
        segmentsData.forEach((seg, i) => {
            if ((seg.speaker || "Speaker 1") !== spk || !(seg.arabic_text || "").trim()) return;
            const off = segmentOffsets[seg.segment_id] || 0;
            const box = document.createElement("div");
            const left = Math.max(0, (seg.start + off) * scale);
            const width = Math.max(8, (seg.end - seg.start) * scale);
            box.style.cssText = `position:absolute;left:${left}px;top:4px;width:${width}px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;`;
            box.title = "Line " + (i + 1) + ": drag to shift";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function (ev) {
                ev.preventDefault();
                const startX = ev.clientX;
                const startOff = off;
                // Find neighbors in same speaker lane for overlap prevention
                const sameLane = segmentsData.filter((s, idx) => idx !== i && (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim());
                const move = function (e2) {
                    let no = startOff + (e2.clientX - startX) / scale;
                    no = Math.max(-2, Math.min(2, no));
                    const newStart = seg.start + no;
                    const newEnd = seg.end + no;
                    // Clamp to timeline bounds
                    if (newStart < 0) no = -seg.start;
                    if (newEnd > total) no = total - seg.end;
                    // Prevent overlap with neighbors
                    for (const nb of sameLane) {
                        const nbOff = segmentOffsets[nb.segment_id] || 0;
                        const nbStart = nb.start + nbOff;
                        const nbEnd = nb.end + nbOff;
                        const testStart = seg.start + no;
                        const testEnd = seg.end + no;
                        if (testStart < nbEnd && testEnd > nbStart) {
                            // Overlap detected — clamp
                            if (no > startOff) { no = nbStart - seg.end; } // pushing right → stop at neighbor start
                            else { no = nbEnd - seg.start; } // pushing left → stop at neighbor end
                        }
                    }
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

// ===== FIXED: Progress text (no double bar) =====
const _origCheckTranscribe2 = checkTranscribeProgress;
checkTranscribeProgress = async function() {
    if (!currentJobId) return;
    const res = await fetch(`/api/progress/${currentJobId}`);
    const data = await res.json();
    const fill = document.getElementById("progressFill");
    const txt = document.getElementById("progressText");
    if (fill) fill.style.width = (data.percent || 0) + "%";
    if (txt) txt.textContent = (data.percent || 0) + "%" + (data.status_text ? " — " + data.status_text : "") + "... please wait";
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
};

const _origCheckGen2 = checkGenerateProgress;
checkGenerateProgress = async function() {
    const res = await fetch("/api/progress/generate");
    const data = await res.json();
    if (!data || data.status === "not_found") return;
    const fill = document.getElementById("genProgressFill");
    const txt = document.getElementById("genProgressText");
    if (fill && typeof data.percent === "number") fill.style.width = data.percent + "%";
    if (txt && typeof data.percent === "number") txt.textContent = data.percent + "% — generating audio... please wait";
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
};

// ===== FIXED: Table row creation (no duplicate # column, aligned fields) =====
function createRow(seg, i) {
    const row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    // Alternating background by speaker group
    if (!seg.locked) {
        let groupIdx = 0;
        for (let j = 1; j <= i; j++) { if (segmentsData[j].speaker !== segmentsData[j - 1].speaker) groupIdx++; }
        row.style.background = groupIdx % 2 === 0 ? "#ffffff" : "#f8fafc";
    }

    const mk = (tag) => document.createElement(tag);

    // # column
    const numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.style.fontWeight = "600"; numCell.textContent = i + 1; row.appendChild(numCell);

    // Start
    const startCell = mk("td"); const si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = () => { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);

    // End
    const endCell = mk("td"); const ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = () => { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);

    // Speaker
    const spCell = mk("td"); const spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = () => updateSpeakerName(i, spI.value); spCell.appendChild(spI); row.appendChild(spCell);

    // Gender
    const gCell = mk("td"); const gS = mk("select"); ["male", "female"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = () => { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);

    // Style / Emotion (dropdown + textbox)
    const eCell = mk("td");
    const eWrap = mk("div"); eWrap.style.cssText = "display:flex;gap:3px;align-items:center;";
    const eS = mk("select"); eS.style.width = "auto"; eS.style.minWidth = "60px"; eS.style.flex = "none";
    const blank = mk("option"); blank.value = ""; blank.textContent = "＋"; eS.appendChild(blank);
    EMOTIONS.slice().sort().forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    ["confident, calm", "anxious, afraid", "calm, firm", "playful, teasing", "tired, sad", "angry, controlled"].forEach(v => { const o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    eS.title = "Pick a style tag to add it";
    eS.onchange = () => {
        if (!eS.value) return;
        const merged = (seg.emotion ? seg.emotion + ", " : "") + eS.value;
        const clean = sanitizeStyle(merged) || "neutral";
        seg.emotion = clean; eI.value = clean; eS.selectedIndex = 0; updateBadges();
    };
    const eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.placeholder = "tags…"; eI.title = "Speaking style — multiple tags, comma separated"; eI.style.flex = "1"; eI.style.minWidth = "80px";
    eI.onchange = () => {
        const clean = sanitizeStyle(eI.value) || "neutral";
        if (clean !== eI.value.trim()) notify("info", "Words not in the official list were removed. Style: '" + clean + "'.");
        eI.value = clean; seg.emotion = clean; updateBadges();
    };
    eWrap.appendChild(eS); eWrap.appendChild(eI); eCell.appendChild(eWrap); row.appendChild(eCell);

    // English
    const enCell = mk("td"); const enT = mk("textarea"); enT.value = seg.text; enT.onchange = () => { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);

    // Arabic
    const arCell = mk("td"); const arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = () => { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);

    // Actions
    const aCell = mk("td");
    const pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio"; pb.onclick = () => previewRow(i, pb);
    const rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak this line only"; rb.onclick = () => regenerateLine(i, rb);
    const ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = () => insertSegmentAfter(i);
    const db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = () => deleteSegment(i);
    const lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked" : "Lock this line"; lb.onclick = () => toggleLock(i);
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);

    return row;
}

// ===== FIXED: Remove duplicate # column injection =====
(function removeDuplicateNumCol() {
    const tr = document.querySelector("#segmentsTable thead tr");
    if (!tr) return;
    const ths = tr.querySelectorAll("th");
    // If first two THs both say "#", remove the second one (injected by old code)
    if (ths.length >= 2 && ths[0].textContent.trim() === "#" && ths[1].textContent.trim() === "#") {
        ths[1].remove();
    }
})();

// ===== FIXED: Clone analysis table (frontend-computed, always populates) =====
async function analyzeSpeakers() {
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))].sort();
    const analysis = names.map(name => {
        const segs = segmentsData.filter(s => (s.speaker || "Speaker 1") === name && (s.text || "").trim());
        const total = segs.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
        const total_time = Math.round(total * 10) / 10;
        const n = segs.length;
        let status, message;
        if (total_time < 1.0) { status = "bad"; message = `❌ Cannot clone — only ${total_time}s across ${n} line(s). Need ≥1s. Use a library voice in Step 4.`; }
        else if (total_time < 3.0) { status = "warning"; message = `⚠️ Barely enough (${total_time}s, ${n} line(s)). Clone may sound robotic.`; }
        else if (total_time < 10.0) { status = "warning"; message = `🟡 Acceptable (${total_time}s, ${n} line(s)). Decent clone.`; }
        else if (total_time < 20.0) { status = "good"; message = `✅ Good (${total_time}s, ${n} line(s)). Natural clone expected.`; }
        else { status = "good"; message = `🌟 Excellent (${total_time}s, ${n} line(s)). Best quality clone.`; }
        return { speaker: name, total_time, num_segments: n, status, message };
    });
    const tbody = document.querySelector("#cloneAnalysisTable tbody");
    tbody.innerHTML = "";
    analysis.forEach(item => {
        const row = document.createElement("tr");
        const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = item.status !== "bad"; cb.dataset.speaker = item.speaker;
        const c0 = document.createElement("td"); c0.style.textAlign = "center"; c0.appendChild(cb); row.appendChild(c0);
        const c1 = document.createElement("td"); c1.textContent = item.speaker; row.appendChild(c1);
        const c2 = document.createElement("td"); c2.textContent = `${item.total_time}s (${item.num_segments} line${item.num_segments > 1 ? "s" : ""})`; row.appendChild(c2);
        const c3 = document.createElement("td"); c3.textContent = item.message; c3.style.color = item.status === "good" ? "#059669" : (item.status === "warning" ? "#d97706" : "#dc2626"); c3.style.fontSize = "0.9em"; row.appendChild(c3);
        tbody.appendChild(row);
    });
    document.getElementById("cloneAnalysisSection").classList.remove("hidden");
    notify("success", "Review the guidance below. Speakers marked ❌ are unchecked automatically.");
}

// ===== FIXED: Timeline with ruler + overlap prevention =====
function renderTimeline() {
    const wrap = document.getElementById("timelineWrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!segmentsData.length) return;
    const total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(s => s.end).concat([1]));
    const W = wrap.clientWidth || 900;
    const scale = W / total;

    // Time ruler
    const ruler = document.createElement("div");
    ruler.style.cssText = "position:relative;height:24px;border-bottom:1px solid #475569;background:#0f172a;";
    const step = total <= 10 ? 1 : total <= 30 ? 5 : 10;
    for (let t = 0; t <= total; t += step) {
        const mark = document.createElement("div");
        mark.style.cssText = `position:absolute;left:${t * scale}px;top:0;height:100%;border-left:1px solid #475569;`;
        const label = document.createElement("span");
        label.textContent = t + "s";
        label.style.cssText = "position:absolute;left:3px;top:3px;color:#94a3b8;font-size:10px;white-space:nowrap;";
        mark.appendChild(label);
        ruler.appendChild(mark);
    }
    wrap.appendChild(ruler);

    const speakers = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    speakers.forEach((spk, li) => {
        const lane = document.createElement("div");
        lane.style.cssText = `position:relative;height:34px;border-bottom:1px solid #334155;background:${li % 2 === 0 ? "#1e293b" : "#1a2332"};`;
        const lab = document.createElement("span");
        lab.textContent = spk;
        lab.style.cssText = "position:absolute;left:4px;top:9px;color:#64748b;font-size:11px;z-index:5;pointer-events:none;";
        lane.appendChild(lab);
        segmentsData.forEach((seg, i) => {
            if ((seg.speaker || "Speaker 1") !== spk || !(seg.arabic_text || "").trim()) return;
            const off = segmentOffsets[seg.segment_id] || 0;
            const box = document.createElement("div");
            const left = Math.max(0, (seg.start + off) * scale);
            const width = Math.max(8, (seg.end - seg.start) * scale);
            box.style.cssText = `position:absolute;left:${left}px;top:4px;width:${width}px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;`;
            box.title = "Line " + (i + 1) + ": drag to shift";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function (ev) {
                ev.preventDefault();
                const startX = ev.clientX;
                const startOff = off;
                const sameLane = segmentsData.filter((s, idx) => idx !== i && (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim());
                const move = function (e2) {
                    let no = startOff + (e2.clientX - startX) / scale;
                    no = Math.max(-2, Math.min(2, no));
                    if (seg.start + no < 0) no = -seg.start;
                    if (seg.end + no > total) no = total - seg.end;
                    for (const nb of sameLane) {
                        const nbOff = segmentOffsets[nb.segment_id] || 0;
                        const nbStart = nb.start + nbOff;
                        const nbEnd = nb.end + nbOff;
                        const testStart = seg.start + no;
                        const testEnd = seg.end + no;
                        if (testStart < nbEnd && testEnd > nbStart) {
                            if (no > startOff) { no = nbStart - seg.end; }
                            else { no = nbEnd - seg.start; }
                        }
                    }
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

// ===== FIX: Step 4 speaker voices table population =====
async function renderSpeakerVoices() {
    const tbody = document.querySelector("#speakerVoicesTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    const names = [...new Set(segmentsData.map(s => s.speaker || "Speaker 1"))];
    if (!names.length) return;
    const hasPools = voicePools.male.length > 0 || voicePools.female.length > 0;
    if (!hasPools) await ensureVoicePools();
    names.forEach(name => {
        const row = document.createElement("tr");
        const c1 = document.createElement("td"); c1.textContent = name; c1.style.fontWeight = "600"; row.appendChild(c1);
        const c2 = document.createElement("td");
        const sel = document.createElement("select");
        sel.style.width = "100%";
        // Cloned voice option
        if (clonedBySpeaker[name]) {
            const o = document.createElement("option"); o.value = "clone"; o.textContent = "🎙️ Cloned voice (from video)"; sel.appendChild(o);
        }
        // Male voices
        const addGroup = (g, label) => {
            (voicePools[g] || []).slice(0, 8).forEach((p, i) => {
                const o = document.createElement("option"); o.value = g + ":" + (i + 1); o.textContent = label + " " + (i + 1); sel.appendChild(o);
            });
        };
        addGroup("male", "🎲 Male voice");
        addGroup("female", "🎲 Female voice");
        // Fallback if no pools loaded
        if (!clonedBySpeaker[name] && !voicePools.male.length && !voicePools.female.length) {
            const o = document.createElement("option"); o.value = ""; o.textContent = "— Click 'Load Voice Options' first —"; sel.appendChild(o);
        }
        sel.value = speakerChoices[name] || "";
        sel.onchange = () => { speakerChoices[name] = sel.value; applyChoice(name); renderSpeakerVoices(); };
        c2.appendChild(sel);
        // Show current assignment
        const info = document.createElement("div");
        info.style.cssText = "font-size:12px;color:#6b7280;margin-top:4px;";
        info.textContent = speakerVoiceNames[name] || "";
        c2.appendChild(info);
        row.appendChild(c2);
        tbody.appendChild(row);
    });
}

// ===== FIX: Ensure voice pools load properly =====
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
    if (ok) { notify("success", "Voice options loaded. Pick a voice per speaker below."); renderSpeakerVoices(); }
    else notify("error", "Could not load the voice library. Check the server configuration.");
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

// ===== USER HEADER =====
(function loadUserInfo() {
    fetch("/api/user/info").then(function(r) { return r.json(); }).then(function(data) {
        var nameEl = document.getElementById("userName");
        var credEl = document.getElementById("creditsDisplay");
        var credNum = document.getElementById("creditsNum");
        if (nameEl) nameEl.textContent = data.name || "";
        if (!data.is_guest && credEl && credNum) {
            credNum.textContent = data.credits;
            credEl.style.display = "inline-block";
        }
    }).catch(function() {});
})();

function doLogout() {
    fetch("/api/logout", { method: "POST" }).then(function() {
        // Also clear Supabase session if available
        if (typeof window.supabase !== "undefined" && window.__SUPABASE_URL) {
            try {
                var sb = window.supabase.createClient(window.__SUPABASE_URL, window.__SUPABASE_KEY || "");
                sb.auth.signOut();
            } catch(e) {}
        }
        window.location.href = "/login";
    }).catch(function() {
        window.location.href = "/login";
    });
}

function refreshCredits() {
    fetch("/api/user/info").then(function(r) { return r.json(); }).then(function(data) {
        var credNum = document.getElementById("creditsNum");
        if (credNum && !data.is_guest) credNum.textContent = data.credits;
    }).catch(function() {});
}



// ===== FILE UPLOAD LABEL =====
function onFileSelected(input) {
    var label = document.getElementById("fileUploadText");
    if (input.files && input.files[0]) {
        var f = input.files[0];
        var sizeMB = (f.size / (1024 * 1024)).toFixed(1);
        label.textContent = f.name + " (" + sizeMB + " MB)";
    } else {
        label.textContent = "Choose File";
    }
}

// ===== USER HEADER =====
(function loadUserInfo() {
    fetch("/api/user/info").then(function(r) { return r.json(); }).then(function(data) {
        var nameEl = document.getElementById("userName");
        var credEl = document.getElementById("creditsDisplay");
        var credNum = document.getElementById("creditsNum");
        if (nameEl) nameEl.textContent = data.name || "";
        if (!data.is_guest && credEl && credNum) {
            credNum.textContent = data.credits;
            credEl.style.display = "inline-block";
        }
    }).catch(function() {});
})();

function doLogout() {
    fetch("/api/logout", { method: "POST" }).then(function() {
        window.location.href = "/login";
    }).catch(function() { window.location.href = "/login"; });
}

function refreshCredits() {
    fetch("/api/user/info").then(function(r) { return r.json(); }).then(function(data) {
        var credNum = document.getElementById("creditsNum");
        if (credNum && !data.is_guest) credNum.textContent = data.credits;
    }).catch(function() {});
}

// ===== AUDIOSR TOGGLE — send preference with generate =====
var _origGenerateAudio = generateAudio;
generateAudio = function() {
    var enhance = document.getElementById("enhanceBackground");
    window._enhanceBackground = enhance ? enhance.checked : false;
    _origGenerateAudio();
};

// ===== FIXED TABLE ROW (locked = yellow, alternating) =====
function createRow(seg, i) {
    var row = document.createElement("tr");
    if (seg.locked) row.className = "locked";
    if (!seg.locked) {
        var groupIdx = 0;
        for (var j = 1; j <= i; j++) { if (segmentsData[j].speaker !== segmentsData[j - 1].speaker) groupIdx++; }
        row.style.background = groupIdx % 2 === 0 ? "#ffffff" : "#f8fafc";
    }
    var mk = function(tag) { return document.createElement(tag); };
    var numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.style.fontWeight = "600"; numCell.textContent = i + 1; row.appendChild(numCell);
    var startCell = mk("td"); var si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = function() { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    var endCell = mk("td"); var ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = function() { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    var spCell = mk("td"); var spI = mk("input"); spI.type = "text"; spI.value = seg.speaker; spI.onchange = function() { updateSpeakerName(i, spI.value); }; spCell.appendChild(spI); row.appendChild(spCell);
    var gCell = mk("td"); var gS = mk("select"); ["male", "female"].forEach(function(v) { var o = mk("option"); o.value = v; o.textContent = v; o.selected = (seg.gender === v); gS.appendChild(o); }); gS.onchange = function() { segmentsData[i].gender = gS.value; }; gCell.appendChild(gS); row.appendChild(gCell);
    var eCell = mk("td");
    var eWrap = mk("div"); eWrap.style.cssText = "display:flex;gap:3px;align-items:center;";
    var eS = mk("select"); eS.style.width = "auto"; eS.style.minWidth = "60px"; eS.style.flex = "none";
    var blank = mk("option"); blank.value = ""; blank.textContent = "＋"; eS.appendChild(blank);
    EMOTIONS.slice().sort().forEach(function(v) { var o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    ["confident, calm", "anxious, afraid", "calm, firm", "playful, teasing", "tired, sad", "angry, controlled"].forEach(function(v) { var o = mk("option"); o.value = v; o.textContent = v; eS.appendChild(o); });
    eS.onchange = function() {
        if (!eS.value) return;
        var merged = (seg.emotion ? seg.emotion + ", " : "") + eS.value;
        var clean = sanitizeStyle(merged) || "neutral";
        seg.emotion = clean; eI.value = clean; eS.selectedIndex = 0; updateBadges();
    };
    var eI = mk("input"); eI.type = "text"; eI.list = "emotionList"; eI.value = seg.emotion || "neutral"; eI.placeholder = "tags…"; eI.style.flex = "1"; eI.style.minWidth = "80px";
    eI.onchange = function() {
        var clean = sanitizeStyle(eI.value) || "neutral";
        if (clean !== eI.value.trim()) notify("info", "Words not in the official list were removed. Style: '" + clean + "'.");
        eI.value = clean; seg.emotion = clean; updateBadges();
    };
    eWrap.appendChild(eS); eWrap.appendChild(eI); eCell.appendChild(eWrap); row.appendChild(eCell);
    var enCell = mk("td"); var enT = mk("textarea"); enT.value = seg.text; enT.onchange = function() { segmentsData[i].text = enT.value; updateBadges(); }; enCell.appendChild(enT); row.appendChild(enCell);
    var arCell = mk("td"); var arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = function() { segmentsData[i].arabic_text = arT.value; updateBadges(); }; arCell.appendChild(arT); row.appendChild(arCell);
    var aCell = mk("td");
    var pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio"; pb.onclick = function() { previewRow(i, pb); };
    var rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak this line only"; rb.onclick = function() { regenerateLine(i, rb); };
    var ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = function() { insertSegmentAfter(i); };
    var db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = function() { deleteSegment(i); };
    var lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked" : "Lock this line"; lb.onclick = function() { toggleLock(i); };
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    row.appendChild(aCell);
    return row;
}

// ===== CLONE ANALYSIS (frontend-computed) =====
async function analyzeSpeakers() {
    if (!segmentsData.length) { notify("error", "No segments found."); return; }
    var names = [];
    var seen = {};
    segmentsData.forEach(function(s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    names.sort();
    var analysis = names.map(function(name) {
        var segs = segmentsData.filter(function(s) { return (s.speaker || "Speaker 1") === name && (s.text || "").trim(); });
        var total = segs.reduce(function(a, s) { return a + Math.max(0, s.end - s.start); }, 0);
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
    analysis.forEach(function(item) {
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

// ===== SPEAKER VOICES TABLE =====
async function renderSpeakerVoices() {
    var tbody = document.querySelector("#speakerVoicesTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    var names = [];
    var seen = {};
    segmentsData.forEach(function(s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    if (!names.length) return;
    var hasPools = voicePools.male.length > 0 || voicePools.female.length > 0;
    if (!hasPools) await ensureVoicePools();
    names.forEach(function(name) {
        var row = document.createElement("tr");
        var c1 = document.createElement("td"); c1.textContent = name; c1.style.fontWeight = "600"; row.appendChild(c1);
        var c2 = document.createElement("td");
        var sel = document.createElement("select"); sel.style.width = "100%";
        if (clonedBySpeaker[name]) { var o = document.createElement("option"); o.value = "clone"; o.textContent = "🎙️ Cloned voice (from video)"; sel.appendChild(o); }
        var addGroup = function(g, label) {
            (voicePools[g] || []).slice(0, 8).forEach(function(p, i) {
                var o = document.createElement("option"); o.value = g + ":" + (i + 1); o.textContent = label + " " + (i + 1); sel.appendChild(o);
            });
        };
        addGroup("male", "🎲 Male voice");
        addGroup("female", "🎲 Female voice");
        if (!clonedBySpeaker[name] && !voicePools.male.length && !voicePools.female.length) {
            var o = document.createElement("option"); o.value = ""; o.textContent = "— Click 'Load Voice Options' first —"; sel.appendChild(o);
        }
        sel.value = speakerChoices[name] || "";
        sel.onchange = function() { speakerChoices[name] = sel.value; applyChoice(name); renderSpeakerVoices(); };
        c2.appendChild(sel);
        var info = document.createElement("div");
        info.style.cssText = "font-size:12px;color:#6b7280;margin-top:4px;";
        info.textContent = speakerVoiceNames[name] || "";
        c2.appendChild(info);
        row.appendChild(c2);
        tbody.appendChild(row);
    });
}

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

async function loadVoiceOptions() {
    var ok = await ensureVoicePools();
    if (ok) { notify("success", "Voice options loaded. Pick a voice per speaker below."); renderSpeakerVoices(); }
    else notify("error", "Could not load the voice library.");
}

async function autoAssignVoices() {
    var ok = await ensureVoicePools();
    if (!ok) { notify("error", "Voice library unavailable."); return; }
    var names = [];
    var seen = {};
    segmentsData.forEach(function(s) { var n = s.speaker || "Speaker 1"; if (!seen[n]) { seen[n] = true; names.push(n); } });
    names.forEach(function(name) {
        if (speakerChoices[name]) { applyChoice(name); return; }
        if (clonedBySpeaker[name]) { speakerChoices[name] = "clone"; applyChoice(name); return; }
        var male = 0, female = 0;
        segmentsData.forEach(function(s) { if (s.speaker === name) { if (s.gender === "female") female++; else male++; } });
        var g = female > male ? "female" : "male";
        var pool = voicePools[g];
        if (!pool.length) { g = (g === "male" ? "female" : "male"); pool = voicePools[g]; }
        if (!pool.length) return;
        var usedIds = {};
        Object.values(speakerVoices).forEach(function(v) { usedIds[v] = true; });
        var freeIdx = pool.map(function(p, i) { return i; }).filter(function(i) { return !usedIds[pool[i].voice_id]; });
        var pick = freeIdx.length ? freeIdx[Math.floor(Math.random() * freeIdx.length)] : Math.floor(Math.random() * pool.length);
        speakerChoices[name] = g + ":" + (pick + 1);
        applyChoice(name);
    });
    renderSpeakerVoices();
    notify("success", "Voices auto-assigned. Change any speaker's voice in the Step 4 table.");
}

// ===== TIMELINE WITH RULER + OVERLAP PREVENTION =====
function renderTimeline() {
    var wrap = document.getElementById("timelineWrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!segmentsData.length) return;
    var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function(s) { return s.end; }).concat([1]));
    var W = wrap.clientWidth || 900;
    var scale = W / total;
    var ruler = document.createElement("div");
    ruler.style.cssText = "position:relative;height:24px;border-bottom:1px solid #475569;background:#0f172a;";
    var step = total <= 10 ? 1 : total <= 30 ? 5 : 10;
    for (var t = 0; t <= total; t += step) {
        var mark = document.createElement("div");
        mark.style.cssText = "position:absolute;left:" + (t * scale) + "px;top:0;height:100%;border-left:1px solid #475569;";
        var label = document.createElement("span");
        label.textContent = t + "s";
        label.style.cssText = "position:absolute;left:3px;top:3px;color:#94a3b8;font-size:10px;white-space:nowrap;";
        mark.appendChild(label);
        ruler.appendChild(mark);
    }
    wrap.appendChild(ruler);
    var speakers = [];
    var sSeen = {};
    segmentsData.forEach(function(s) { var n = s.speaker || "Speaker 1"; if (!sSeen[n]) { sSeen[n] = true; speakers.push(n); } });
    speakers.forEach(function(spk, li) {
        var lane = document.createElement("div");
        lane.style.cssText = "position:relative;height:34px;border-bottom:1px solid #334155;background:" + (li % 2 === 0 ? "#1e293b" : "#1a2332") + ";";
        var lab = document.createElement("span");
        lab.textContent = spk;
        lab.style.cssText = "position:absolute;left:4px;top:9px;color:#64748b;font-size:11px;z-index:5;pointer-events:none;";
        lane.appendChild(lab);
        segmentsData.forEach(function(seg, i) {
            if ((seg.speaker || "Speaker 1") !== spk || !(seg.arabic_text || "").trim()) return;
            var off = segmentOffsets[seg.segment_id] || 0;
            var box = document.createElement("div");
            var left = Math.max(0, (seg.start + off) * scale);
            var width = Math.max(8, (seg.end - seg.start) * scale);
            box.style.cssText = "position:absolute;left:" + left + "px;top:4px;width:" + width + "px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;";
            box.title = "Line " + (i + 1) + ": drag to shift";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function(ev) {
                ev.preventDefault();
                var startX = ev.clientX;
                var startOff = off;
                var sameLane = segmentsData.filter(function(s, idx) { return idx !== i && (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim(); });
                var move = function(e2) {
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
                var up = function() { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); renderTimeline(); };
                document.addEventListener("mousemove", move);
                document.addEventListener("mouseup", up);
            };
            lane.appendChild(box);
        });
        wrap.appendChild(lane);
    });
}

// ===== SAFE PROGRESS MESSAGES — does NOT replace real progress =====
var transcribeLastPercent = 0;
var generateLastPercent = 0;
var transcribeLastUpdate = Date.now();
var generateLastUpdate = Date.now();

var friendlyWaitMessages = [
    "The system is processing audio carefully. Please keep this page open.",
    "Speaker detection and vocal separation are the slowest steps.",
    "If the percentage is moving, everything is fine.",
    "High-quality processing takes longer, but gives better results.",
    "Please do not refresh the page while processing.",
    "The server is still alive. Waiting for the next processing update.",
    "Some steps may stay at one percentage for a while, especially speaker detection.",
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
    }, 60000); // every 1 minute, as requested
}

function stopFriendlyMessages(progressTextId) {
    if (friendlyTimer) {
        clearInterval(friendlyTimer);
        friendlyTimer = null;
    }
    var el = document.getElementById(progressTextId + "_friendly");
    if (el) el.remove();
}

function safePercent(value, fallback) {
    var n = Number(value);
    if (isNaN(n)) return fallback || 0;
    return Math.max(fallback || 0, Math.min(100, n));
}

// ===== SAFE TRANSCRIBE PROGRESS OVERRIDE =====
checkTranscribeProgress = async function () {
    if (!currentJobId) return;

    try {
        var res = await fetch("/api/progress/" + currentJobId + "?t=" + Date.now());
        var data = await res.json();

        var fill = document.getElementById("progressFill");
        var txt = document.getElementById("progressText");

        var percent = safePercent(data.percent, transcribeLastPercent);
        if (percent >= transcribeLastPercent) {
            transcribeLastPercent = percent;
        }
        transcribeLastUpdate = Date.now();

        if (fill) fill.style.width = transcribeLastPercent + "%";

        var statusText = data.status_text || data.message || data.status || "Processing...";
        if (txt) {
            txt.textContent =
                transcribeLastPercent + "% — " + statusText;
        }

        if (data.status === "processing") {
            startFriendlyMessages("progressText");
        }

        if (data.is_video !== undefined) {
            isVideoUpload = data.is_video;
        }

        if (data.status === "done") {
            stopFriendlyMessages("progressText");
            clearInterval(transcribePollTimer);

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
            if (data.detected_speakers > 0) {
                message += " Detected speakers: " + data.detected_speakers + ".";
            }

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
            if (txt) txt.textContent = "Error — " + (data.error || "Unknown error");
            notify("error", data.error || "Transcription failed.");
        }

    } catch (e) {
        var txt = document.getElementById("progressText");
        if (txt) {
            txt.textContent = transcribeLastPercent + "% — Still waiting for server response...";
        }
        console.error("Progress check failed:", e);
    }
};

// ===== SAFE GENERATE PROGRESS OVERRIDE =====
checkGenerateProgress = async function () {
    try {
        var res = await fetch("/api/progress/generate?t=" + Date.now());
        var data = await res.json();

        if (!data || data.status === "not_found") return;

        var fill = document.getElementById("genProgressFill");
        var txt = document.getElementById("genProgressText");

        var percent = safePercent(data.percent, generateLastPercent);
        if (percent >= generateLastPercent) {
            generateLastPercent = percent;
        }
        generateLastUpdate = Date.now();

        if (fill) fill.style.width = generateLastPercent + "%";

        var statusText = data.status_text || data.message || data.status || "Generating...";
        if (txt) {
            txt.textContent = generateLastPercent + "% — " + statusText;
        }

        if (data.status === "processing") {
            startFriendlyMessages("genProgressText");
        }

        if (data.status === "done") {
            stopFriendlyMessages("genProgressText");
            clearInterval(generatePollTimer);
			generatePollTimer = null;
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

            if (isVideoUpload) {
                document.getElementById("mergeSection").classList.remove("hidden");
            }

            fetchUsage();
            updateBadges();
            if (typeof refreshCredits === "function") refreshCredits();
        }

        if (data.status === "error") {
            stopFriendlyMessages("genProgressText");
            clearInterval(generatePollTimer);
			generatePollTimer = null;
            generateLastPercent = 0;

            var btn2 = document.getElementById("generateButton");
            if (btn2) btn2.disabled = false;

            if (txt) txt.textContent = "Error — " + (data.error || "Unknown error");
            notify("error", data.error || "Audio generation failed.");
        }

    } catch (e) {
        var txt = document.getElementById("genProgressText");
        if (txt) {
            txt.textContent = generateLastPercent + "% — Still waiting for server response...";
        }
        console.error("Generate progress check failed:", e);
    }
};


// ===== ADD-ON: yellow original-start markers (wraps renderTimeline, deletes nothing) =====
(function () {
    if (typeof renderTimeline !== "function") return;
    var _baseRenderTimeline = renderTimeline;
    renderTimeline = function () {
        _baseRenderTimeline();
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var W = wrap.clientWidth || 900;
        var scale = W / total;
        var band = document.createElement("div");
        band.style.cssText = "position:relative;height:18px;background:#d4e157;border-radius:8px 8px 0 0;";
        wrap.insertBefore(band, wrap.firstChild);
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
    };
})();

// ===== ADD-ON: New-project reset (second video starts clean) =====
function resetWorkspace() {
    // Stop any running pollers / friendly messages
    try { clearInterval(transcribePollTimer); } catch (e) {}
    try { clearInterval(generatePollTimer); } catch (e) {}
    if (typeof stopFriendlyMessages === "function") {
        stopFriendlyMessages("progressText");
        stopFriendlyMessages("genProgressText");
    }

    // Clear all job data
    segmentsData = [];
    originalSegments = [];
    totalDuration = 0;
    currentJobId = null;
    segmentOffsets = {};
    speakerChoices = {};
    speakerVoices = {};
    speakerVoiceNames = {};
    clonedBySpeaker = {};
    isVideoUpload = false;
    // NOTE: voicePools / availableVoices are kept on purpose —
    // they are your account-level studio library, not job data.

    // Hide all result/edit sections
    ["editorSection", "voicesSection", "cloneAnalysisSection", "speakerVoicesSection",
     "generateSection", "resultSection", "timelineSection", "mergeSection", "lipsyncSection"]
        .forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.add("hidden");
        });

    // Clear tables and results
    var tb = document.querySelector("#segmentsTable tbody"); if (tb) tb.innerHTML = "";
    var cb = document.querySelector("#cloneAnalysisTable tbody"); if (cb) cb.innerHTML = "";
    var vb = document.querySelector("#speakerVoicesTable tbody"); if (vb) vb.innerHTML = "";
    var ar = document.getElementById("audioResults"); if (ar) ar.innerHTML = "";
    var vr = document.getElementById("videoResults");
    if (vr) { vr.innerHTML = ""; vr.classList.add("hidden"); }
    var tw = document.getElementById("timelineWrap"); if (tw) tw.innerHTML = "";

    // Reset progress bars
    var pf = document.getElementById("progressFill"); if (pf) pf.style.width = "0%";
    var pt = document.getElementById("progressText"); if (pt) pt.textContent = "Starting...";
    var ps = document.getElementById("progressSection"); if (ps) ps.classList.add("hidden");
    var gf = document.getElementById("genProgressFill"); if (gf) gf.style.width = "0%";
    var gt = document.getElementById("genProgressText"); if (gt) gt.textContent = "Starting...";
    var gp = document.getElementById("genProgress"); if (gp) gp.classList.add("hidden");
    var ep = document.getElementById("emotionProgress"); if (ep) ep.classList.add("hidden");

    // Reset speaker count + badges
    var sc = document.getElementById("speakerCount"); if (sc) sc.value = "";
    if (typeof updateBadges === "function") updateBadges();
}

// When a NEW file is chosen: warn if a project exists, then reset
(function () {
    if (typeof onFileSelected !== "function") return;
    var _origOnFile = onFileSelected;
    onFileSelected = function (input) {
        if (input.files && input.files[0]) {
            if (segmentsData.length &&
                !confirm("Choosing a new file will clear the current project (segments, translations, voices). Continue?")) {
                input.value = "";
                var fl = document.getElementById("fileUploadText");
                if (fl) fl.textContent = "Choose File";
                return;
            }
            resetWorkspace();
        }
        _origOnFile(input);
    };
})();

// "Dub Another Video" button injected into Step 6
(function () {
    var target = document.getElementById("resultSection");
    if (!target) return;
    var btn = document.createElement("button");
    btn.className = "blue";
    btn.style.marginTop = "16px";
    btn.textContent = "🆕 Dub Another Video";
    btn.onclick = function () {
        resetWorkspace();
        var fi = document.getElementById("audioFile"); if (fi) fi.value = "";
        var fl = document.getElementById("fileUploadText"); if (fl) fl.textContent = "Choose File";
        window.scrollTo({ top: 0, behavior: "smooth" });
        notify("info", "Workspace cleared. Upload your next video in Step 1.");
    };
    target.appendChild(btn);
})();

// ===== ADD-ON: download protection, safe reset, loaded-project media guard =====
var workspaceHasMedia = true;
var resultsExist = false;
var resultsDownloaded = false;

function resetFileLabel() {
    var fl = document.getElementById("fileUploadText");
    if (fl) fl.textContent = "Choose File";
}

function confirmResetSafe() {
    if (resultsExist && !resultsDownloaded) {
        if (!confirm("⚠️ You generated audio/video that has NOT been downloaded yet.\n\nIf you continue, it will be lost forever (server files are temporary).\n\nDid you download everything you need?")) return false;
    }
    if (segmentsData.length) {
        if (!confirm("This clears the current project (segments, translations, voices).\nTip: use 💾 Save Project first if you want to keep it.\n\nContinue?")) return false;
    }
    return true;
}

// Detect: results appeared / a download link was clicked
(function () {
    var rs = document.getElementById("resultSection");
    if (rs && typeof MutationObserver !== "undefined") {
        new MutationObserver(function () {
            if (!rs.classList.contains("hidden")) { resultsExist = true; resultsDownloaded = false; }
        }).observe(rs, { attributes: true, attributeFilter: ["class"] });
    }
    document.addEventListener("click", function (e) {
        var t = e.target;
        var a = (t && t.closest) ? t.closest('a[href*="/api/download/"]') : null;
        if (a) resultsDownloaded = true;
    }, true);
})();

// Warn when closing tab / pressing back / reloading with undownloaded results (or mid-generation)
window.addEventListener("beforeunload", function (e) {
    var generating = false;
    try { generating = !!generatePollTimer; } catch (err) {}
    if (window._internalNav && !window._forceWarn) return;
    if ((resultsExist && !resultsDownloaded) || generating) { e.preventDefault(); e.returnValue = ""; return ""; }
});

// Yellow banner for loaded projects (no media on server)
function showMediaBanner() {
    var ed = document.getElementById("editorSection");
    if (!ed) return;
    var b = document.getElementById("noMediaBanner");
    if (!b) {
        b = document.createElement("div");
        b.id = "noMediaBanner";
        b.style.cssText = "margin:0 0 12px;padding:10px 14px;background:#fef3c7;border:1px solid #fbbf24;border-radius:10px;font-size:13px;color:#92400e;";
        b.innerHTML = "📼 <strong>Loaded project:</strong> the original audio/video is not on the server (files are temporary). Preview ▶, re-speak 🔄, emotion detection, auto-fix and video merge are disabled. You can still edit text, translate, add tashkeel, export SRT/SBV and generate the Arabic MP3.";
        ed.insertBefore(b, ed.children[1] || null);
    }
    b.style.display = "block";
}
function hideMediaBanner() {
    var b = document.getElementById("noMediaBanner");
    if (b) b.style.display = "none";
}

// Guard media-dependent actions when the workspace has no media on server
["previewRow", "regenerateLine", "detectEmotions", "autoFixTiming", "mergeVideo"].forEach(function (name) {
    if (typeof window[name] === "function") {
        var orig = window[name];
        window[name] = function () {
            if (!workspaceHasMedia) {
                notify("error", "📼 Loaded projects have no media on the server. Preview, re-speak, emotions, auto-fix and merge need a fresh upload. Editing, translate, tashkeel, SRT export and Generate still work.");
                return;
            }
            return orig.apply(this, arguments);
        };
    }
});

// Mark workspace as media-less after loading a JSON project
(function () {
    if (typeof loadProjectFile !== "function") return;
    var orig = loadProjectFile;
    loadProjectFile = function (ev) {
        var r = orig(ev);
        workspaceHasMedia = false;
        showMediaBanner();
        notify("info", "Project loaded. Original media is not on the server — media features are disabled (see the yellow notice).");
        return r;
    };
})();

// Reset also restores media flag + hides banner + clears result flags
(function () {
    if (typeof resetWorkspace !== "function") return;
    var orig = resetWorkspace;
    resetWorkspace = function () {
        orig();
        workspaceHasMedia = true;
        resultsExist = false;
        resultsDownloaded = false;
        hideMediaBanner();
    };
})();

// File selection: single safe confirm (download warning + project warning), no double dialogs
(function () {
    if (typeof onFileSelected !== "function") return;
    var prev = onFileSelected;
    onFileSelected = function (input) {
        if (input.files && input.files[0]) {
            if (!confirmResetSafe()) { input.value = ""; resetFileLabel(); return; }
            resetWorkspace(); // clears segments so the inner wrapper won't ask twice
        }
        prev(input);
    };
})();

// "Dub Another Video" button: same safe confirm before wiping
(function () {
    var btns = document.querySelectorAll("#resultSection button");
    for (var i = 0; i < btns.length; i++) {
        if (btns[i].textContent.indexOf("Dub Another Video") > -1) {
            btns[i].onclick = function () {
                if (!confirmResetSafe()) return;
                resetWorkspace();
                var fi = document.getElementById("audioFile"); if (fi) fi.value = "";
                resetFileLabel();
                window.scrollTo({ top: 0, behavior: "smooth" });
                notify("info", "Workspace cleared. Upload your next video in Step 1.");
            };
        }
    }
})();

// ===== ADD-ON: Buy credits modal, post-purchase refresh, balance guards =====
(function injectBuyUI() {
    var bar = document.getElementById("userBar");
    if (!bar || document.getElementById("buyBtn")) return;
    var right = bar.children[1];
    var btn = document.createElement("button");
    btn.id = "buyBtn";
    btn.className = "btn-logout";
    btn.style.cssText = "background:#ecfdf5;border-color:#bbf7d0;color:#059669;font-weight:700;";
    btn.textContent = "➕ Buy";
    btn.onclick = openBuyModal;
    right.insertBefore(btn, right.lastElementChild);

    var modal = document.createElement("div");
    modal.id = "buyModal";
    modal.style.cssText = "display:none;position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:200;align-items:center;justify-content:center;";
    modal.innerHTML = '<div style="background:#fff;border-radius:16px;padding:28px;width:340px;max-width:92vw;box-shadow:0 20px 60px rgba(0,0,0,0.3);">' +
        '<h3 style="color:#1a237e;margin-bottom:4px;">Buy Credits</h3>' +
        '<p style="font-size:12px;color:#6b7280;margin-bottom:16px;">100 credits = $1.00 · Credits never expire · Secure payment by Stripe</p>' +
        '<div id="buyPacks" style="display:flex;flex-direction:column;gap:10px;"></div>' +
        '<button onclick="closeBuyModal()" style="margin-top:16px;width:100%;padding:10px;border-radius:10px;border:1px solid #e5e7eb;background:#f3f4f6;color:#6b7280;font-weight:600;cursor:pointer;font-family:inherit;">Cancel</button>' +
        '</div>';
    document.body.appendChild(modal);
})();

function openBuyModal() {
    var modal = document.getElementById("buyModal");
    var wrap = document.getElementById("buyPacks");
    wrap.innerHTML = '<p style="font-size:13px;color:#6b7280;">Loading packs...</p>';
    modal.style.display = "flex";
    fetch("/api/billing/packs").then(function (r) { return r.json(); }).then(function (data) {
        wrap.innerHTML = "";
        ["starter", "standard", "pro", "business"].forEach(function (key) {
            var p = data.packs && data.packs[key];
            if (!p) return;
            var b = document.createElement("button");
            b.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-radius:10px;border:2px solid #e5e7eb;background:#fff;cursor:pointer;font-family:inherit;";
            b.innerHTML = '<span style="font-weight:700;color:#1a237e;">' + p.credits.toLocaleString() + ' credits</span><span style="font-weight:800;color:#059669;">$' + p.amount_usd.toFixed(2) + '</span>';
            b.onmouseenter = function () { b.style.borderColor = "#42a5f5"; };
            b.onmouseleave = function () { b.style.borderColor = "#e5e7eb"; };
            b.onclick = function () { buyPack(key, b); };
            wrap.appendChild(b);
        });
    }).catch(function () { wrap.innerHTML = '<p style="color:#dc2626;font-size:13px;">Could not load packs.</p>'; });
}

function closeBuyModal() { document.getElementById("buyModal").style.display = "none"; }

function buyPack(key, btn) {
    btn.disabled = true; btn.style.opacity = "0.6";
    fetch("/api/billing/checkout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pack: key })
    }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error) { notify("error", data.error); btn.disabled = false; btn.style.opacity = "1"; return; }
        window.location.href = data.url;
    }).catch(function (e) { notify("error", e.message); btn.disabled = false; btn.style.opacity = "1"; });
}

// Returning from Stripe success
if (window.location.hash.indexOf("credits-purchased") > -1) {
    setTimeout(function () {
        notify("success", "🎉 Payment complete! Your credits have been added.");
        refreshCredits();
        history.replaceState(null, "", "/app");
    }, 800);
}

// Refresh badge after transcription completes (deduction happens server-side)
(function () {
    if (typeof checkTranscribeProgress !== "function") return;
    var orig = checkTranscribeProgress;
    checkTranscribeProgress = async function () {
        var before = segmentsData ? segmentsData.length : 0;
        await orig();
        if (segmentsData.length && before === 0) refreshCredits();
    };
})();

// Frontend balance guards (server enforces too)
(function () {
    function guard(fnName, min, msg) {
        if (typeof window[fnName] !== "function") return;
        var orig = window[fnName];
        window[fnName] = function () {
            fetch("/api/user/info").then(function (r) { return r.json(); }).then(function (d) {
                if (!d.is_guest && typeof d.credits === "number" && d.credits < min) {
                    notify("error", msg + " (balance: " + d.credits + ")");
                    openBuyModal();
                    return;
                }
                orig();
            }).catch(function () { orig(); });
        };
    }
    guard("startTranscribe", 5, "Not enough credits — transcription costs 3 credits.");
    guard("generateAudio", 20, "Not enough credits — generation costs 1 credit per ~60 characters.");
    guard("mergeVideo", 1, "Not enough credits — merging costs 1 credit.");
})();

// ===== ADD-ON: guard voice cloning on loaded projects (no media on server) =====
(function () {
    if (typeof confirmCloning !== "function") return;
    var orig = confirmCloning;
    confirmCloning = function () {
        if (!workspaceHasMedia) {
            notify("error", "📼 Voice cloning needs the original audio on the server, and loaded projects have none. Either re-upload the same video in Step 1, or skip cloning and pick studio library voices in Step 4.");
            return;
        }
        return orig.apply(this, arguments);
    };
})();

// ===== ADD-ON: block reset during generation + tell server about abandoned jobs =====
(function () {
    var origConfirm = confirmResetSafe;
    confirmResetSafe = function () {
        var generating = false;
        try { generating = !!generatePollTimer; } catch (e) {}
        if (generating) {
            notify("error", "⏳ Audio generation is still running. Wait for it to finish before starting a new video — switching now could mix the two audios.");
            return false;
        }
        return origConfirm();
    };
})();

(function () {
    var origReset = resetWorkspace;
    resetWorkspace = function () {
        try {
            if (currentJobId) fetch("/api/abandon/" + currentJobId, { method: "POST" }).catch(function () {});
        } catch (e) {}
        origReset();
    };
})();

// ===== ADD-ON: webhook-independent credit sync =====
(function () {
    function syncCredits() {
        fetch("/api/billing/sync").then(function (r) { return r.json(); }).then(function (d) {
            if (d && d.added_sessions_credits) {
                notify("success", "💰 " + d.added_sessions_credits + " credits from your purchase have been added.");
            }
            refreshCredits();
        }).catch(function () {});
    }
    if (window.location.hash.indexOf("credits-purchased") > -1) {
        setTimeout(syncCredits, 600);
    }
    var _origOpenBuy = window.openBuyModal;
    if (typeof _origOpenBuy === "function") {
        window.openBuyModal = function () { syncCredits(); return _origOpenBuy(); };
    }
})();

// ===== ADD-ON: visible credit sync + manual trigger =====
(function () {
    window.syncCreditsNow = function () {
        fetch("/api/billing/sync").then(function (r) { return r.json(); }).then(function (d) {
            console.log("[sync]", d);
            if (d && d.error) { notify("error", "Credit sync failed: " + d.error); return; }
            if (d && d.added_sessions_credits) {
                notify("success", "💰 " + d.added_sessions_credits + " credits added from your purchase(s).");
            } else if (d && d.sessions_seen === 0) {
                notify("info", "Sync found no paid Stripe sessions for this account.");
            }
            refreshCredits();
        }).catch(function (e) { notify("error", "Credit sync error: " + e.message); });
    };
    if (window.location.hash.indexOf("credits-purchased") > -1) {
        setTimeout(window.syncCreditsNow, 1200);
    }
    var _ob = window.openBuyModal;
    if (typeof _ob === "function") {
        window.openBuyModal = function () { window.syncCreditsNow(); return _ob(); };
    }
})();

// ===== ADD-ON: direct fulfillment via checkout session id + visible sync =====
(function () {
    var m = window.location.search.match(/[?&]sid=([^&]+)/);
    var sid = m ? decodeURIComponent(m[1]) : "";
    function fulfill(s) {
        fetch("/api/billing/fulfill", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sid: s })
        }).then(function (r) { return r.json(); }).then(function (d) {
            console.log("[fulfill]", d);
            if (d && d.added) {
                notify("success", "💰 " + d.added + " credits added from your purchase.");
                refreshCredits();
            } else if (d && d.error) {
                notify("error", "Credit fulfillment: " + d.error);
            }
        }).catch(function (e) { console.error("[fulfill]", e); });
    }
    if (sid) setTimeout(function () { fulfill(sid); }, 900);

    window.syncCreditsNow = function () {
        fetch("/api/billing/sync").then(function (r) { return r.json(); }).then(function (d) {
            console.log("[sync]", d);
            if (d && d.error) { notify("error", "Credit sync failed: " + d.error); return; }
            if (d && d.added_sessions_credits) {
                notify("success", "💰 " + d.added_sessions_credits + " credits added from your purchase.");
            } else if (d && d.sessions_seen === 0) {
                notify("info", "Sync found no paid Stripe sessions for this account.");
            }
            refreshCredits();
        }).catch(function (e) { notify("error", "Credit sync error: " + e.message); });
    };
    var _ob = window.openBuyModal;
    if (typeof _ob === "function") {
        window.openBuyModal = function () { window.syncCreditsNow(); return _ob(); };
    }
})();

// ===== POLISH PASS: favicon, meta, subtitles, empty states, mobile wraps =====
(function () {
    // Favicon + meta description (no HTML edits needed)
    var fl = document.createElement("link");
    fl.rel = "icon"; fl.type = "image/png"; fl.href = "/logo.png";
    document.head.appendChild(fl);
    if (!document.querySelector('meta[name="description"]')) {
        var m = document.createElement("meta");
        m.name = "description";
        m.content = "Lisan AI — automatic English to Arabic video dubbing with voice cloning, emotions and lip-timed audio.";
        document.head.appendChild(m);
    }

    // Notify safety net: panel visible while it has cards, hidden when empty
    if (typeof notify === "function") {
        var _n = notify;
        notify = function (type, msg) {
            _n(type, msg);
            var p = document.getElementById("notifyPanel");
            if (p) {
                p.style.display = "flex";
                setTimeout(function () {
                    if (!p.children.length) p.style.display = "none";
                }, 10500);
            }
        };
    }

    // Hide the disabled Lip-Sync card whatever its id is
    document.querySelectorAll(".card h3").forEach(function (h) {
        if (/Lip-Sync/i.test(h.textContent)) {
            var c = h.closest(".card");
            if (c) c.style.display = "none";
        }
    });

    // Wrap every table in a horizontal scroll container (mobile)
    document.querySelectorAll("table").forEach(function (t) {
        if (t.parentElement && t.parentElement.classList.contains("table-wrap")) return;
        var w = document.createElement("div");
        w.className = "table-wrap";
        t.parentNode.insertBefore(w, t);
        w.appendChild(t);
    });

    // One-line subtitles under each step title
    var SUBS = {
        editorSection: "Fix timings, edit text, translate, and protect lines with 🔒.",
        voicesSection: "Optional: clone each speaker's own voice from the video.",
        cloneAnalysisSection: "See how much clean speech each speaker has before cloning.",
        speakerVoicesSection: "Assign a cloned or studio voice to every speaker.",
        generateSection: "Generate the final Arabic audio with emotions and exact timing."
    };
    Object.keys(SUBS).forEach(function (id) {
        var sec = document.getElementById(id);
        if (!sec || sec.querySelector(".step-sub")) return;
        var h = sec.querySelector("h3");
        if (!h) return;
        var p = document.createElement("p");
        p.className = "step-sub";
        p.textContent = SUBS[id];
        h.insertAdjacentElement("afterend", p);
    });

    // Friendly empty states for the three tables
    function ensureEmptyState(tableId, text) {
        var tb = document.querySelector("#" + tableId + " tbody");
        if (!tb) return;
        var update = function () {
            var hasRows = tb.querySelectorAll("tr").length > 0;
            var es = document.getElementById(tableId + "_empty");
            if (!hasRows && !es) {
                var wrap = tb.closest(".table-wrap") || tb.parentNode;
                es = document.createElement("div");
                es.id = tableId + "_empty";
                es.className = "empty-state";
                es.textContent = text;
                wrap.appendChild(es);
            } else if (hasRows && es) {
                es.remove();
            }
        };
        new MutationObserver(update).observe(tb, { childList: true });
        update();
    }
    ensureEmptyState("segmentsTable", "No segments yet — upload a video in Step 1 and press Start.");
    ensureEmptyState("speakerVoicesTable", "Voice assignments will appear here after transcription.");
    ensureEmptyState("cloneAnalysisTable", "Clone analysis will appear here after Step 3.");

    // Credits badge tooltip
    var cb = document.getElementById("creditsDisplay");
})();

// ===== STEP 5.5: VOLUME MATCH & PER-LINE MIX =====
window.VOL_NODES = {};
window._volumeGains = window._volumeGains || {};
window._volumeLines = window._volumeLines || [];
var volOrigAudio = null;

(function injectVolumeCard() {
    if (document.getElementById("volumeSection")) return;
    var res = document.getElementById("resultSection");
    if (!res) return;
    var card = document.createElement("div");
    card.className = "card hidden";
    card.id = "volumeSection";
    card.innerHTML = '<h3>Step 5.5: Volume Match & Per-Line Mix</h3>' +
        '<p class="note">Every Arabic line was automatically loudness-matched to the original speaker\'s voice (see <strong>Auto</strong> column). Play 🔊 a dubbed line, fine-tune it with the slider (−6…+6 dB, live preview), then apply to rebuild the final MP3. Re-running Generate resets trims to auto.</p>' +
        '<div class="table-wrap"><table id="volumeTable"><thead><tr><th>#</th><th>Speaker</th><th>Line</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style="min-width:130px">Trim</th><th></th></tr></thead><tbody></tbody></table></div>' +
        '<button id="applyVolumesBtn" class="green">🔊 Apply Volumes & Rebuild MP3</button> ' +
        '<button id="resetVolumesBtn" class="blue">↺ Reset All Sliders</button>';
    res.parentNode.insertBefore(card, res);
    document.getElementById("applyVolumesBtn").onclick = applyVolumes;
    document.getElementById("resetVolumesBtn").onclick = function () {
        window._volumeGains = {};
        buildVolumeTable(window._volumeLines || []);
        notify("info", "Sliders reset to auto-matched values. Press Apply to rebuild.");
    };
})();

function volCtx() { if (!window._volCtx) window._volCtx = new (window.AudioContext || window.webkitAudioContext)(); return window._volCtx; }
function linGain(db) { return Math.pow(10, (db || 0) / 20); }
function stopVolDubAll() { Object.keys(window.VOL_NODES).forEach(function (k) { try { window.VOL_NODES[k].a.pause(); } catch (e) {} }); }

function playOrigLine(line) {
    stopVolDubAll();
    if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
    if (typeof stopPreview === "function") stopPreview();
    var a = new Audio("/api/source/" + currentJobId);
    volOrigAudio = a;
    a.addEventListener("loadedmetadata", function () { a.currentTime = Math.max(0, line.start); a.play().catch(function (e) { notify("error", "Preview failed: " + e.message); }); }, { once: true });
    a.addEventListener("timeupdate", function () { if (a.currentTime >= line.end) a.pause(); });
}

function playDubLine(line) {
    if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
    if (typeof stopPreview === "function") stopPreview();
    var node = window.VOL_NODES[line.segment_id];
    if (!node) {
        var a = new Audio("/api/segment_audio/" + currentJobId + "/" + line.segment_id);
        var ctx = volCtx();
        var src = ctx.createMediaElementSource(a);
        var g = ctx.createGain();
        src.connect(g); g.connect(ctx.destination);
        node = { a: a, g: g };
        window.VOL_NODES[line.segment_id] = node;
    }
    node.g.gain.value = linGain((window._volumeGains || {})[line.segment_id] || 0);
    try { volCtx().resume(); } catch (e) {}
    if (node.a.paused) node.a.play(); else node.a.pause();
}

function onVolSlider(sid, val) {
    window._volumeGains = window._volumeGains || {};
    window._volumeGains[sid] = val;
    var node = window.VOL_NODES[sid];
    if (node) node.g.gain.value = linGain(val);
    var lab = document.getElementById("vollab_" + sid);
    if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
    var btn = document.getElementById("applyVolumesBtn");
    if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
}

function buildVolumeTable(lines) {
    var tbody = document.querySelector("#volumeTable tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    (lines || []).forEach(function (ln, i) {
        var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
        var tr = document.createElement("tr");
        function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
        td(String(i + 1));
        td(ln.speaker || seg.speaker || "");
        var full = (seg.arabic_text || seg.text || "");
        td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + '</span>');
        var c1 = document.createElement("td");
        var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "▶"; b1.title = "Play original line";
        b1.onclick = function () { playOrigLine(ln); }; c1.appendChild(b1); tr.appendChild(c1);
        var c2 = document.createElement("td");
        var b2 = document.createElement("button"); b2.className = "action-btn blue"; b2.textContent = "🔊"; b2.title = "Play dubbed line (with slider trim)";
        b2.onclick = function () { playDubLine(ln); }; c2.appendChild(b2); tr.appendChild(c2);
        td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
        var c3 = document.createElement("td");
        var cur = (window._volumeGains || {})[ln.segment_id] || 0;
        var rg = document.createElement("input"); rg.type = "range"; rg.min = "-6"; rg.max = "6"; rg.step = "0.5"; rg.value = cur;
        rg.oninput = function () { onVolSlider(ln.segment_id, parseFloat(rg.value)); };
        c3.appendChild(rg); tr.appendChild(c3);
        td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + ' dB</span>');
        tbody.appendChild(tr);
    });
}

function showVolumeSection(lines) {
    window._volumeLines = lines;
    window._volumeGains = window._volumeGains || {};
    buildVolumeTable(lines);
    var el = document.getElementById("volumeSection");
    if (el) el.classList.remove("hidden");
}

async function applyVolumes() {
    if (!currentJobId) { notify("error", "No job."); return; }
    var btn = document.getElementById("applyVolumesBtn");
    btn.disabled = true; btn.textContent = "⏳ Rebuilding...";
    var offs = {};
    Object.keys(segmentOffsets).forEach(function (k) { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
    try {
        var res = await fetch("/api/remix_audio", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                job_id: currentJobId, segments: segmentsData, offsets: offs,
                gains: window._volumeGains || {}, total_duration: totalDuration,
                duration_mode: document.getElementById("durationMode").value
            })
        });
        var data = await res.json();
        if (!res.ok || !data || data.status !== "success") {
            notify("error", "Rebuild failed: " + ((data && (data.error || data.detail)) || res.status));
            return;
        }
        notify("success", "Volumes applied — final MP3 rebuilt with your per-line trim.");
        var au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        btn.textContent = "🔊 Apply Volumes & Rebuild MP3";
    } catch (e) { notify("error", e.message); }
    finally { btn.disabled = false; }
}

// Populate the table when generation finishes
(function () {
    if (typeof checkGenerateProgress !== "function") return;
    var orig = checkGenerateProgress;
    checkGenerateProgress = async function () {
        await orig();
        try {
            var r = await fetch("/api/progress/generate?t=" + Date.now());
            var d = await r.json();
            if (d && d.status === "done" && d.result && Array.isArray(d.result.lines) && d.result.lines.length) {
                var sig = currentJobId + ":" + d.result.lines.length;
                if (window._volBuiltFor !== sig) {
                    window._volBuiltFor = sig;
                    showVolumeSection(d.result.lines);
                }
            }
        } catch (e) {}
    };
})();

// Reset hides & clears the volume section too
(function () {
    if (typeof resetWorkspace !== "function") return;
    var orig = resetWorkspace;
    resetWorkspace = function () {
        orig();
        window._volumeLines = [];
        window._volumeGains = {};
        stopVolDubAll();
        if (volOrigAudio) { volOrigAudio.pause(); volOrigAudio = null; }
        var vs = document.getElementById("volumeSection");
        if (vs) vs.classList.add("hidden");
    };
})();

// ===== STEP 5.5 v2: sliders default to matched volume + master trim =====
(function () {
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }
    window.masterTrimValue = function () {
        var el = document.getElementById("masterTrim");
        return el ? (parseFloat(el.value) || 0) : 0;
    };
    function ensureMaster() {
        var card = document.getElementById("volumeSection");
        if (!card) return;
        if (!document.getElementById("masterTrimWrap")) {
            var wrap = document.createElement("div");
            wrap.id = "masterTrimWrap";
            wrap.style.cssText = "display:flex;align-items:center;gap:10px;margin:12px 0 4px;flex-wrap:wrap;";
            wrap.innerHTML = '<strong style="font-size:13px;">🎚️ Master volume:</strong>' +
                '<input type="range" id="masterTrim" min="-12" max="12" step="0.5" value="0" style="width:220px;">' +
                '<span id="masterTrimLab" style="min-width:64px;">+0.0 dB</span>';
            var tw = card.querySelector(".table-wrap");
            if (tw) card.insertBefore(wrap, tw); else card.appendChild(wrap);
            document.getElementById("masterTrim").oninput = function () {
                var v = parseFloat(this.value) || 0;
                document.getElementById("masterTrimLab").textContent = (v > 0 ? "+" : "") + v.toFixed(1) + " dB";
                Object.keys(window.VOL_NODES || {}).forEach(function (sid) {
                    var base = (window._volumeGains || {})[sid] || 0;
                    window.VOL_NODES[sid].g.gain.value = Math.pow(10, (base + v) / 20);
                });
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
            };
        }
        var ab = document.getElementById("applyVolumesBtn");
        if (ab) ab.onclick = function () { window.applyVolumesV2(); };
        var rb = document.getElementById("resetVolumesBtn");
        if (rb) rb.onclick = function () { window.resetVolumesV2(); };
    }
    window.resetVolumesV2 = function () {
        window._volumeGains = {};
        (window._volumeLines || []).forEach(function (ln) {
            window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0);
        });
        if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
        document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
        notify("info", "Sliders reset to the auto-matched volumes.");
    };
    window.applyVolumesV2 = function () {
        if (!currentJobId) { notify("error", "No job."); return; }
        var btn = document.getElementById("applyVolumesBtn");
        var m = window.masterTrimValue();
        var gains = {};
        Object.keys(window._volumeGains || {}).forEach(function (sid) {
            gains[sid] = clampG((window._volumeGains[sid] || 0) + m);
        });
        btn.disabled = true; btn.textContent = "⏳ Rebuilding...";
        var offs = {};
        Object.keys(segmentOffsets).forEach(function (k) { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
        fetch("/api/remix_audio", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: currentJobId, segments: segmentsData, offsets: offs, gains: gains, total_duration: totalDuration, duration_mode: document.getElementById("durationMode").value })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!data || data.status !== "success") { notify("error", "Rebuild failed: " + ((data && (data.error || data.detail)) || "server error")); return; }
            Object.keys(gains).forEach(function (sid) { window._volumeGains[sid] = gains[sid]; });
            if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
            document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
            var mt = document.getElementById("masterTrim");
            if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
            notify("success", "Volumes applied — final MP3 rebuilt with your mix.");
            var au = document.querySelector("#audioResults audio");
            if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        }).catch(function (e) { notify("error", e.message); }).finally(function () {
            btn.disabled = false; btn.textContent = "🔊 Apply Volumes & Rebuild MP3";
        });
    };
    var _oldShow = window.showVolumeSection;
    window.showVolumeSection = function (lines) {
        window._volumeGains = {};
        (lines || []).forEach(function (ln) {
            window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0);
        });
        if (typeof _oldShow === "function") _oldShow(lines);
        document.querySelectorAll("#volumeTable input[type=range]").forEach(function (rg) { rg.min = -12; rg.max = 12; });
        ensureMaster();
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; document.getElementById("masterTrimLab").textContent = "+0.0 dB"; }
    };
    window.onVolSlider = function (sid, val) {
        window._volumeGains = window._volumeGains || {};
        window._volumeGains[sid] = val;
        var node = (window.VOL_NODES || {})[sid];
        if (node) node.g.gain.value = Math.pow(10, (val + window.masterTrimValue()) / 20);
        var lab = document.getElementById("vollab_" + sid);
        if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
        var btn = document.getElementById("applyVolumesBtn");
        if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
    };
    var _oldPlayDub = window.playDubLine;
    window.playDubLine = function (line) {
        var r = _oldPlayDub(line);
        var node = (window.VOL_NODES || {})[line.segment_id];
        if (node) node.g.gain.value = Math.pow(10, (((window._volumeGains || {})[line.segment_id] || 0) + window.masterTrimValue()) / 20);
        return r;
    };
})();


// ===== V3: custom voices, usage button, volume-table fixes =====
(function () {
    // Hide "Load Voice Options" (voices auto-load with the table now)
    document.querySelectorAll("button").forEach(function (b) {
        if (/Load Voice Options/i.test(b.textContent)) b.style.display = "none";
    });
    // Usage button in user bar
    var bar = document.getElementById("userBar");
    if (bar && !document.getElementById("accountBtn")) {
        var right = bar.children[1];
        var a = document.createElement("button");
        a.id = "accountBtn"; a.className = "btn-logout"; a.textContent = "📊 Usage";
        a.onclick = function () { window.location.href = "/account"; };
        right.insertBefore(a, document.getElementById("buyBtn") || right.lastElementChild);
    }
    // Custom voice upload box in Step 4
    var sv = document.getElementById("speakerVoicesSection");
    if (sv && !document.getElementById("customVoiceBox")) {
        var box = document.createElement("div");
        box.id = "customVoiceBox";
        box.className = "note";
        box.style.marginTop = "12px";
        box.innerHTML = '<strong>📤 Use your own voice clip:</strong> pick a speaker and upload an MP3/WAV clip (max 20 s) where that person speaks most of the time. The clip is not analyzed — the voice engine extracts the dominant voice, so music or other voices in it will reduce quality.<br>' +
            '<select id="cvSpeaker" style="width:auto;min-width:140px;margin:8px 6px 0 0;"></select>' +
            '<input type="file" id="cvFile" accept=".mp3,.wav,audio/mpeg,audio/wav" style="width:auto;display:inline-block;margin-top:8px;">' +
            '<button class="purple" id="cvUpload" style="margin-top:8px;">Upload as this speaker\'s voice</button>' +
            '<span id="cvStatus" style="margin-left:10px;font-size:12px;color:#6b7280;"></span>';
        sv.appendChild(box);
        document.getElementById("cvUpload").onclick = window.uploadCustomVoice;
    }
    window.uploadCustomVoice = function () {
        var f = document.getElementById("cvFile").files[0];
        var sp = document.getElementById("cvSpeaker").value;
        var st = document.getElementById("cvStatus");
        if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
        if (!/\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
        var probe = new Audio(URL.createObjectURL(f));
        probe.onloadedmetadata = function () {
            if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s — the limit is 20 seconds."); return; }
            var form = new FormData();
            form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
            st.textContent = "Uploading & creating voice...";
            fetch("/api/upload_custom_voice", { method: "POST", body: form })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    st.textContent = "";
                    if (d.error) { notify("error", d.error); return; }
                    clonedBySpeaker[sp] = d.voice_id;
                    speakerChoices[sp] = "clone";
                    applyChoice(sp);
                    renderSpeakerVoices();
                    notify("success", "Custom voice created and assigned to " + sp + ".");
                }).catch(function (e) { st.textContent = ""; notify("error", e.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
    function refreshCvSpeakers() {
        var sel = document.getElementById("cvSpeaker");
        if (!sel) return;
        var names = [];
        segmentsData.forEach(function (s) { if (names.indexOf(s.speaker) < 0) names.push(s.speaker); });
        sel.innerHTML = "";
        names.forEach(function (n) { var o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o); });
    }
    if (typeof renderSpeakerVoices === "function") {
        var _rsv = renderSpeakerVoices;
        renderSpeakerVoices = function () { var r = _rsv.apply(this, arguments); refreshCvSpeakers(); return r; };
    }
})();

// ===== V3 volume table: icons, reset-to-match, master behaviour & position =====
(function () {
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }
    window._masterPrev = 0;
    var volOrigAudio2 = null, volOrigBtn = null;

    function stopAllVol(btnExcept) {
        Object.keys(window.VOL_NODES || {}).forEach(function (k) {
            var n = window.VOL_NODES[k];
            try { n.a.pause(); } catch (e) {}
            if (n.btn && n.btn !== btnExcept) n.btn.textContent = "▶";
        });
        if (volOrigAudio2) { try { volOrigAudio2.pause(); } catch (e) {} }
        if (volOrigBtn && volOrigBtn !== btnExcept) volOrigBtn.textContent = "▶";
    }

    window.playOrigLine = function (line, btn) {
        if (volOrigAudio2 && volOrigBtn === btn) {
            volOrigAudio2.pause(); volOrigAudio2 = null; volOrigBtn = null; btn.textContent = "▶"; return;
        }
        stopAllVol(btn);
        if (volOrigBtn) volOrigBtn.textContent = "▶";
        var a = new Audio("/api/source/" + currentJobId + "?t=" + Date.now());
        volOrigAudio2 = a; volOrigBtn = btn; btn.textContent = "⏸";
        a.addEventListener("loadedmetadata", function () { a.currentTime = Math.max(0, line.start); a.play().catch(function () { btn.textContent = "▶"; }); }, { once: true });
        a.addEventListener("timeupdate", function () {
            if (a.currentTime >= line.end) { a.pause(); btn.textContent = "▶"; volOrigAudio2 = null; volOrigBtn = null; }
        });
    };

    window.playDubLine = function (line, btn) {
        var node = (window.VOL_NODES || {})[line.segment_id];
        if (node && node.btn === btn && !node.a.paused) { node.a.pause(); btn.textContent = "▶"; return; }
        stopAllVol(btn);
        if (!node) {
            var a = new Audio("/api/segment_audio/" + currentJobId + "/" + line.segment_id + "?t=" + Date.now());
            var ctx = (window._volCtx || (window._volCtx = new (window.AudioContext || window.webkitAudioContext)()));
            var src = ctx.createMediaElementSource(a);
            var g = ctx.createGain();
            src.connect(g); g.connect(ctx.destination);
            node = { a: a, g: g, btn: btn };
            window.VOL_NODES[line.segment_id] = node;
        }
        node.btn = btn;
        node.g.gain.value = Math.pow(10, ((window._volumeGains || {})[line.segment_id] || 0) / 20);
        try { window._volCtx.resume(); } catch (e) {}
        node.a.play();
        btn.textContent = "⏸";
        node.a.onended = function () { btn.textContent = "▶"; };
    };

    window.buildVolumeTable = function (lines) {
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td(String(i + 1));
            td(ln.speaker || seg.speaker || "");
            var full = (seg.arabic_text || seg.text || "");
            td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + '</span>');
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "▶"; b1.title = "Play original line";
            b1.onclick = function () { window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "▶"; b2.title = "Play dubbed line (with slider trim)";
            b2.onclick = function () { window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
            td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
            var c3 = document.createElement("td");
            var cur = (window._volumeGains || {})[ln.segment_id] || 0;
            var rg = document.createElement("input"); rg.type = "range"; rg.min = -12; rg.max = 12; rg.step = 0.5; rg.value = cur;
            rg.oninput = function () { window.onVolSlider(ln.segment_id, parseFloat(rg.value)); };
            c3.appendChild(rg); tr.appendChild(c3);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + ' dB</span>');
            tbody.appendChild(tr);
        });
    };

    window.onVolSlider = function (sid, val) {
        window._volumeGains = window._volumeGains || {};
        window._volumeGains[sid] = val;
        var node = (window.VOL_NODES || {})[sid];
        if (node) node.g.gain.value = Math.pow(10, val / 20);
        var lab = document.getElementById("vollab_" + sid);
        if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
        var btn = document.getElementById("applyVolumesBtn");
        if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
    };

    function placeMaster() {
        var card = document.getElementById("volumeSection");
        if (!card) return;
        var wrap = document.getElementById("masterTrimWrap");
        if (!wrap) {
            wrap = document.createElement("div");
            wrap.id = "masterTrimWrap";
            wrap.style.cssText = "display:flex;align-items:center;gap:10px;justify-content:flex-end;margin:10px 0 2px;";
            wrap.innerHTML = '<strong style="font-size:13px;">🎚️ Master:</strong>' +
                '<input type="range" id="masterTrim" min="-12" max="12" step="0.5" value="0" style="width:200px;">' +
                '<span id="masterTrimLab" style="min-width:60px;">+0.0 dB</span>';
            var tw = card.querySelector(".table-wrap");
            if (tw) card.insertBefore(wrap, tw); else card.appendChild(wrap);
        }
        wrap.style.justifyContent = "flex-end";
        document.getElementById("masterTrim").oninput = function () {
            var m = parseFloat(this.value) || 0;
            var delta = m - (window._masterPrev || 0);
            window._masterPrev = m;
            document.getElementById("masterTrimLab").textContent = (m > 0 ? "+" : "") + m.toFixed(1) + " dB";
            if (Math.abs(delta) < 0.001) return;
            (window._volumeLines || []).forEach(function (ln) {
                var g = clampG(((window._volumeGains || {})[ln.segment_id] || 0) + delta);
                window._volumeGains[ln.segment_id] = g;
                var node = (window.VOL_NODES || {})[ln.segment_id];
                if (node) node.g.gain.value = Math.pow(10, g / 20);
            });
            window.buildVolumeTable(window._volumeLines || []);
            var btn = document.getElementById("applyVolumesBtn");
            if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
        };
    }

    var _oldShow3 = window.showVolumeSection;
    window.showVolumeSection = function (lines) {
        window._volumeGains = {};
        (lines || []).forEach(function (ln) { window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0); });
        window._masterPrev = 0;
        if (typeof _oldShow3 === "function") _oldShow3(lines);
        placeMaster();
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; var lb = document.getElementById("masterTrimLab"); if (lb) lb.textContent = "+0.0 dB"; }
    };

    window.resetVolumesV2 = function () {
        window._volumeGains = {};
        (window._volumeLines || []).forEach(function (ln) { window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0); });
        window._masterPrev = 0;
        window.buildVolumeTable(window._volumeLines || []);
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; var lb = document.getElementById("masterTrimLab"); if (lb) lb.textContent = "+0.0 dB"; }
        Object.keys(window.VOL_NODES || {}).forEach(function (sid) {
            var n = window.VOL_NODES[sid];
            if (n) n.g.gain.value = Math.pow(10, ((window._volumeGains[sid] || 0)) / 20);
        });
        notify("info", "Sliders restored to the measured original-matched volumes.");
    };

    window.applyVolumesV2 = function () {
        if (!currentJobId) { notify("error", "No job."); return; }
        var btn = document.getElementById("applyVolumesBtn");
        var gains = {};
        Object.keys(window._volumeGains || {}).forEach(function (sid) { gains[sid] = clampG(window._volumeGains[sid] || 0); });
        btn.disabled = true; btn.textContent = "⏳ Rebuilding...";
        var offs = {};
        Object.keys(segmentOffsets).forEach(function (k) { if (Math.abs(segmentOffsets[k]) > 0.001) offs[k] = segmentOffsets[k]; });
        fetch("/api/remix_audio", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: currentJobId, segments: segmentsData, offsets: offs, gains: gains, total_duration: totalDuration, duration_mode: document.getElementById("durationMode").value })
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!data || data.status !== "success") { notify("error", "Rebuild failed: " + ((data && (data.error || data.detail)) || "server error")); return; }
            notify("success", "Volumes applied — final MP3 rebuilt.");
            var au = document.querySelector("#audioResults audio");
            if (au) { au.src = "/api/download/final_dubbed.mp3?cache=" + Date.now(); au.load(); }
            btn.textContent = "🔊 Apply Volumes & Rebuild MP3";
        }).catch(function (e) { notify("error", e.message); })
          .finally(function () { btn.disabled = false; });
    };

    // After re-speaking a line, drop its cached audio so the table plays the new take
    if (typeof regenerateLine === "function") {
        var _rg = regenerateLine;
        regenerateLine = function (i, btn) {
            var seg = segmentsData[i];
            var p = _rg.apply(this, arguments);
            if (seg && window.VOL_NODES && window.VOL_NODES[seg.segment_id]) {
                try { window.VOL_NODES[seg.segment_id].a.pause(); } catch (e) {}
                delete window.VOL_NODES[seg.segment_id];
            }
            return p;
        };
    }
})();

// ===== FIX: custom voice button (late binding) + robust upload + voice GC =====
(function () {
    function bind() {
        var b = document.getElementById("cvUpload");
        if (b) b.onclick = function () { if (typeof window.uploadCustomVoice === "function") window.uploadCustomVoice(); };
    }
    bind();
    new MutationObserver(bind).observe(document.body, { childList: true, subtree: true });
})();

window.uploadCustomVoice = function () {
    var fEl = document.getElementById("cvFile");
    var spEl = document.getElementById("cvSpeaker");
    var st = document.getElementById("cvStatus");
    if (!fEl || !spEl) { notify("error", "Custom voice box not ready — refresh the page."); return; }
    var f = fEl.files[0];
    var sp = spEl.value || "Speaker 1";
    if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
    if (!/\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
    var probe = new Audio(URL.createObjectURL(f));
    probe.onloadedmetadata = function () {
        if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s — the limit is 20 seconds."); return; }
        var form = new FormData();
        form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
        if (st) st.textContent = "Uploading & creating voice...";
        function tryUrl(url, fallback) {
            fetch(url, { method: "POST", body: form.clone ? form : form })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (out) {
                    if (!out.ok || !out.d || out.d.error || !out.d.voice_id) {
                        if (fallback) { tryUrl(fallback, null); return; }
                        if (st) st.textContent = "";
                        notify("error", (out.d && (out.d.error || out.d.detail)) || "Server error — is the server redeployed?");
                        return;
                    }
                    if (st) st.textContent = "";
                    clonedBySpeaker[sp] = out.d.voice_id;
                    speakerChoices[sp] = "clone";
                    applyChoice(sp);
                    renderSpeakerVoices();
                    notify("success", "Custom voice created and assigned to " + sp + ".");
                })
                .catch(function (e) {
                    if (fallback) { tryUrl(fallback, null); return; }
                    if (st) st.textContent = "";
                    notify("error", "Upload failed: " + e.message);
                });
        }
        tryUrl("/api/upload_custom_voice", "/api/upload_custom_voice2");
    };
    probe.onerror = function () { notify("error", "Could not read that audio file."); };
};

window.cleanOldClones = function () {
    if (!confirm("Delete ALL old cloned/custom voices from your voice account, except the ones this project is using right now?")) return;
    var keep = Object.values(clonedBySpeaker || {}).concat(window.clonedVoiceIds || []);
    fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: keep }) })
        .then(function (r) { return r.json(); })
        .then(function (d) { notify("success", "🧹 Removed " + (d.deleted || 0) + " old cloned voice(s)." + ((d.errors || []).length ? " (" + d.errors.length + " errors)" : "")); })
        .catch(function (e) { notify("error", e.message); });
};


// Auto garbage-collect after every cloning run (keep only current clones)
(function () {
    if (typeof confirmCloning !== "function") return;
    var orig = confirmCloning;
    confirmCloning = function () {
        var p = orig.apply(this, arguments);
        Promise.resolve(p).then(function () {
            var keep = Object.values(clonedBySpeaker || {}).concat(window.clonedVoiceIds || []);
            if (keep.length) fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: keep }) }).catch(function () {});
        });
        return p;
    };
})();

// ===== I18N v2: safe key-tagging, RTL, translated notifications, control repairs =====
(function () {
    var P = [
        ["Step 1: Upload Audio or Video", "الخطوة 1: رفع الصوت أو الفيديو"],
        ["Step 2: Edit Segments", "الخطوة 2: تحرير المقاطع"],
        ["Step 3: Voice Cloning Setup", "الخطوة 3: إعداد استنساخ الصوت"],
        ["Step 3.5: Choose Which Speakers to Clone", "الخطوة 3.5: اختيار المتحدثين للاستنساخ"],
        ["Step 4: Speaker Voices", "الخطوة 4: أصوات المتحدثين"],
        ["Step 5: Generate Arabic Audio", "الخطوة 5: توليد الصوت العربي"],
        ["Step 5.5: Volume Match & Per-Line Mix", "الخطوة 5.5: مطابقة مستوى الصوت ومزج كل سطر"],
        ["Step 6: Final Result", "الخطوة 6: النتيجة النهائية"],
        ["Start", "ابدأ"],
        ["Auto Translate to Arabic", "ترجمة تلقائية إلى العربية"],
        ["Add Tashkeel Only", "إضافة التشكيل فقط"],
        ["Detect Emotions from Voice", "كشف المشاعر من الصوت"],
        ["Auto-Fix Timing", "إصلاح التوقيت تلقائيًا"],
        ["Import SRT/SBV", "استيراد SRT/SBV"],
        ["Save Project", "حفظ المشروع"],
        ["Load Project", "تحميل المشروع"],
        ["Prepare Voice Cloning", "تحضير استنساخ الصوت"],
        ["Clone Selected Voices", "استنساخ الأصوات المحددة"],
        ["Auto-Assign All", "تعيين تلقائي للكل"],
        ["Generate Arabic Audio", "توليد الصوت العربي"],
        ["Merge Audio into Video", "دمج الصوت في الفيديو"],
        ["Confirm Changes & Rebuild MP3", "تأكيد التغييرات وإعادة بناء MP3"],
        ["Reset Offsets", "إصفار الإزاحات"],
        ["Fine-Tune Timeline", "ضبط الخط الزمني"],
        ["Dub Another Video", "دبلجة فيديو آخر"],
        ["Log Out", "تسجيل الخروج"],
        ["Download MP3", "تنزيل MP3"],
        ["Download Dubbed Video (MP4)", "تنزيل الفيديو المدبلج (MP4)"],
        ["Download Pure Vocals (MP3)", "تنزيل الصوت فقط (MP3)"],
        ["Apply Volumes & Rebuild MP3", "تطبيق مستويات الصوت وإعادة بناء MP3"],
        ["Reset All Sliders", "إعادة تعيين كل المنزلقات"],
        ["Upload as this speaker's voice", "رفعه كصوت لهذا المتحدث"],
        ["Clean old cloned voices", "تنظيف الأصوات المستنسخة القديمة"],
        ["📊 Usage", "📊 الاستخدام"],
        ["➕ Buy", "➕ شراء"],
        ["Choose File", "اختر ملفًا"]
    ];
    var R = [
        ["Supports: MP3, WAV, MP4, AVI, MKV, MOV, WEBM", "يدعم: MP3, WAV, MP4, AVI, MKV, MOV, WEBM. الحدود: 60 ثانية و400 ميجابايت كحد أقصى"],
        ["Important: Your generated audio", "مهم: ملفات الصوت والفيديو الناتجة مؤقتة. نزّلها فورًا بعد المعالجة — ستُفقد عند انتهاء الجلسة أو إعادة تشغيل الخادم"],
        ["Download your files now", "نزّل ملفاتك الآن! الصوت والفيديو الناتجان مؤقتان ويُفقدان عند انتهاء الجلسة"],
        ["English to Arabic AI Dubbing", "English to Arabic AI Dubbing"],
        ["Credits are our internal unit", "الائتمانات وحدتنا الداخلية: 100 ائتمان = 1 دولار. الدبلجة الكاملة النموذجية تكلف بضعة ائتمانات فقط"],
        ["Voice generation supports emotions", "يدعم توليد الصوت المشاعر والأصوات المستنسخة. تُحتسب التكلفة بالأحرف وتُعرض بالائتمانات (100 ائتمان = 1 دولار)"],
        ["Cloning copies each speaker's own voice", "ينسخ الاستنساخ صوت كل متحدث من الفيديو. اختياري — يمكنك اختيار أصوات المكتبة في الخطوة 4"],
        ["Pick a voice for each speaker", "اختر صوتًا لكل متحدث. الأصوات المستنسخة من الفيديو؛ والأصوات المرقمة من مكتبة الاستوديو"],
        ["Combines the dubbed Arabic audio", "يدمج الصوت العربي المدبلج مع موسيقى الخلفية الأصلية والفيديو"],
        ["Drag each block left/right", "اسحب كل كتلة يسارًا/يمينًا لمطابقة حركة الشفاه، ثم أكّد لإعادة بناء MP3"],
        ["Each Arabic line was automatically loudness-matched", "تمت مطابقة مستوى كل سطر عربي مع صوت المتحدث الأصلي تلقائيًا (عمود Auto). شغّل ▶ واضبط بالمنزلق ثم طبّق"]
    ];
    var N = [
        ["Transcription complete.", "اكتملت التفريغة."],
        ["Arabic audio generated and merged.", "تم توليد الصوت العربي ودمجه."],
        ["Video merged successfully!", "تم دمج الفيديو بنجاح!"],
        ["Voices cloned successfully!", "تم استنساخ الأصوات بنجاح!"],
        ["Project saved to JSON.", "تم حفظ المشروع."],
        ["Project loaded.", "تم تحميل المشروع."],
        ["SRT exported.", "تم تصدير SRT."],
        ["SBV exported.", "تم تصدير SBV."],
        ["Translation complete.", "اكتملت الترجمة."],
        ["Emotion detection complete.", "اكتمل كشف المشاعر."],
        ["Voices auto-assigned.", "تم تعيين الأصوات تلقائيًا."],
        ["Voice options loaded.", "تم تحميل خيارات الأصوات."],
        ["Volumes applied", "طُبقت مستويات الصوت"],
        ["Sliders reset", "أعيد تعيين المنزلقات"],
        ["Removed", "تمت إزالة"],
        ["Custom voice created and assigned to", "تم إنشاء صوت مخصص وتعيينه إلى"],
        ["Not enough credits", "الرصيد غير كافٍ"],
        ["Insufficient credits", "الرصيد غير كافٍ"],
        ["Upload failed:", "فشل الرفع:"],
        ["Rebuild failed:", "فشل إعادة البناء:"],
        ["failed:", "فشل:"],
        ["Workspace cleared.", "تم مسح مساحة العمل."],
        ["Uploading and starting transcription...", "جارٍ الرفع وبدء التفريغة..."]
    ];

    function tagAll() {
        document.querySelectorAll("h3, button, a, p, .note, span, label, th").forEach(function (el) {
            if (el.dataset && el.dataset.i18n) return;
            if (el.closest && (el.closest("#notifyPanel") || el.closest("#buyModal"))) return;
            var base = (el.textContent || "").trim();
            if (!base) return;
            var isCtl = /^(H3|BUTTON|A|LABEL|TH)$/.test(el.tagName);
            var list = isCtl ? P : R;
            for (var i = 0; i < list.length; i++) {
                var hit = isCtl ? base.indexOf(list[i][0]) === 0 : base.indexOf(list[i][0]) > -1;
                if (!hit) continue;
                if (!isCtl && el.querySelector && el.querySelector("select, input, button, audio, video")) return;
                el.dataset.i18n = String(i);
                el.dataset.i18nKind = isCtl ? "p" : "r";
                el.dataset.i18nEn = (el.firstChild && el.firstChild.nodeType === 3) ? el.firstChild.nodeValue : el.innerHTML;
                return;
            }
        });
    }

    function applyLang(lang) {
        try {
            window.currentLang = lang;
            localStorage.setItem("lisan_lang", lang);
            tagAll();
            document.querySelectorAll("[data-i18n]").forEach(function (el) {
                try {
                    var kind = el.dataset.i18nKind;
                    var entry = (kind === "p" ? P : R)[parseInt(el.dataset.i18n, 10)];
                    if (!entry) return;
                    var en = el.dataset.i18nEn;
                    var hasText = el.firstChild && el.firstChild.nodeType === 3;
                    if (lang === "ar") {
                        var ar = (kind === "p") ? entry[1] + " " : en.replace(entry[0], entry[1]);
                        if (hasText) el.firstChild.nodeValue = ar; else el.innerHTML = ar;
                    } else {
                        if (hasText) el.firstChild.nodeValue = en; else el.innerHTML = en;
                    }
                } catch (e) {}
            });
            document.body.classList.toggle("lang-ar", lang === "ar");
            document.documentElement.lang = (lang === "ar") ? "ar" : "en";
            var lb = document.getElementById("langBtn");
            if (lb) lb.textContent = (lang === "en") ? "🌐 عربي" : "🌐 English";
        } catch (e) { console.error("applyLang:", e); }
    }
    window.applyLang = applyLang;

    // Translated notifications
    if (typeof notify === "function" && !window._notifyWrapped) {
        window._notifyWrapped = true;
        var _n = notify;
        notify = function (type, msg) {
            if (window.currentLang === "ar") {
                var s = String(msg);
                for (var i = 0; i < N.length; i++) {
                    if (s.indexOf(N[i][0]) > -1) s = s.replace(N[i][0], N[i][1]);
                }
                msg = s;
            }
            return _n(type, msg);
        };
    }

    // Repair the custom-voice box if an earlier bug wiped its controls
    function repairCvBox() {
        var box = document.getElementById("customVoiceBox");
        if (!box || document.getElementById("cvSpeaker")) return;
        box.innerHTML = '<strong>📤 Use your own voice clip:</strong> pick a speaker and upload an MP3/WAV clip (max 20 s) where that person speaks most of the time. The clip is not analyzed — the voice engine extracts the dominant voice, so music or other voices in it will reduce quality.<br>' +
            '<select id="cvSpeaker" style="width:auto;min-width:140px;margin:8px 6px 0 0;"></select>' +
            '<input type="file" id="cvFile" accept=".mp3,.wav,audio/mpeg,audio/wav" style="width:auto;display:inline-block;margin-top:8px;">' +
            '<button class="purple" id="cvUpload" style="margin-top:8px;">Upload as this speaker\'s voice</button> ' +
            '<button class="red" id="cvClean" style="margin-top:8px;">🧹 Clean old cloned voices</button>' +
            '<span id="cvStatus" style="margin-left:10px;font-size:12px;color:#6b7280;"></span>';
        bindCv();
    }
    function bindCv() {
        var u = document.getElementById("cvUpload");
        if (u) u.onclick = function () { if (typeof window.uploadCustomVoice === "function") window.uploadCustomVoice(); };
        var c = document.getElementById("cvClean");
        if (c) c.onclick = function () { if (typeof window.cleanOldClones === "function") window.cleanOldClones(); };
    }

    // 🧹 now removes ALL app-created clones (they must be ephemeral)
    window.cleanOldClones = function () {
        if (!confirm("Delete ALL cloned/custom voices from your voice account (including this project's)? Cloning again will re-create only what you need.")) return;
        fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: [] }) })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, st: r.status, d: d }; }); })
            .then(function (out) {
                if (!out.ok) { notify("error", "Cleanup endpoint not found (status " + out.st + ") — redeploy main.py with the /api/cleanup_voices block."); return; }
                notify("success", "🧹 Removed " + (out.d.deleted || 0) + " cloned voice(s) from your account.");
            })
            .catch(function (e) { notify("error", e.message); });
    };

    // Auto garbage-collect clones whenever the workspace resets (new video = fresh voices)
    if (typeof resetWorkspace === "function" && !window._gcWrapped) {
        window._gcWrapped = true;
        var _rw = resetWorkspace;
        resetWorkspace = function () {
            var r = _rw.apply(this, arguments);
            fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: [] }) }).catch(function () {});
            return r;
        };
    }

    // Language button (create or reuse)
    (function () {
        var bar = document.getElementById("userBar");
        var lb = document.getElementById("langBtn");
        if (!lb && bar && bar.children[1]) {
            lb = document.createElement("button");
            lb.id = "langBtn"; lb.className = "btn-logout";
            bar.children[1].insertBefore(lb, bar.children[1].firstElementChild);
        }
        if (lb) lb.onclick = function () { applyLang(window.currentLang === "en" ? "ar" : "en"); };
    })();

    var t = null;
    new MutationObserver(function () {
        clearTimeout(t);
        t = setTimeout(function () {
            repairCvBox();
            bindCv();
            if (window.currentLang === "ar") applyLang("ar");
        }, 400);
    }).observe(document.body, { childList: true, subtree: true });

    repairCvBox();
    bindCv();
    applyLang(localStorage.getItem("lisan_lang") || "en");
})();

// ===== No clean button: voice cleanup is automatic only =====
(function () {
    function dropClean() { var b = document.getElementById("cvClean"); if (b) b.remove(); }
    dropClean();
    new MutationObserver(dropClean).observe(document.body, { childList: true, subtree: true });
})();

// ===== RESTORE PATCH: fixes master-slider, icons, load-voices hide, master side =====
(function () {
    // 1) FIX: master slider must move every line slider (the bug was window.volumeGains vs window._volumeGains)
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }

    // Re-bind master input to the CORRECT handler every time it appears
    function bindMaster() {
        var mt = document.getElementById("masterTrim");
        if (!mt || mt.dataset.bound === "1") return;
        mt.dataset.bound = "1";
        mt.oninput = function () {
            var m = parseFloat(mt.value) || 0;
            var lab = document.getElementById("masterTrimLab");
            if (lab) lab.textContent = (m > 0 ? "+" : "") + m.toFixed(1) + " dB";
            // Shift EVERY line slider by the master delta
            var prev = window._masterPrev || 0;
            var delta = m - prev;
            window._masterPrev = m;
            if (Math.abs(delta) < 0.001) return;
            (window._volumeLines || []).forEach(function (ln) {
                var g = clampG(((window._volumeGains || {})[ln.segment_id] || 0) + delta);
                window._volumeGains[ln.segment_id] = g;
                var node = (window.VOL_NODES || {})[ln.segment_id];
                if (node) node.g.gain.value = Math.pow(10, g / 20);
            });
            if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
            var btn = document.getElementById("applyVolumesBtn");
            if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
        };
    }

    // 2) FIX: per-line slider handler (correct variable name)
    window.onVolSlider = function (sid, val) {
        window._volumeGains = window._volumeGains || {};
        window._volumeGains[sid] = val;
        var node = (window.VOL_NODES || {})[sid];
        if (node) node.g.gain.value = Math.pow(10, (val + (window._masterPrev || 0)) / 20);
        var lab = document.getElementById("vollab_" + sid);
        if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
        var btn = document.getElementById("applyVolumesBtn");
        if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
    };

    // 3) FIX: Step 5.5 table — green ▶ icons in BOTH columns, sliders start at matched value
    window.buildVolumeTable = function (lines) {
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td(String(i + 1));
            td(ln.speaker || seg.speaker || "");
            var full = (seg.arabic_text || seg.text || "");
            td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + "</span>");
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "▶"; b1.title = "Play original line";
            b1.onclick = function () { window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "▶"; b2.title = "Play dubbed line (with slider trim)";
            b2.onclick = function () { window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
            td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
            var c3 = document.createElement("td");
            var cur = (window._volumeGains || {})[ln.segment_id] || 0;
            var rg = document.createElement("input"); rg.type = "range"; rg.min = "-12"; rg.max = "12"; rg.step = "0.5"; rg.value = cur;
            rg.oninput = function () { window.onVolSlider(ln.segment_id, parseFloat(rg.value)); };
            c3.appendChild(rg); tr.appendChild(c3);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + " dB</span>");
            tbody.appendChild(tr);
        });
    };

    // showVolumeSection: seed sliders with matched values, place master on the LEFT, bind it
    var _oldShow = window.showVolumeSection;
    window.showVolumeSection = function (lines) {
        window._volumeGains = {};
        window._masterPrev = 0;
        (lines || []).forEach(function (ln) {
            window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0);
        });
        if (typeof _oldShow === "function") _oldShow(lines);
        else if (typeof buildVolumeTable === "function") buildVolumeTable(lines);
        window._volumeLines = lines;
        // Master on the LEFT
        var card = document.getElementById("volumeSection");
        if (card && !document.getElementById("masterTrimWrap")) {
            var wrap = document.createElement("div");
            wrap.id = "masterTrimWrap";
            wrap.style.cssText = "display:flex;align-items:center;gap:10px;justify-content:flex-start;margin:10px 0 2px;";
            wrap.innerHTML = '<strong style="font-size:13px;">🎚️ Master:</strong>' +
                '<input type="range" id="masterTrim" min="-12" max="12" step="0.5" value="0" style="width:200px;">' +
                '<span id="masterTrimLab" style="min-width:60px;">+0.0 dB</span>';
            var tw = card.querySelector(".table-wrap");
            if (tw) card.insertBefore(wrap, tw); else card.appendChild(wrap);
        }
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; mt.dataset.bound = "0"; var lb = document.getElementById("masterTrimLab"); if (lb) lb.textContent = "+0.0 dB"; }
        bindMaster();
    };

    // 4) FIX: keep "Load Voice Options" hidden permanently
    function hideLoadVoices() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Load Voice Options/i.test(b.textContent)) b.style.display = "none";
        });
    }
    hideLoadVoices();
    new MutationObserver(hideLoadVoices).observe(document.body, { childList: true, subtree: true });

    // Re-bind master whenever DOM changes
    new MutationObserver(bindMaster).observe(document.body, { childList: true, subtree: true });
})();

// ===== CUSTOM VOICE v2: appears in speaker dropdown + label =====
(function () {
    window._customVoiceNames = window._customVoiceNames || {};
    function fixCustomLabels() {
        Object.keys(window._customVoiceNames || {}).forEach(function (name) {
            document.querySelectorAll("#speakerVoicesTable tbody tr").forEach(function (row) {
                if ((row.cells[0] || {}).textContent !== name) return;
                var opt = row.querySelector('select option[value="clone"]');
                if (opt) opt.textContent = window._customVoiceNames[name];
                var info = row.querySelector("div.note, div[style*='font-size: 12px']");
                if (info && speakerChoices[name] === "clone") info.textContent = window._customVoiceNames[name];
            });
        });
    }
    if (typeof renderSpeakerVoices === "function" && !window._rsvWrapped2) {
        window._rsvWrapped2 = true;
        var _rsv = renderSpeakerVoices;
        renderSpeakerVoices = function () {
            var p = _rsv.apply(this, arguments);
            Promise.resolve(p).then(fixCustomLabels);
            return p;
        };
    }
    window.uploadCustomVoice = function () {
        var fEl = document.getElementById("cvFile"), spEl = document.getElementById("cvSpeaker"), st = document.getElementById("cvStatus");
        if (!fEl || !spEl) { notify("error", "Custom voice box not ready — refresh the page."); return; }
        var f = fEl.files[0], sp = spEl.value || "Speaker 1";
        if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
        if (!/\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
        var probe = new Audio(URL.createObjectURL(f));
        probe.onloadedmetadata = function () {
            if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s — the limit is 20 seconds."); return; }
            var form = new FormData();
            form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
            if (st) st.textContent = "Uploading & creating voice...";
            fetch("/api/upload_custom_voice", { method: "POST", body: form })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (out) {
                    if (st) st.textContent = "";
                    if (!out.ok || !out.d || out.d.error || !out.d.voice_id) { notify("error", (out.d && (out.d.error || out.d.detail)) || "Server error."); return; }
                    window._customVoiceNames[sp] = "📤 Custom voice (" + sp + ")";
                    clonedBySpeaker[sp] = out.d.voice_id;
                    speakerChoices[sp] = "clone";
                    speakerVoices[sp] = out.d.voice_id;
                    speakerVoiceNames[sp] = window._customVoiceNames[sp];
                    window.clonedVoiceIds = window.clonedVoiceIds || [];
                    window.clonedVoiceIds.push(out.d.voice_id);
                    renderSpeakerVoices();
                    notify("success", "Custom voice created and assigned to " + sp + ".");
                })
                .catch(function (e) { if (st) st.textContent = ""; notify("error", "Upload failed: " + e.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
})();

// ===== RESTORE v2: Step 5.5 buttons + master slider on the LEFT =====
(function () {
    function clampG(v) { return Math.max(-12, Math.min(12, v)); }
    function bindMaster() {
        var mt = document.getElementById("masterTrim");
        if (!mt || mt.dataset.bound === "1") return;
        mt.dataset.bound = "1";
        mt.oninput = function () {
            var m = parseFloat(mt.value) || 0;
            var lab = document.getElementById("masterTrimLab");
            if (lab) lab.textContent = (m > 0 ? "+" : "") + m.toFixed(1) + " dB";
            var delta = m - (window._masterPrev || 0);
            window._masterPrev = m;
            if (Math.abs(delta) < 0.001) return;
            (window._volumeLines || []).forEach(function (ln) {
                var g = clampG(((window._volumeGains || {})[ln.segment_id] || 0) + delta);
                window._volumeGains[ln.segment_id] = g;
                var node = (window.VOL_NODES || {})[ln.segment_id];
                if (node) node.g.gain.value = Math.pow(10, g / 20);
            });
            if (typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines || []);
            var btn = document.getElementById("applyVolumesBtn");
            if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
        };
    }
    window.onVolSlider = function (sid, val) {
        window._volumeGains = window._volumeGains || {};
        window._volumeGains[sid] = val;
        var node = (window.VOL_NODES || {})[sid];
        if (node) node.g.gain.value = Math.pow(10, (val + (window._masterPrev || 0)) / 20);
        var lab = document.getElementById("vollab_" + sid);
        if (lab) lab.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + " dB";
        var btn = document.getElementById("applyVolumesBtn");
        if (btn) btn.textContent = "🔊 Apply Volumes & Rebuild MP3 •";
    };
    window.buildVolumeTable = function (lines) {
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td(String(i + 1));
            td(ln.speaker || seg.speaker || "");
            var full = (seg.arabic_text || seg.text || "");
            td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + "</span>");
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "▶"; b1.title = "Play original line";
            b1.onclick = function () { if (window.playOrigLine) window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "▶"; b2.title = "Play dubbed line";
            b2.onclick = function () { if (window.playDubLine) window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
            td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
            var c3 = document.createElement("td");
            var cur = (window._volumeGains || {})[ln.segment_id] || 0;
            var rg = document.createElement("input"); rg.type = "range"; rg.min = "-12"; rg.max = "12"; rg.step = "0.5"; rg.value = cur;
            rg.oninput = function () { window.onVolSlider(ln.segment_id, parseFloat(rg.value)); };
            c3.appendChild(rg); tr.appendChild(c3);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + " dB</span>");
            tbody.appendChild(tr);
        });
    };
    var _oldShow = window.showVolumeSection;
    window.showVolumeSection = function (lines) {
        window._volumeGains = {}; window._masterPrev = 0;
        (lines || []).forEach(function (ln) { window._volumeGains[ln.segment_id] = clampG(Number(ln.auto_gain_db) || 0); });
        window._volumeLines = lines;
        if (typeof _oldShow === "function") _oldShow(lines);
        else window.buildVolumeTable(lines);
        var card = document.getElementById("volumeSection");
        if (card && !document.getElementById("masterTrimWrap")) {
            var wrap = document.createElement("div");
            wrap.id = "masterTrimWrap";
            wrap.style.cssText = "display:flex;align-items:center;gap:10px;justify-content:flex-start;margin:10px 0 2px;";
            wrap.innerHTML = '<strong style="font-size:13px;">🎚️ Master:</strong>' +
                '<input type="range" id="masterTrim" min="-12" max="12" step="0.5" value="0" style="width:200px;">' +
                '<span id="masterTrimLab" style="min-width:60px;">+0.0 dB</span>';
            var tw = card.querySelector(".table-wrap");
            if (tw) card.insertBefore(wrap, tw); else card.appendChild(wrap);
        }
        var mt = document.getElementById("masterTrim");
        if (mt) { mt.value = 0; mt.dataset.bound = "0"; var lb = document.getElementById("masterTrimLab"); if (lb) lb.textContent = "+0.0 dB"; }
        bindMaster();
    };
    function hideLoadVoices() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Load Voice Options/i.test(b.textContent)) b.style.display = "none";
        });
    }
    hideLoadVoices();
    new MutationObserver(hideLoadVoices).observe(document.body, { childList: true, subtree: true });
    new MutationObserver(bindMaster).observe(document.body, { childList: true, subtree: true });
})();
// ===== LIVE CREDITS v2 (notify hook) =====
// Calls refreshCredits() once whenever notify("success", ...) fires.
// No polling, no per-tick spam. Idempotent.
(function () {
    if (typeof window.notify !== "function") return;
    if (window._notifyCreditsHooked) return;
    window._notifyCreditsHooked = true;
    var _origNotify = window.notify;
    window.notify = function (type, msg) {
        try {
            if (type === "success" && typeof window.refreshCredits === "function") {
                setTimeout(window.refreshCredits, 50);  // defer so UI isn't blocked
            }
        } catch (e) {}
        return _origNotify.apply(this, arguments);
    };
})();

// ===== OVERLAP CONTROL v2: single flag-aware fade engine + UI updates =====
(function () {
    window.overlapAllowed = window.overlapAllowed || {};
    window.customBySpeaker = window.customBySpeaker || {};

    // send overlap flags with remix/generate requests
    if (!window._fetchOvWrap2) {
        window._fetchOvWrap2 = true;
        var _fetch = window.fetch;
        window.fetch = function (url, opts) {
            try {
                var u = String(url);
                if (opts && opts.body && typeof opts.body === "string" &&
                    (u.indexOf("/api/remix_audio") > -1 || u.indexOf("/api/generate") > -1)) {
                    var obj = JSON.parse(opts.body);
                    if (obj && typeof obj === "object") {
                        obj.overlap_allowed = window.overlapAllowed || {};
                        opts = Object.assign({}, opts, { body: JSON.stringify(obj) });
                    }
                }
            } catch (e) {}
            return _fetch.call(this, url, opts);
        };
    }

    // custom voice stays separate from the video clone (working — kept as-is)
    window.uploadCustomVoice = function () {
        var fEl = document.getElementById("cvFile"), spEl = document.getElementById("cvSpeaker"), st = document.getElementById("cvStatus");
        if (!fEl || !spEl) { notify("error", "Custom voice box not ready - refresh the page."); return; }
        var f = fEl.files[0], sp = spEl.value || "Speaker 1";
        if (!f) { notify("error", "Choose an MP3 or WAV clip first."); return; }
        if (!/\.(mp3|wav)$/i.test(f.name)) { notify("error", "Only MP3 or WAV files are allowed."); return; }
        var probe = new Audio(URL.createObjectURL(f));
        probe.onloadedmetadata = function () {
            if (probe.duration > 20.5) { notify("error", "Clip is " + Math.round(probe.duration) + "s - the limit is 20 seconds."); return; }
            var form = new FormData();
            form.append("file", f); form.append("speaker", sp); form.append("job_id", currentJobId || "");
            if (st) st.textContent = "Uploading & creating voice...";
            fetch("/api/upload_custom_voice", { method: "POST", body: form })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (out) {
                    if (st) st.textContent = "";
                    if (!out.ok || !out.d || out.d.error || !out.d.voice_id) { notify("error", (out.d && (out.d.error || out.d.detail)) || "Server error."); return; }
                    window.customBySpeaker[sp] = out.d.voice_id;
                    window.clonedVoiceIds = window.clonedVoiceIds || [];
                    window.clonedVoiceIds.push(out.d.voice_id);
                    speakerChoices[sp] = "custom";
                    speakerVoices[sp] = out.d.voice_id;
                    speakerVoiceNames[sp] = "\uD83D\uDCE4 Custom voice";
                    renderSpeakerVoices();
                    notify("success", "Custom voice created. The cloned voice stays in the dropdown.");
                })
                .catch(function (err) { if (st) st.textContent = ""; notify("error", "Upload failed: " + err.message); });
        };
        probe.onerror = function () { notify("error", "Could not read that audio file."); };
    };
    if (typeof applyChoice === "function" && !window._acWrap7) {
        window._acWrap7 = true;
        var _ac = applyChoice;
        applyChoice = function (name) {
            if ((speakerChoices[name] || "") === "custom" && window.customBySpeaker[name]) {
                speakerVoices[name] = window.customBySpeaker[name];
                speakerVoiceNames[name] = "\uD83D\uDCE4 Custom voice";
                return;
            }
            return _ac.apply(this, arguments);
        };
    }
    if (typeof renderSpeakerVoices === "function" && !window._rsvWrap7) {
        window._rsvWrap7 = true;
        var _rsv = renderSpeakerVoices;
        renderSpeakerVoices = function () {
            var p = _rsv.apply(this, arguments);
            var done = function () {
                document.querySelectorAll("#speakerVoicesTable tbody tr").forEach(function (row) {
                    var name = (row.cells[0] || {}).textContent || "";
                    var sel = row.querySelector("select");
                    if (!sel || !window.customBySpeaker[name]) return;
                    if (!sel.querySelector('option[value="custom"]')) {
                        var o = document.createElement("option");
                        o.value = "custom";
                        o.textContent = "\uD83D\uDCE4 Custom voice (" + name + ")";
                        var co = sel.querySelector('option[value="clone"]');
                        if (co) co.insertAdjacentElement("afterend", o); else sel.appendChild(o);
                    }
                    sel.value = speakerChoices[name] || sel.value;
                });
            };
            if (p && p.then) p.then(done); else done();
            return p;
        };
    }

    // Step 2 header: lock / unlock ALL lines icon
    function injectLockAll() {
        var sec = document.getElementById("editorSection");
        if (!sec || document.getElementById("lockAllBtn")) return;
        var h = sec.querySelector("h3");
        if (!h) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn"; b.className = "action-btn"; b.textContent = "\uD83D\uDD13";
        b.title = "Lock / unlock ALL lines";
        b.style.marginLeft = "8px";
        b.onclick = function () {
            var allLocked = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; });
            segmentsData.forEach(function (s) { s.locked = !allLocked; });
            b.textContent = allLocked ? "\uD83D\uDD13" : "\uD83D\uDD12";
            renderTable();
            notify("info", allLocked ? "All lines unlocked." : "All lines locked.");
        };
        h.appendChild(b);
    }
    injectLockAll();
    new MutationObserver(injectLockAll).observe(document.body, { childList: true, subtree: true });

    // Step 6: hide Fine-Tune button, timeline always visible with results
    function syncTimelineVisibility() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/fine-?tune timeline/i.test(b.textContent || "")) b.style.display = "none";
        });
        var rs = document.getElementById("resultSection");
        var ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden") && ts.classList.contains("hidden")) {
            ts.classList.remove("hidden");
            if (typeof renderTimeline === "function") renderTimeline();
        }
    }
    syncTimelineVisibility();
    new MutationObserver(syncTimelineVisibility).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });

    // Step 5.5 table: No-overlap checkbox as 4th column
    window.buildVolumeTable = function (lines) {
        var thead = document.querySelector("#volumeTable thead");
        if (thead && !thead.dataset.v7) {
            thead.dataset.v7 = "1";
            thead.innerHTML = "<tr><th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; lines overlapping it are NOT faded'>No overlap</th><th>\u25B6 Orig</th><th>\uD83D\uDD0A Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th></tr>";
        }
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td(String(i + 1));
            td(ln.speaker || seg.speaker || "");
            var full = (seg.arabic_text || seg.text || "");
            td('<span title="' + full.replace(/"/g, "'") + '">' + full.slice(0, 60) + "</span>");
            var cN = document.createElement("td");
            var cb = document.createElement("input"); cb.type = "checkbox";
            cb.checked = !window.overlapAllowed[ln.segment_id];
            cb.title = "Checked = protected (intruders get faded). Uncheck = allow talk-over without fading.";
            cb.onchange = function () {
                if (cb.checked) delete window.overlapAllowed[ln.segment_id];
                else window.overlapAllowed[ln.segment_id] = true;
                if (typeof renderTimeline === "function") renderTimeline();
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "\uD83D\uDD0A Apply Volumes & Rebuild MP3 \u2022";
            };
            cN.appendChild(cb); tr.appendChild(cN);
            var c1 = document.createElement("td");
            var b1 = document.createElement("button"); b1.className = "action-btn green"; b1.textContent = "\u25B6"; b1.title = "Play original line";
            b1.onclick = function () { if (window.playOrigLine) window.playOrigLine(ln, b1); }; c1.appendChild(b1); tr.appendChild(c1);
            var c2 = document.createElement("td");
            var b2 = document.createElement("button"); b2.className = "action-btn green"; b2.textContent = "\u25B6"; b2.title = "Play dubbed line";
            b2.onclick = function () { if (window.playDubLine) window.playDubLine(ln, b2); }; c2.appendChild(b2); tr.appendChild(c2);
            td((ln.auto_gain_db > 0 ? "+" : "") + ln.auto_gain_db + " dB");
            var c3 = document.createElement("td");
            var cur = (window._volumeGains || {})[ln.segment_id] || 0;
            var rg = document.createElement("input"); rg.type = "range"; rg.min = "-12"; rg.max = "12"; rg.step = "0.5"; rg.value = cur;
            rg.oninput = function () { window.onVolSlider(ln.segment_id, parseFloat(rg.value)); };
            c3.appendChild(rg); tr.appendChild(c3);
            td('<span id="vollab_' + ln.segment_id + '">' + (cur > 0 ? "+" : "") + cur.toFixed(1) + " dB</span>");
            tbody.appendChild(tr);
        });
    };

    // THE single fade engine: glued to blocks, live during drag, honors overlap flags
    var pending7 = false;
    function schedule7() { if (pending7) return; pending7 = true; requestAnimationFrame(function () { pending7 = false; draw7(); }); }
    function draw7() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".segFadeOv, .fadeOv7").forEach(function (f) { f.remove(); });
        Array.prototype.forEach.call(wrap.querySelectorAll("div"), function (d) {
            var bg = (d.style.background || "") + (d.style.backgroundImage || "");
            if (bg.indexOf("f59e0b") > -1 && d.style.pointerEvents === "none") d.remove();
        });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var divs = wrap.querySelectorAll("div");
        for (var bi = 0; bi < divs.length; bi++) {
            var b = divs[bi];
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") === -1 || st.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            b.style.boxShadow = window.overlapAllowed[seg.segment_id] ? "inset 0 0 0 2px #22c55e" : "";
            var cs = seg.start + (segmentOffsets[seg.segment_id] || 0);
            var slot = seg.end - seg.start;
            var audioEnd = cs + Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var boundary = total;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if (window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < boundary) boundary = qs;
            });
            var fadeFrom = boundary - 0.005;
            var ov = audioEnd - fadeFrom;
            var lane = b.parentElement; if (!lane) continue;
            var f = document.createElement("div");
            f.className = "fadeOv7";
            f.style.position = "absolute";
            f.style.top = b.offsetTop + "px";
            f.style.height = b.offsetHeight + "px";
            if (ov <= 0.02) f.style.display = "none";
            f.style.left = (b.offsetLeft + Math.max(0, (fadeFrom - cs) * scale)) + "px";
            f.style.width = Math.max(3, (Math.min(audioEnd, total) - fadeFrom) * scale) + "px";
            f.style.background = "repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px)";
            f.style.opacity = "0.9";
            f.style.borderRadius = "0 4px 4px 0";
            f.style.pointerEvents = "none";
            f.style.zIndex = "4";
            f.title = "Faded/trimmed in the final mix";
            lane.appendChild(f);
            if (!b.dataset.fadeHook7) {
                b.dataset.fadeHook7 = "1";
                new MutationObserver(schedule7).observe(b, { attributes: true, attributeFilter: ["style"] });
            }
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    if (typeof renderTimeline === "function" && !window._fadeV7) {
        window._fadeV7 = true;
        var _rt7 = renderTimeline;
        renderTimeline = function () { var r = _rt7.apply(this, arguments); draw7(); return r; };
    }
    if (typeof checkGenerateProgress === "function" && !window._durWrap7) {
        window._durWrap7 = true;
        var _cg7 = checkGenerateProgress;
        checkGenerateProgress = async function () {
            await _cg7.apply(this, arguments);
            try {
                var r = await fetch("/api/progress/generate?t=" + Date.now());
                var d = await r.json();
                if (d && d.status === "done" && d.result && Array.isArray(d.result.lines)) {
                    window._lineDurations = window._lineDurations || {};
                    d.result.lines.forEach(function (ln) { if (ln && ln.duration) window._lineDurations[ln.segment_id] = ln.duration; });
                }
            } catch (e) {}
        };
    }
})();

// ===== UI UPDATE v3 =====
(function () {
    window.addEventListener("click", function (e) { var a = e.target && e.target.closest ? e.target.closest('a[href^="/"]') : null; if (a) window._internalNav = true; }, true);
    if (typeof window.doLogout === "function" && !window._dlW3) { window._dlW3 = true; var _dl = window.doLogout; window.doLogout = function () { window._forceWarn = true; return _dl.apply(this, arguments); }; }
    function fixStep2() {
        document.querySelectorAll("button, a").forEach(function (b) {
            var tx = (b.textContent || "").trim();
            if (/^(⬇️?\s*)?(Export\s+)?(SRT|SBV)$/i.test(tx)) b.style.display = "none";
            if (/Import SRT\/SBV/i.test(tx)) b.textContent = "Import Eng. Subtitle";
        });
    }
    function fixLockAll() {
        document.querySelectorAll("#lockAllBtn2").forEach(function (b) { if (!b.closest("#segmentsTable thead")) b.remove(); });
        var thr = document.querySelector("#segmentsTable thead tr");
        if (!thr || document.querySelector("#segmentsTable thead #lockAllBtn2")) return;
        var ths = thr.querySelectorAll("th"), target = null;
        ths.forEach(function (th) { if (/actions/i.test(th.textContent || "")) target = th; });
        if (!target && ths.length) target = ths[ths.length - 1];
        if (!target) return;
        var b = document.createElement("button");
        b.id = "lockAllBtn2"; b.className = "action-btn"; b.textContent = "🔓"; b.title = "Lock / unlock ALL lines"; b.style.marginLeft = "6px";
        b.onclick = function () { var all = segmentsData.length > 0 && segmentsData.every(function (s) { return s.locked; }); segmentsData.forEach(function (s) { s.locked = !all; }); b.textContent = all ? "🔓" : "🔒"; renderTable(); notify("info", all ? "All lines unlocked." : "All lines locked."); };
        target.appendChild(b);
    }
    function fixVolume() {
        var thr = document.querySelector("#volumeTable thead tr");
        if (thr && !thr.dataset.v9) { thr.dataset.v9 = "1"; thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; intruders are NOT faded'>No overlap</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style='min-width:130px'>Trim</th><th></th>"; }
        document.querySelectorAll("#volumeSection strong, #volumeSection span").forEach(function (el) { if (/Master trim/i.test(el.textContent || "")) el.textContent = (el.textContent || "").replace(/Master trim[^\(:]*/i, "Master volume"); });
    }
    function fixStep6() {
        document.querySelectorAll("button").forEach(function (b) {
            if (/Fine-?Tune Timeline/i.test((b.textContent || "").trim())) {
                var p = document.createElement("p"); p.className = "step-sub"; p.style.cssText = "font-weight:600;margin:10px 0 4px;"; p.textContent = "Fine-Tune Timeline";
                b.parentNode.insertBefore(p, b); b.remove();
            }
        });
        var rs = document.getElementById("resultSection"), ts = document.getElementById("timelineSection");
        if (rs && ts && !rs.classList.contains("hidden")) { ts.classList.remove("hidden"); if (typeof renderTimeline === "function") renderTimeline(); }
    }
    function fixDub() {
        var btn = null;
        document.querySelectorAll("#resultSection button, #mergeSection button, #videoResults button, #videoResults a").forEach(function (b) { if (/Dub Another Video/i.test(b.textContent || "")) btn = b; });
        if (!btn) return;
        if (btn.style.background.indexOf("237, 108, 2") === -1 && btn.style.cssText.indexOf("#ed6c02") === -1) btn.style.cssText += ";background:#ed6c02;border-color:#ed6c02;color:#fff;font-weight:700;";
        var row = document.querySelector("#videoResults .download-buttons");
        if (row && btn.parentNode !== row) {
            var sep = document.createElement("span");
            sep.style.cssText = "width:1px;align-self:stretch;background:#cbd5e1;margin:0 12px;";
            row.style.display = "flex"; row.style.alignItems = "center";
            row.appendChild(sep); row.appendChild(btn);
            btn.style.marginLeft = "auto";
        }
    }
    // ---- autosave / restore session ----
    var SAVE_KEY = "lisan_workspace_v1";
    function saveWs() {
        try {
            if (!segmentsData.length) return;
            localStorage.setItem(SAVE_KEY, JSON.stringify({
                currentJobId: currentJobId, totalDuration: totalDuration, isVideoUpload: isVideoUpload,
                segments: segmentsData, originalSegments: originalSegments,
                speakerVoices: speakerVoices, speakerVoiceNames: speakerVoiceNames,
                speakerChoices: speakerChoices, clonedBySpeaker: clonedBySpeaker,
                customBySpeaker: window.customBySpeaker || {}, segmentOffsets: segmentOffsets
            }));
        } catch (e) {}
    }
    function restoreWs() {
        try {
            if (segmentsData.length) return;
            var raw = localStorage.getItem(SAVE_KEY);
            if (!raw) return;
            var d = JSON.parse(raw);
            if (!d || !Array.isArray(d.segments) || !d.segments.length) return;
            segmentsData = d.segments; originalSegments = d.originalSegments || [];
            currentJobId = d.currentJobId || null; totalDuration = d.totalDuration || 0; isVideoUpload = !!d.isVideoUpload;
            speakerVoices = d.speakerVoices || {}; speakerVoiceNames = d.speakerVoiceNames || {};
            speakerChoices = d.speakerChoices || {}; clonedBySpeaker = d.clonedBySpeaker || {};
            window.customBySpeaker = d.customBySpeaker || {}; segmentOffsets = d.segmentOffsets || {};
            renderTable(); if (typeof renderSpeakerVoices === "function") renderSpeakerVoices();
            ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(function (id) { var el = document.getElementById(id); if (el) el.classList.remove("hidden"); });
            if (typeof showMediaBanner === "function") showMediaBanner();
            notify("info", "Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.");
        } catch (e) {}
    }
    setInterval(saveWs, 4000);
    window.addEventListener("beforeunload", saveWs);
    if (typeof window.renderTable === "function" && !window._saveWrapped) { window._saveWrapped = true; var _rt0 = window.renderTable; window.renderTable = function () { var r = _rt0.apply(this, arguments); saveWs(); return r; }; }
    setTimeout(restoreWs, 600);
    // ---- Step 2 edits flow to 5.5 + timeline, dragged positions preserved ----
    var prevStarts = {};
    function snap() { segmentsData.forEach(function (s) { prevStarts[s.segment_id] = s.start; }); }
    function onChg(e) {
        var inp = e.target;
        if (!inp || !inp.closest || !inp.closest("#segmentsTable")) return;
        var tr = inp.closest("tr"); if (!tr || !tr.parentNode) return;
        var i = Array.prototype.indexOf.call(tr.parentNode.children, tr);
        var seg = segmentsData[i]; if (!seg) return;
        var cell = inp.closest("td");
        var ci = cell ? Array.prototype.indexOf.call(tr.children, cell) : -1;
        if (ci === 2) { var prev = prevStarts[seg.segment_id]; if (typeof prev === "number") { var dlt = seg.start - prev; if (Math.abs(dlt) > 0.0001) segmentOffsets[seg.segment_id] = (segmentOffsets[seg.segment_id] || 0) - dlt; } }
        prevStarts[seg.segment_id] = seg.start;
        if (typeof renderTimeline === "function") renderTimeline();
        if (window._volumeLines && window._volumeLines.length && typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines);
        saveWs();
    }
    document.addEventListener("change", onChg, true);
    snap();
    // ---- FADE v8: fade is the block's own striped tail; cross-speaker only ----
    function draw8() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".fadeFinal").forEach(function (f) { f.remove(); });
        Array.prototype.forEach.call(wrap.querySelectorAll("div"), function (d) {
            var st = d.getAttribute("style") || "";
            if (st.indexOf("repeating-linear-gradient") > -1 && !d.classList.contains("fadeFinal") && !(d.parentNode && (d.parentNode.getAttribute("style") || "").indexOf("cursor") > -1)) d.remove();
        });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var divs = wrap.querySelectorAll("div");
        for (var bi = 0; bi < divs.length; bi++) {
            var b = divs[bi];
            var bst = b.getAttribute("style") || "";
            if (bst.indexOf("cursor") === -1 || bst.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            var off = segmentOffsets[seg.segment_id] || 0;
            var cs = seg.start + off;
            var slot = seg.end - seg.start;
            var dur = Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var cap = Infinity;
            act.forEach(function (q) { if (q.segment_id !== seg.segment_id && (q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) { var qs = q.start + (segmentOffsets[q.segment_id] || 0); if (qs > cs + 0.0001 && qs < cap) cap = qs; } });
            var visEnd = Math.min(cs + dur, cap);
            var limit = Infinity;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if ((q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) return;
                if (window.overlapAllowed && window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < limit) limit = qs;
            });
            var blockWPx = Math.max(8, (visEnd - cs) * scale);
            var wStr = blockWPx + "px";
            if (b.style.width !== wStr) b.style.width = wStr;
            var want = (window.overlapAllowed && window.overlapAllowed[seg.segment_id]) ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== want) b.style.boxShadow = want;
            if (limit === Infinity || cs + dur <= limit + 0.02) continue;
            var fadeLeftPx = Math.max(0, (limit - cs) * scale);
            if (fadeLeftPx >= blockWPx - 2) continue;
            var f = document.createElement("div");
            f.className = "fadeFinal";
            f.style.cssText = "position:absolute;top:0;height:100%;left:" + fadeLeftPx + "px;width:" + (blockWPx - fadeLeftPx) + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;";
            f.title = "Faded/trimmed in the final mix (runs into the next other-speaker line)";
            b.appendChild(f);
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    var pend8 = false;
    function sched8() { if (pend8) return; pend8 = true; requestAnimationFrame(function () { pend8 = false; draw8(); }); }
    function hook8() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div").forEach(function (b) {
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") > -1 && st.indexOf("grab") > -1 && !b.dataset.hook8) { b.dataset.hook8 = "1"; new MutationObserver(sched8).observe(b, { attributes: true, attributeFilter: ["style"] }); }
        });
    }
    if (typeof renderTimeline === "function" && !window._fadeV8) {
        window._fadeV8 = true;
        var _rt8 = renderTimeline;
        renderTimeline = function () { var r = _rt8.apply(this, arguments); draw8(); hook8(); return r; };
    }
    function runAll() { fixStep2(); fixLockAll(); fixVolume(); fixStep6(); fixDub(); }
    runAll();
    var tmo = null;
    new MutationObserver(function () { clearTimeout(tmo); tmo = setTimeout(function () { runAll(); hook8(); }, 300); }).observe(document.body, { childList: true, subtree: true });
})();

// ===== UI UPDATE v4: pure collision timeline (fixed widths, connected fades only) =====
(function () {
    if (window._uiV4) return; window._uiV4 = true;
    function draw9() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData.length) return;
        wrap.querySelectorAll(".fadeFinal").forEach(function (f) { f.remove(); });
        var total = totalDuration > 0 ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var act = segmentsData.filter(function (s) { return (s.arabic_text || "").trim(); });
        var divs = wrap.querySelectorAll("div");
        for (var bi = 0; bi < divs.length; bi++) {
            var b = divs[bi];
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") === -1 || st.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            var off = segmentOffsets[seg.segment_id] || 0;
            var cs = seg.start + off;
            var slot = seg.end - seg.start;
            var slotPx = Math.max(8, slot * scale);
            var wStr = slotPx + "px";
            if (b.style.width !== wStr) b.style.width = wStr;
            var want = (window.overlapAllowed && window.overlapAllowed[seg.segment_id]) ? "inset 0 0 0 2px #22c55e" : "";
            if (b.style.boxShadow !== want) b.style.boxShadow = want;
            var dur = Math.max((window._lineDurations || {})[seg.segment_id] || 0, slot);
            var limit = Infinity;
            act.forEach(function (q) {
                if (q.segment_id === seg.segment_id) return;
                if ((q.speaker || "Speaker 1") === (seg.speaker || "Speaker 1")) return;
                if (window.overlapAllowed && window.overlapAllowed[q.segment_id]) return;
                var qs = q.start + (segmentOffsets[q.segment_id] || 0);
                if (qs > cs + 0.0001 && qs < limit) limit = qs;
            });
            if (limit === Infinity || cs + dur <= limit + 0.02) continue;
            var fadeLeft = Math.max(0, (limit - cs) * scale);
            if (fadeLeft >= slotPx - 2) continue;
            var f = document.createElement("div");
            f.className = "fadeFinal";
            f.style.cssText = "position:absolute;top:0;height:100%;left:" + fadeLeft + "px;width:" + (slotPx - fadeLeft) + "px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 4px,#d97706 4px,#d97706 8px);opacity:0.9;border-radius:0 4px 4px 0;pointer-events:none;";
            f.title = "Faded/trimmed in the final mix (runs into the next other-speaker line)";
            b.appendChild(f);
        }
        if (!document.getElementById("timelineLegendFinal")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendFinal";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:6px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:#42a5f5;border-radius:3px;display:inline-block;"></span>kept</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:repeating-linear-gradient(45deg,#f59e0b,#f59e0b 3px,#d97706 3px,#d97706 6px);border-radius:3px;display:inline-block;"></span>faded / trimmed</span><span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;border:2px solid #22c55e;border-radius:3px;display:inline-block;"></span>overlap allowed</span>';
            wrap.parentNode.insertBefore(leg, wrap.nextSibling);
        }
    }
    var pend = false;
    function sched9() { if (pend) return; pend = true; requestAnimationFrame(function () { pend = false; draw9(); }); }
    function hook9() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div").forEach(function (b) {
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") > -1 && st.indexOf("grab") > -1 && !b.dataset.hook9) {
                b.dataset.hook9 = "1";
                new MutationObserver(sched9).observe(b, { attributes: true, attributeFilter: ["style"] });
            }
        });
    }
    if (typeof renderTimeline === "function") {
        var _rt = renderTimeline;
        renderTimeline = function () { var r = _rt.apply(this, arguments); draw9(); hook9(); return r; };
    }
})();

