const EMOTIONS = ["neutral","happy","sad","angry","fearful","surprised","disgusted","shouting","whispering","screaming","yelling","crying","laughing","sarcastic","seductive","narrative","announcer","conversational","depressed","anxious","confident","indifferent","excited","serious","playful","terrified","relieved","thoughtful","mocking","pleading","commanding","slowly","rushed","drawn out","hesitant","stammering","softly","booming","sorrowful","frustrated","annoyed","appalled","awe","regretful","resigned","curious","deadpan","tired"];
const CREDIT_USD = 0.01;
const GEMINI_IN_PER_M = 0.30, GEMINI_OUT_PER_M = 2.50;
const AUDIO_TOKENS_PER_SEC = 258;
const VOICE_USD_PER_1K_CHARS = 0.18;
const GEMINI_TEXT_MODELS = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"];
const COIN_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v9"/><path d="M14.5 9.5c0-1-1.1-1.6-2.5-1.6s-2.5.6-2.5 1.6 1.1 1.6 2.5 1.6 2.5.6 2.5 1.6-1.1 1.6-2.5 1.6-2.5-.6-2.5-1.6"/></svg>`;
const NOTIFY_AUTO_CLOSE_MS = 10000;
// Timeline blocks are sized by how much English text a line has, not by its
// timespan (seg.end - seg.start) -- this constant converts character count
// into an "equivalent duration" at a typical speaking rate, so it can still
// be multiplied by the timeline's normal pixels-per-second scale.
const ENGLISH_CHARS_PER_SEC = 15;

let segmentsData = [], originalSegments = [];
let speakerVoices = {}, speakerVoiceNames = {};
let speakerChoices = {}, clonedBySpeaker = {};
let voicePools = { male: [], female: [] };
let availableVoices = [];
let segmentOffsets = {};
let currentJobId = null, totalDuration = 0, isVideoUpload = false;
let transcribePollTimer = null, generatePollTimer = null, emotionPollTimer = null, lipsyncPollTimer = null;
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
    if (/voice_add_edit_limit_reached|monthly limit of voice add\/edit/i.test(msg)) {
        return "🎙️ Voice cloning limit reached: your voice engine account has hit its monthly cap for creating/editing voices. This resets automatically next month, or you can upgrade your voice engine plan to raise the limit. Meanwhile you can skip cloning and pick a numbered studio voice for this speaker in Step 4.";
    }
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
// Real per-step charges from the server's own pricing config (admin-editable) --
// these fall back to the current server defaults until /api/pricing answers, so
// the badges below are never wrong even before that fetch completes.
window._realPricing = { transcribeCredits: 3, mergeCredits: 1, charsPerCredit: 60, cloneCredits: 5, lipsyncCreditsPerSec: 10 };
async function loadRealPricing() {
    try {
        const res = await fetch("/api/pricing");
        if (res.ok) {
            const d = await res.json();
            if (typeof d.transcribeCredits === "number") window._realPricing.transcribeCredits = d.transcribeCredits;
            if (typeof d.mergeCredits === "number") window._realPricing.mergeCredits = d.mergeCredits;
            if (typeof d.charsPerCredit === "number") window._realPricing.charsPerCredit = d.charsPerCredit;
            if (typeof d.cloneCredits === "number") window._realPricing.cloneCredits = d.cloneCredits;
            if (typeof d.lipsyncCreditsPerSec === "number") window._realPricing.lipsyncCreditsPerSec = d.lipsyncCreditsPerSec;
        }
    } catch (e) {}
    updateBadges();
}
function updateBadges() {
    setBadge("badgeTranscribe", window._realPricing.transcribeCredits);
    setBadge("badgeImport", 0);
    setBadge("badgeSRT", 0);
    setBadge("badgeSBV", 0);
    setBadge("badgeSave", 0);
    setBadge("badgeLoad", 0);
    setBadge("badgeTranslate", usdToCredits(translateEstimateUsd()));
    setBadge("badgeTashkeel", usdToCredits(tashkeelEstimateUsd()));
    setBadge("badgeEmotions", usdToCredits(emotionsEstimateUsd()));
    setBadge("badgeAutoFix", 0);
    // Step 4: Auto-Assign and Browse Voice Library only pick/preview existing
    // studio voices -- no new voice is created, so both are free. Choosing a
    // file is just a local file picker (no server call, no cost) -- only
    // clicking "Upload as this speaker's voice" actually creates a voice
    // (same ElevenLabs quota as Clone), so only THAT button carries the
    // cloneCredits price. (Both used to show the same badge, which read as
    // two separate 5-credit charges for one action.)
    setBadge("badgeAutoAssign", 0);
    setBadge("badgeVoiceLibrary", 0);
    setBadge("badgeCvUpload", window._realPricing.cloneCredits);
    setBadge("badgePrepareClone", 0);
    setBadge("badgeClone", window._realPricing.cloneCredits);
    setBadge("badgeGenerate", usdToCredits(generateEstimateUsd()));
    setBadge("badgeMerge", window._realPricing.mergeCredits);
    // Step 5.5's "Apply changes & rebuild MP3" only re-mixes with ffmpeg (no TTS
    // call), so it's free -- shown explicitly rather than left with no badge.
    setBadge("badgeApplyVolumes", 0);
    // Lip-sync is billed per second of video, not a flat fee, so it doesn't use
    // the coin-badge pattern -- just update the rate shown in Step 7's note.
    var lsRate = document.getElementById("lipsyncRateNote");
    if (lsRate) lsRate.textContent = window._realPricing.lipsyncCreditsPerSec;
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
    throw new Error(lastErr || "Text engine call failed");
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

function stopRowPreview() {
    if (previewAudio) { try { previewAudio.pause(); } catch (e) {} previewAudio = null; }
    if (previewBtnCurrent) { previewBtnCurrent.textContent = "▶"; previewBtnCurrent = null; }
}
function previewRow(i, btn) {
    if (!currentJobId) { notify("error", "Transcribe or load a project first."); return; }
    if (previewAudio && previewBtnCurrent === btn) { stopRowPreview(); return; }
    stopRowPreview();
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
    const startPlaying = () => { try { a.currentTime = Math.max(0, playStart); } catch (e) {} a.play().catch(err => { notify("error", "Preview playback failed: " + err.message); stopRowPreview(); }); };
    if (a.readyState >= 1) startPlaying(); else a.addEventListener("loadedmetadata", startPlaying, { once: true });
    a.addEventListener("timeupdate", () => { if (a.currentTime >= playEnd) stopRowPreview(); });
    a.addEventListener("error", () => { notify("error", "Preview unavailable: source audio not found."); stopRowPreview(); }, { once: true });
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
            segmentsData = p.segments.map(s => ({ segment_id: s.segment_id || ("seg_" + Math.random().toString(36).slice(2, 8)), start: Number(s.start) || 0, end: Number(s.end) || 0, speaker: s.speaker || "Speaker 1", gender: s.gender || "male", emotion: s.emotion || "neutral", text: s.text || "", arabic_text: s.arabic_text || "", locked: !!s.locked, tempo_mode: s.tempo_mode || "excellent", words: Array.isArray(s.words) ? s.words : [] }));
            originalSegments = Array.isArray(p.original_segments) ? p.original_segments : [];
            speakerVoices = p.speaker_voices || {};
            speakerVoiceNames = p.speaker_voice_names || {};
            speakerChoices = p.speaker_choices || {};
            clonedBySpeaker = p.cloned_by_speaker || {};
            if (p.job_id) currentJobId = p.job_id;
            if (Number(p.total_duration) > 0) totalDuration = Number(p.total_duration);
            isVideoUpload = !!p.is_video;
            renderTable(); renderSpeakerVoices();
            // Step 1 remains visible for media upload
            ["editorSection", "voicesSection", "speakerVoicesSection", "generateSection"].forEach(id => document.getElementById(id).classList.remove("hidden"));
            document.getElementById("attachMediaSection").classList.remove("hidden");
            projectWasLoaded = true;
            notify("success", "Project loaded. Please upload the matching audio/video file to enable preview, re-speak, and other functions.");
            workspaceHasMedia = false;
            fetchUsage(); updateBadges();
            validateClonedVoicesAfterLoad();
        } catch (e) { notify("error", "Load failed: " + e.message); }
    };
    reader.readAsText(f); evt.target.value = "";
}

// Cloned/custom voices are deleted from the voice account automatically (they're
// meant to be ephemeral). A project saved before that cleanup still points to those
// old voice_ids, so after loading a project we re-check each "cloned" speaker against
// the account's current voice list and clear any that no longer exist — this makes the
// existing "No voice for: ..." check in generateAudio() catch it before generation,
// instead of the server erroring out mid-generation with a raw voice_not_found error.
async function validateClonedVoicesAfterLoad() {
    var speakersWithClone = Object.keys(clonedBySpeaker || {}).filter(function (sp) { return clonedBySpeaker[sp]; });
    if (!speakersWithClone.length) return;
    try {
        var res = await fetch("/api/voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        var data = await res.json();
        if (!data || !Array.isArray(data.voices)) return; // couldn't verify right now — leave assignments as-is
        var validIds = {};
        data.voices.forEach(function (v) { validIds[v.voice_id] = true; });
        var invalidSpeakers = [];
        speakersWithClone.forEach(function (sp) {
            if (!validIds[clonedBySpeaker[sp]]) {
                invalidSpeakers.push(sp);
                delete clonedBySpeaker[sp];
                delete speakerVoices[sp];
                delete speakerVoiceNames[sp];
                delete speakerChoices[sp];
            }
        });
        if (invalidSpeakers.length) {
            if (typeof renderSpeakerVoices === "function") renderSpeakerVoices();
            if (typeof updateBadges === "function") updateBadges();
            notify("error", "🎙️ The cloned voice(s) for " + invalidSpeakers.join(", ") + " no longer exist in your voice account (old cloned voices are removed automatically). Re-clone in Step 3.5 or pick a voice in Step 4 before generating.");
        }
    } catch (e) { /* offline or API hiccup — leave assignments as-is rather than block the user */ }
}


async function attachMedia(input) {
    const file = input.files[0];
    if (!file) return;

    const form = new FormData();
    form.append("file", file);

    const label = document.getElementById("fileUploadText");
    if (label) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        label.textContent = file.name + " (" + sizeMB + " MB)";
    }

    const pf = document.getElementById("progressFill");
    const pt = document.getElementById("progressText");
    if (pf) pf.style.width = "0%";
    if (pt) pt.textContent = "Uploading... 0%";
    document.getElementById("progressSection").classList.remove("hidden");

    try {
        const data = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/attach_media");
            xhr.upload.onprogress = function (e) {
                if (e.lengthComputable && pf && pt) {
                    const pct = Math.round((e.loaded / e.total) * 100);
                    pf.style.width = pct + "%";
                    pt.textContent = "Uploading... " + pct + "%";
                }
            };
            xhr.upload.onload = function () {
                if (pf) pf.style.width = "100%";
                if (pt) pt.textContent = "Processing media...";
            };
            xhr.onload = function () {
                try { resolve(JSON.parse(xhr.responseText)); }
                catch (e) { reject(new Error("Bad server response")); }
            };
            xhr.onerror = function () { reject(new Error("Network error during upload")); };
            xhr.send(form);
        });

        if (data.job_id) {
            currentJobId = data.job_id;
            isVideoUpload = data.is_video;
            workspaceHasMedia = true;
            if (pt) pt.textContent = "Upload complete.";

            // Hide the attach section
            document.getElementById("attachMediaSection").classList.add("hidden");
            // The "no media on server" warning no longer applies now that media is attached
            if (typeof hideMediaBanner === "function") hideMediaBanner();

            // Show success
            notify("success", "Media attached successfully. All functions are now enabled.");

            // Update UI state
            updateBadges();
        } else {
            notify("error", "Failed to attach media: " + (data.error || "Unknown error"));
        }
    } catch (e) {
        notify("error", "Failed to attach media: " + e.message);
    } finally {
        document.getElementById("progressSection").classList.add("hidden");
        input.value = "";
    }
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
    segmentsData.splice(i + 1, 0, { segment_id: "manual_" + Date.now(), start: Number(start.toFixed(2)), end: Number(end.toFixed(2)), speaker: cur.speaker, gender: cur.gender, emotion: cur.emotion, text: "", arabic_text: "", locked: false, tempo_mode: cur.tempo_mode || "excellent" });
    renderTable(); renderSpeakerVoices();
    notify("info", "Manual line inserted. Use ✨ Auto-Fix to sync its time.");
}
// ===== Internal-pause detection (auto-suggest a segment split) =====
// Whisper sometimes groups two natural spoken phrases -- with a real pause
// between them -- into a single segment (its segment boundaries don't
// always land on every silence). A Segment here only has one start/end/
// text, so that internal pause has nowhere to live: the Arabic translation
// gets generated as one continuous line and TTS renders it as one fluent
// utterance stretched across the whole slot, and the pause is gone. This
// looks at the word-level timestamps Whisper already returns per segment
// (segment.words, set in whisper_service.py's transcribe_worker) to find
// a gap between two consecutive words big enough to be a real pause rather
// than normal word-to-word spacing, so the row can offer to split there.
//
// 0.6s -- ordinary fluent speech (including a quick breath or a comma)
// usually has well under 0.5s between words, so this stays clear of that
// and only catches a real, deliberate beat. (Was 0.45s; raised after that
// setting caught too many ordinary breathing/comma pauses in real testing
// and produced far more splits than were actually wanted.) Only used as a
// last resort now, when a segment has no audio-measured pause_gaps at all
// -- see detectInternalPause below for why that's the primary signal.
const INTERNAL_PAUSE_THRESHOLD_SEC = 0.6;

// Decides where to cut a segment, given a real, audio-measured silence
// window for it (gapStart/gapEnd -- ffmpeg's silencedetect, independent of
// Whisper entirely). Returns the index of the last word that belongs
// BEFORE the pause, or -1 if no word clearly does.
//
// Real clips have shown Whisper's own per-word timestamps getting
// unreliable right around a genuine pause, and not in just one direction:
// the last word before a pause can have its END overrun into the silence;
// a word can straddle the ENTIRE pause, starting before it and ending
// after; and a word can have its START bleed backward, landing before the
// pause even though it's actually spoken after it. Patching each of those
// as its own special case doesn't scale -- comparing every word to the
// pause as a whole window, using its MIDPOINT rather than either single
// edge, handles all of them the same way: a word whose midpoint still
// falls inside the real silence is never trusted as being cleanly on
// either side of it, no matter which of its own edges is the one that
// drifted. Once a later word's midpoint is clearly past the pause, the
// boundary can't move any further -- so a bad backward-bled START on a
// word that's actually after the pause can't override a good, earlier
// boundary the way a simpler "word.start < gapStart" check could.
function classifyPauseBoundary(words, gapStart, gapEnd) {
    var idx = -1;
    for (var m = 0; m < words.length - 1; m++) {
        var mid = (words[m].start + words[m].end) / 2;
        if (mid < gapStart) {
            idx = m;
        } else if (mid <= gapEnd) {
            // Straddles the real pause too much to trust either way --
            // don't let it move the boundary, but keep scanning in case
            // a clean "before" word still follows (rare, but cheap to allow).
            continue;
        } else {
            break; // clearly past the pause -- nothing later can be "before" it
        }
    }
    return idx;
}

function detectInternalPause(seg) {
    var words = seg && seg.words;
    if (!words || words.length < 2) return null;
    // If the segment's start/end were hand-edited since transcription, the
    // word timestamps below no longer describe this row reliably -- don't
    // suggest a split from stale data.
    if (Math.abs(words[0].start - seg.start) > 0.5) return null;
    if (Math.abs(words[words.length - 1].end - seg.end) > 0.5) return null;

    // Ground truth first: the backend measures real silence directly from
    // the audio for each segment (seg.pause_gaps, via ffmpeg's
    // silencedetect) -- that's far more trustworthy than anything inferred
    // purely from Whisper's own word timings, which is exactly the part of
    // its output that keeps turning out to be unreliable near a pause (see
    // classifyPauseBoundary above). Only consider gaps that actually fall
    // inside THIS segment's own start/end -- pause_gaps isn't
    // re-partitioned word-by-word when a segment splits (see
    // autoSplitAllPauses), so a stale gap belonging to a sibling piece must
    // never be allowed to trigger another split here.
    var gaps = seg.pause_gaps;
    if (gaps && gaps.length) {
        var widest = null;
        for (var g = 0; g < gaps.length; g++) {
            if (gaps[g].start < seg.start - 0.05 || gaps[g].end > seg.end + 0.05) continue;
            if (!widest || (gaps[g].end - gaps[g].start) > (widest.end - widest.start)) widest = gaps[g];
        }
        if (widest) {
            var idx = classifyPauseBoundary(words, widest.start, widest.end);
            if (idx >= 0 && idx < words.length - 1) {
                return { index: idx, gap: widest.end - widest.start, usedGap: widest };
            }
        }
    }

    // Fallback: no usable audio-measured gap for this segment at all
    // (ffmpeg failed, or nothing it found lines up inside these bounds) --
    // the raw gap between consecutive words is the only signal left.
    //
    // Never use it, though, on a piece that already came out of a previous
    // auto-split (seg._autoSplitChild). Real clip proof of why: a segment
    // with ONE true pause -- "The stewardess said <pause> both pilots." --
    // got correctly cut there using the real measured silence, but the
    // silence was long enough that Whisper's own word timing inside it
    // still showed a big leftover gap between "both" and "pilots." (its
    // recorded word for "both" ends well before the real silence does).
    // That leftover word-timing gap isn't a SECOND real pause -- it's just
    // residue of the exact same measured silence that already produced
    // this split -- but the raw fallback below can't tell the difference
    // and split "both" | "pilots." apart on it anyway. Once ground truth
    // has already explained a segment's silence, only more ground truth
    // (another real, distinct pause_gaps entry) should be allowed to
    // justify splitting it further.
    if (seg._autoSplitChild) return null;
    var best = null;
    for (var k = 0; k < words.length - 1; k++) {
        var gap = words[k + 1].start - words[k].end;
        if (gap >= INTERNAL_PAUSE_THRESHOLD_SEC && (!best || gap > best.gap)) {
            best = { index: k, gap: gap };
        }
    }
    return best;
}

// Runs once, right after a fresh transcription lands (before the user has
// touched anything), and silently splits every line that has a detected
// internal pause -- no button, no manual step. Returns how many splits it
// made, for the "Auto-split N line(s)..." note in the completion toast.
function autoSplitAllPauses() {
    var splitCount = 0;
    var i = 0;
    while (i < segmentsData.length) {
        var seg = segmentsData[i];
        var pause = detectInternalPause(seg);
        // Kept on permanently (console only, no UI, negligible cost) so a
        // wrong split can be diagnosed from a pasted console log alone,
        // without needing a code change first just to capture the data.
        if (seg && seg.words && seg.words.length > 1) {
            try {
                console.log("[pause-detect] seg " + i + " \"" + seg.text + "\" pause=" +
                    JSON.stringify(pause) + " pause_gaps=" + JSON.stringify(seg.pause_gaps || []) +
                    " words=" + JSON.stringify(seg.words.map(function (w) { return { w: (w.word || "").trim(), s: w.start, e: w.end }; })));
            } catch (e) { /* never let logging break the split */ }
        }
        if (!pause) { i++; continue; }
        var words = seg.words;
        var originalEnd = seg.end;
        var firstWords = words.slice(0, pause.index + 1);
        var secondWords = words.slice(pause.index + 1);
        var firstText = firstWords.map(function (w) { return (w.word || "").trim(); }).join(" ").trim();
        var secondText = secondWords.map(function (w) { return (w.word || "").trim(); }).join(" ").trim();
        var newFirstEnd = Number(firstWords[firstWords.length - 1].end.toFixed(2));
        var newSecondStart = Number(secondWords[0].start.toFixed(2));

        // The backend's audio-measured pause_gaps belonged to the WHOLE
        // original segment -- split it between the two new halves by where
        // each gap actually falls, so a leftover gap from further down the
        // line can never be mistaken for another pause inside the (now
        // shorter) first half on a later pass through this same loop.
        // The gap that actually triggered THIS split (pause.usedGap, when
        // the fallback path found it) is explicitly dropped from both sides
        // first and unconditionally -- on a clip where one word's own
        // timestamp overran across the whole pause, that gap could still
        // land inside the new first half's range by the filters below and
        // fire a second, spurious split using the same silence that was
        // just used.
        var allGaps = (seg.pause_gaps || []).filter(function (g) {
            return !(pause.usedGap && g.start === pause.usedGap.start && g.end === pause.usedGap.end);
        });
        var firstGaps = allGaps.filter(function (g) { return g.end <= newFirstEnd + 0.05; });
        var secondGaps = allGaps.filter(function (g) { return g.start >= newSecondStart - 0.05; });

        // Same split-by-position treatment for the VAD suspect-gap flags
        // (seg.suspect_gaps, set by the backend's flag_suspect_word_gaps):
        // each flagged gap belonged to the whole original segment, so it
        // has to follow whichever half its own start/end actually falls
        // into, or a flag could end up attached to a half that no longer
        // contains the words it was about.
        var allSuspects = seg.suspect_gaps || [];
        var firstSuspects = allSuspects.filter(function (g) { return g.end <= newFirstEnd + 0.05; });
        var secondSuspects = allSuspects.filter(function (g) { return g.start >= newSecondStart - 0.05; });

        // Mutate the original row into the first half...
        seg.end = newFirstEnd;
        seg.text = firstText;
        seg.words = firstWords;
        seg.arabic_text = "";
        seg.locked = false;
        seg._autoSplitChild = true; // see the comment on that check in detectInternalPause
        if (firstGaps.length) { seg.pause_gaps = firstGaps; } else { delete seg.pause_gaps; }
        if (firstSuspects.length) { seg.suspect_gaps = firstSuspects; } else { delete seg.suspect_gaps; }

        // ...and insert the second half right after it, using the real
        // measured gap as the boundary so the pause is actually preserved.
        // end stays at the ORIGINAL segment's end (captured before the
        // mutation above), not the last word's own end, so it still lines
        // up with whatever segment (if any) comes right after this one.
        var secondSeg = {
            segment_id: "split_" + Date.now() + "_" + Math.random().toString(36).slice(2, 6),
            start: newSecondStart,
            end: Number(originalEnd.toFixed(2)),
            speaker: seg.speaker, gender: seg.gender, emotion: seg.emotion,
            text: secondText, arabic_text: "", locked: false,
            tempo_mode: seg.tempo_mode || "excellent",
            words: secondWords,
            _autoSplitChild: true, // see the comment on that check in detectInternalPause
        };
        if (secondGaps.length) secondSeg.pause_gaps = secondGaps;
        if (secondSuspects.length) secondSeg.suspect_gaps = secondSuspects;
        segmentsData.splice(i + 1, 0, secondSeg);
        splitCount++;
        // Re-check index i again (don't advance) -- the first half may
        // itself still contain another pause if the original line had
        // more than one. Once it's clean, the loop naturally moves on to
        // the second half at i+1 and checks that too.
    }
    return splitCount;
}

function deleteSegment(i) { if (!confirm("Delete this segment?")) return; segmentsData.splice(i, 1); cleanUnusedSpeakerVoices(); renderTable(); renderSpeakerVoices(); }
function cleanUnusedSpeakerVoices() {
    const active = new Set(segmentsData.map(s => s.speaker));
    Object.keys(speakerVoices).forEach(n => { if (!active.has(n)) { delete speakerVoices[n]; delete speakerVoiceNames[n]; delete speakerChoices[n]; delete clonedBySpeaker[n]; } });
}

// ===== Step 1.5 speaker count/names -> Step 2 dropdown options =====
// Pure helper: reads the two Step 1.5 inputs and returns the name list they imply
// (named speakers first, then "Speaker N" for any remaining count). Does NOT look
// at segmentsData, so it's safe to call before or after transcription.
function computeSpeakerNamesFromInputs() {
    var namesRaw = (document.getElementById("speakerNames") || {}).value || "";
    var names = namesRaw.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
    var countRaw = (document.getElementById("speakerCount") || {}).value;
    var count = parseInt(countRaw, 10) || 0;
    var n = Math.max(count, names.length);
    var list = [];
    for (var idx = 0; idx < n; idx++) list.push(names[idx] || ("Speaker " + (idx + 1)));
    return list;
}
// Used to populate the Step 2 Speaker dropdown: the Step 1.5 list, plus any speaker
// name already present in the current segments (covers Load Project, or a count
// smaller than what was actually detected) so no existing value is ever orphaned.
function getSpeakerOptionsList() {
    var list = computeSpeakerNamesFromInputs();
    (segmentsData || []).forEach(function(s) { if (s.speaker && list.indexOf(s.speaker) === -1) list.push(s.speaker); });
    return list.length ? list : ["Speaker 1"];
}
// Called once right after transcription returns. The backend labels segments with
// its own default "Speaker 1", "Speaker 2", ... — this renames those (and only
// those; anything already renamed is left alone) to match the names typed in
// Step 1.5, in order.
function remapDefaultSpeakerLabels() {
    var list = computeSpeakerNamesFromInputs();
    if (!list.length) return;
    (segmentsData || []).forEach(function(s) {
        var m = /^Speaker (\d+)$/.exec(s.speaker || "");
        if (m) {
            var idx = parseInt(m[1], 10) - 1;
            if (list[idx]) s.speaker = list[idx];
        }
    });
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
    const res = await fetch("/api/progress/generate?job_id=" + encodeURIComponent(currentJobId || ""));
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
            <audio controls src="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}"></audio>
            <div class="download-buttons"><a href="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download MP3</a></div>`;
        // Step 7 (lip-sync) stays hidden -- disabled in config.py
        // (LIPSYNC_ENABLED) until a provider proves reliable; see that
        // comment for why. mergeSection (Step 6's "merge into video") is
        // unrelated and still reveals normally.
        if (isVideoUpload) { document.getElementById("mergeSection").classList.remove("hidden"); }
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
            <video controls src="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed_video.mp4?cache=${Date.now()}"></video>
            <div class="download-buttons">
                <a href="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed_video.mp4?cache=${Date.now()}" download="final_dubbed_video.mp4">⬇️ Download Dubbed Video (MP4)</a>
                <a class="blue" href="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download Pure Vocals (MP3)</a>
            </div>`;
        notify("success", "Video merged successfully!");
    } catch (e) { document.getElementById("mergeButton").disabled = false; notify("error", e.message); }
}

async function runLipsync() {
    if (!currentJobId) { notify("error", "No job found."); return; }
    const btn = document.getElementById("lipsyncButton");
    if (btn) btn.disabled = true;
    const resultsEl = document.getElementById("lipsyncResults");
    if (resultsEl) { resultsEl.classList.add("hidden"); resultsEl.innerHTML = ""; }
    const progEl = document.getElementById("lipsyncProgress");
    if (progEl) progEl.classList.remove("hidden");
    const fill0 = document.getElementById("lipsyncProgressFill");
    const txt0 = document.getElementById("lipsyncProgressText");
    if (fill0) fill0.style.width = "5%";
    if (txt0) txt0.textContent = "Starting...";
    notify("info", "Starting lip-sync — this re-processes the full video and can take a few minutes...");
    try {
        const res = await fetch("/api/lipsync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: currentJobId })
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.status !== "started") {
            const msg = (data && data.error) || ("Server error " + res.status);
            notify("error", "Lip-sync failed to start: " + msg);
            if (progEl) progEl.classList.add("hidden");
            if (btn) btn.disabled = false;
            if (res.status === 402) openBuyModal();
            return;
        }
        window._lipsyncCost = data.credits_charged || 0;
        if (lipsyncPollTimer) clearInterval(lipsyncPollTimer);
        lipsyncPollTimer = setInterval(checkLipsyncProgress, 1500);
    } catch (e) {
        notify("error", e.message);
        if (progEl) progEl.classList.add("hidden");
        if (btn) btn.disabled = false;
    }
}

async function checkLipsyncProgress() {
    if (!currentJobId) return;
    try {
        const res = await fetch("/api/progress/lipsync/" + encodeURIComponent(currentJobId) + "?t=" + Date.now());
        const data = await res.json();
        if (!data || data.status === "not_found") return;
        const fill = document.getElementById("lipsyncProgressFill");
        const txt = document.getElementById("lipsyncProgressText");
        const percent = safePercent(data.percent, 5);
        if (fill) fill.style.width = percent + "%";
        if (txt) txt.textContent = percent + "% — " + (data.message || "Processing...");
        if (data.status === "done") {
            clearInterval(lipsyncPollTimer);
            lipsyncPollTimer = null;
            const btn = document.getElementById("lipsyncButton");
            if (btn) btn.disabled = false;
            if (fill) fill.style.width = "100%";
            if (txt) txt.textContent = "100% — Lip-sync complete.";
            const videoFile = (data.result && data.result.video) || (currentJobId + "_final_lipsync.mp4");
            const costNote = window._lipsyncCost ? (" Cost: " + window._lipsyncCost + " credits.") : "";
            const resultsEl = document.getElementById("lipsyncResults");
            if (resultsEl) {
                resultsEl.classList.remove("hidden");
                resultsEl.innerHTML =
                    '<h4>🎭 Lip-Synced Video:</h4>' +
                    '<video controls src="/api/download/' + encodeURIComponent(videoFile) + '?cache=' + Date.now() + '"></video>' +
                    '<div class="download-buttons"><a href="/api/download/' + encodeURIComponent(videoFile) + '?cache=' + Date.now() + '" download="final_lipsync.mp4">⬇️ Download Lip-Synced Video (MP4)</a></div>';
            }
            notify("success", "Lip-sync complete." + costNote);
            fetchUsage();
            if (typeof updateBadges === "function") updateBadges();
            if (typeof refreshCredits === "function") refreshCredits();
        }
        if (data.status === "error") {
            clearInterval(lipsyncPollTimer);
            lipsyncPollTimer = null;
            const btn = document.getElementById("lipsyncButton");
            if (btn) btn.disabled = false;
            const progEl = document.getElementById("lipsyncProgress");
            if (progEl) progEl.classList.add("hidden");
            notify("error", "Lip-sync failed: " + (data.error || "Unknown error"));
        }
    } catch (e) {
        console.error("Lip-sync progress check failed:", e);
    }
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
        if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
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
        if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
    } catch (e) { notify("error", e.message); }
}

window.addEventListener('DOMContentLoaded', loadRealPricing);

const MAX_UPLOAD_BYTES = 400 * 1024 * 1024;
const MAX_DURATION_SEC = 60.5;
const MIN_DURATION_SEC = 4;
const LIPSYNC_MAX_DURATION_SEC = 15;
const CLONE_QUALITY_WARN_SEC = 30;

// Updates the helper note under the lip-sync checkbox in Step 1 as it's
// toggled -- the actual enforcement happens in startTranscribe() below and
// (for real, since JS can't be trusted) server-side in /api/transcribe;
// this is just keeping the visible copy in sync with which range applies.
function onLipsyncChoiceChanged(checkbox) {
    const note = document.getElementById("lipsyncChoiceNote");
    if (!note) return;
    note.innerHTML = checkbox && checkbox.checked
        ? "Lip-sync selected: clip must be <strong>4-15 seconds</strong>."
        : "Clip must be <strong>4-60 seconds</strong>.";
}

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
        notify("error", "File too large (" + (file.size / 1048576).toFixed(0) + " MB). The limit is 400 MB — a 1-minute 1080p clip is usually well under 150 MB.");
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
        let resultsHtml = "";
        Object.keys(cloned).forEach(speaker => {
            const vid = cloned[speaker];
            if (!vid.startsWith("ERROR")) {
                clonedBySpeaker[speaker] = vid; speakerChoices[speaker] = "clone"; window.clonedVoiceIds.push(vid); applyChoice(speaker); ok++;
                resultsHtml += "<div style='display:flex;align-items:center;gap:10px;padding:6px 0;flex-wrap:wrap;'>"
                    + "<span>✅ " + speaker + " cloned.</span>"
                    + "<a href='/api/download_voice_sample/" + encodeURIComponent(currentJobId) + "/" + encodeURIComponent(speaker) + "' download><button type='button' class='btn-sm' style='cursor:pointer;'>⬇ Download voice sample</button></a>"
                    + "<span class='note'>(available for this session only)</span>"
                    + "</div>";
            }
            else notify("error", speaker + ": " + vid);
        });
        const resultsBox = document.getElementById("cloneResultsBox");
        if (resultsBox) resultsBox.innerHTML = resultsHtml;
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
        // No single global tempo_mode anymore — each segment in `segments`
        // now carries its own tempo_mode (set per-row in the Step 5.5 table).
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
                tempo_mode: seg.tempo_mode || "excellent",
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
        // Keep the timeline's Arabic-audio-duration overlay in sync: a bulk
        // Generate refreshes window._lineDurations via checkGenerateProgress,
        // but re-speaking a single line here never did, so the overlay kept
        // showing the stale (or no) duration after changing this line's Time
        // Stretch setting and re-speaking it. Update it here and force a
        // redraw so the change is visible immediately.
        if (data.stretched_duration) {
            window._lineDurations = window._lineDurations || {};
            window._lineDurations[seg.segment_id] = data.stretched_duration;
        }
        if (typeof renderTimeline === "function") renderTimeline();
        // Keep the Step 5.5 table's "needs attention" marking in sync with what
        // actually happened to this line and to the mix as a whole -- the mark
        // clears itself the moment these flags say the issue is gone.
        var _vln = (window._volumeLines || []).find(function (v) { return v.segment_id === seg.segment_id; });
        if (_vln) _vln.tempo_warning = !!data.tempo_warning;
        if (data.mix && Array.isArray(data.mix.trimmed_segment_ids)) {
            var _trimmedNow = {};
            data.mix.trimmed_segment_ids.forEach(function (sid) { _trimmedNow[sid] = true; });
            (window._volumeLines || []).forEach(function (ln) { ln.trimmed = !!_trimmedNow[ln.segment_id]; });
        }
        if (typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines || []);
        const au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        fetchUsage();
    } catch (e) {
        notify("error", e.message);
    } finally {
        btn.disabled = false; btn.textContent = "🔄";
    }
}

// Pure editing action for the Step 5.5 Time Stretch dropdown: re-warps this
// line's ALREADY-GENERATED audio to the newly picked setting instantly — no
// new TTS call, no ElevenLabs credits spent, unlike regenerateLine() above
// (which re-speaks the line from scratch and is for when the text, emotion,
// voice, or the Step 2 time span itself changes). Silently does nothing if
// the line hasn't been generated yet, so picking a setting ahead of time
// doesn't produce a confusing error.
async function restretchLine(seg) {
    if (!seg || !(seg.arabic_text || "").trim()) return;
    try {
        const res = await fetch("/api/restretch_line", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                job_id: currentJobId || "",
                segment: seg,
                segments: segmentsData,
                tempo_mode: seg.tempo_mode || "excellent",
                duration_mode: document.getElementById("durationMode").value,
                total_duration: totalDuration
            })
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!data || data.status !== "success") {
            if (data && data.error && data.error !== "not_generated") notify("error", "Time Stretch update failed: " + data.error);
            return;
        }
        if (data.stretched_duration) {
            window._lineDurations = window._lineDurations || {};
            window._lineDurations[seg.segment_id] = data.stretched_duration;
        }
        if (typeof renderTimeline === "function") renderTimeline();
        if (window.VOL_NODES && window.VOL_NODES[seg.segment_id]) {
            try { window.VOL_NODES[seg.segment_id].a.pause(); } catch (e) {}
            delete window.VOL_NODES[seg.segment_id];
        }
        // Same needs-attention sync as regenerateLine() -- keeps the Step 5.5
        // table's warning mark accurate (and auto-clearing) after a pure
        // Time Stretch re-warp too, not just a full re-speak.
        var _vln2 = (window._volumeLines || []).find(function (v) { return v.segment_id === seg.segment_id; });
        if (_vln2) _vln2.tempo_warning = !!data.tempo_warning;
        if (data.mix && Array.isArray(data.mix.trimmed_segment_ids)) {
            var _trimmedNow2 = {};
            data.mix.trimmed_segment_ids.forEach(function (sid) { _trimmedNow2[sid] = true; });
            (window._volumeLines || []).forEach(function (ln) { ln.trimmed = !!_trimmedNow2[ln.segment_id]; });
        }
        if (typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines || []);
        const au = document.querySelector("#audioResults audio");
        if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        const idx = segmentsData.indexOf(seg);
        notify("success", `Line ${idx + 1} re-stretched: ${data.stretched_duration}s into a ${data.target}s window.` + (data.tempo_warning ? " ⚠️ stretched to the limit." : ""));
    } catch (e) {
        // Quiet auto-apply on a dropdown change — don't nag on a network hiccup.
    }
}


async function startTranscribe() {
    const file = document.getElementById("audioFile").files[0];
    if (!file) { notify("error", "Choose an audio or video file first."); return; }
    const consentBox = document.getElementById("voiceConsentCheckbox");
    if (consentBox && !consentBox.checked) {
        notify("error", "Please check the voice-rights consent box in Step 1 before starting.");
        consentBox.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
        notify("error", "File too large (" + (file.size / 1048576).toFixed(0) + " MB). The limit is 400 MB — a 1-minute 1080p clip is usually well under 150 MB.");
        return;
    }
    const lipsyncBox = document.getElementById("lipsyncWantedCheckbox");
    const lipsyncWanted = !!(lipsyncBox && lipsyncBox.checked);
    const maxDur = lipsyncWanted ? LIPSYNC_MAX_DURATION_SEC : MAX_DURATION_SEC;
    const dur = await probeFileDuration(file);
    if (dur !== null && dur < MIN_DURATION_SEC) {
        notify("error", "This clip is only " + dur.toFixed(1) + " seconds long. The minimum is " + MIN_DURATION_SEC + " seconds.");
        return;
    }
    if (dur !== null && dur > maxDur) {
        const limitDesc = lipsyncWanted ? "For a lip-synced clip, the" : "The";
        notify("error", "This clip is " + Math.round(dur) + " seconds long. " + limitDesc + " limit is " + maxDur + " seconds — please trim it first.");
        return;
    }
    // Not a blocker -- cloning can still run on a shorter clip, it just may
    // not sound as convincing (ElevenLabs' own guidance: ~30s of clean
    // audio is where they've seen consistently good results). Worth
    // telling the user up front rather than only after they've spent
    // credits on a clone that doesn't sound like the speaker.
    if (dur !== null && dur < CLONE_QUALITY_WARN_SEC) {
        notify("info", "Heads up: this clip is under " + CLONE_QUALITY_WARN_SEC + " seconds. Voice cloning can still run, but a longer clip usually sounds more convincing.");
    }
    // Blank field -> 0, which whisper_service.py already treats as "no
    // hint, auto-detect" (both in the diarization call and in the "only
    // found fewer than requested" warning, which only fires when
    // speaker_count is truthy). Defaulting a blank field to 2 here used to
    // silently turn "I didn't specify a count" into "I specifically asked
    // for 2", which is what produced a confusing warning for a 1-speaker
    // video nobody actually requested 2 speakers for.
    const speakerCount = parseInt(document.getElementById("speakerCount").value, 10) || 0;
    const form = new FormData();
    form.append("file", file);
    form.append("speaker_count", speakerCount);
    form.append("voice_consent", "true");
    form.append("lipsync", lipsyncWanted ? "true" : "false");
    const pf = document.getElementById("progressFill");
    const pt = document.getElementById("progressText");
    document.getElementById("progressSection").classList.remove("hidden");
    if (pf) pf.style.width = "0%";
    if (pt) pt.textContent = "Uploading... 0%";
    notify("info", "Uploading and starting transcription...");
    try {
        const data = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/transcribe");
            xhr.upload.onprogress = function (e) {
                if (e.lengthComputable && pf && pt) {
                    const pct = Math.round((e.loaded / e.total) * 100);
                    pf.style.width = pct + "%";
                    pt.textContent = "Uploading... " + pct + "%";
                }
            };
            xhr.upload.onload = function () {
                if (pf) pf.style.width = "100%";
                if (pt) pt.textContent = "Upload complete. Starting...";
            };
            xhr.onload = function () {
                try { resolve(JSON.parse(xhr.responseText)); }
                catch (e) { reject(new Error("Bad server response")); }
            };
            xhr.onerror = function () { reject(new Error("Network error during upload")); };
            xhr.send(form);
        });
        currentJobId = data.job_id; originalSegments = [];
        speakerVoices = {}; speakerVoiceNames = {}; speakerChoices = {}; clonedBySpeaker = {};
        voicePools = { male: [], female: [] };
        if (pt) pt.textContent = "Upload complete. Starting...";
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
    const res = await fetch("/api/progress/generate?job_id=" + encodeURIComponent(currentJobId || ""));
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
            <audio controls src="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}"></audio>
            <div class="download-buttons"><a href="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download MP3</a></div>`;
        // Step 7 (lip-sync) stays hidden -- disabled in config.py
        // (LIPSYNC_ENABLED) until a provider proves reliable; see that
        // comment for why. mergeSection (Step 6's "merge into video") is
        // unrelated and still reveals normally.
        if (isVideoUpload) { document.getElementById("mergeSection").classList.remove("hidden"); }
        fetchUsage(); updateBadges();
    }
    if (data.status === "error") { clearInterval(generatePollTimer); document.getElementById("generateButton").disabled = false; notify("error", data.error); }
};


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
    const res = await fetch("/api/progress/generate?job_id=" + encodeURIComponent(currentJobId || ""));
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
            <audio controls src="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}"></audio>
            <div class="download-buttons"><a href="/api/download/${encodeURIComponent(currentJobId || "")}_final_dubbed.mp3?cache=${Date.now()}" download="final_dubbed.mp3">⬇️ Download MP3</a></div>`;
        // Step 7 (lip-sync) stays hidden -- disabled in config.py
        // (LIPSYNC_ENABLED) until a provider proves reliable; see that
        // comment for why. mergeSection (Step 6's "merge into video") is
        // unrelated and still reveals normally.
        if (isVideoUpload) { document.getElementById("mergeSection").classList.remove("hidden"); }
        fetchUsage(); updateBadges();
    }
    if (data.status === "error") { clearInterval(generatePollTimer); document.getElementById("generateButton").disabled = false; notify("error", data.error); }
};


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





// ===== FILE UPLOAD LABEL =====
function onFileSelected(input) {
    var label = document.getElementById("fileUploadText");
    var step15 = document.getElementById("step1_5Card");
    var consentSection = document.getElementById("voiceConsentSection");
    var consentBox = document.getElementById("voiceConsentCheckbox");
    if (input.files && input.files[0]) {
        var f = input.files[0];
        var sizeMB = (f.size / (1024 * 1024)).toFixed(1);
        var fullText = f.name + " (" + sizeMB + " MB)";
        label.textContent = fullText;
        label.title = fullText; // full name on hover -- text may be truncated visually
        if (step15) step15.classList.remove("hidden");
        // Only show the consent checkbox once there's actually a file to
        // certify about. New upload -- require fresh consent for it. If a
        // previous upload in this same page session was already certified
        // and locked (see onVoiceConsentChanged), re-enable and uncheck it
        // here so it can't be silently carried over to a different file.
        if (consentSection) consentSection.classList.remove("hidden");
        if (consentBox) { consentBox.checked = false; consentBox.disabled = false; }
    } else {
        label.textContent = "Upload Media";
        label.title = "";
        if (consentSection) consentSection.classList.add("hidden");
        if (consentBox) { consentBox.checked = false; consentBox.disabled = false; }
    }
}

// Once checked, lock the voice-rights checkbox so it can't be casually
// unchecked afterward -- the certification was made for this specific
// upload. onFileSelected (above) re-enables and resets it the moment a
// new file is chosen, so the next upload needs its own fresh consent.
function onVoiceConsentChanged(checkbox) {
    if (checkbox && checkbox.checked) checkbox.disabled = true;
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
    var numCell = mk("td"); numCell.style.textAlign = "center"; numCell.style.color = "#607d8b"; numCell.style.fontWeight = "600"; numCell.textContent = i + 1;
    // Backend's VAD cross-check (vad_utils.flag_suspect_word_gaps) found a
    // word-to-word gap inside this line that Whisper's own timestamps call
    // empty, but real voice-activity data says otherwise -- surface it so
    // the word timing gets a manual look before it silently sets a wrong
    // duration for a paid TTS generation. Never auto-fixed, just flagged.
    if (seg.suspect_gaps && seg.suspect_gaps.length) {
        var warn = mk("span");
        warn.textContent = " ⚠️";
        warn.style.cursor = "help";
        warn.title = seg.suspect_gaps.map(function(g) { return g.reason; }).join("\n\n");
        numCell.appendChild(warn);
    }
    row.appendChild(numCell);
    var startCell = mk("td"); var si = mk("input"); si.type = "number"; si.step = "0.01"; si.value = seg.start; si.onchange = function() { segmentsData[i].start = parseFloat(si.value) || 0; updateBadges(); }; startCell.appendChild(si); row.appendChild(startCell);
    var endCell = mk("td"); var ei = mk("input"); ei.type = "number"; ei.step = "0.01"; ei.value = seg.end; ei.onchange = function() { segmentsData[i].end = parseFloat(ei.value) || 0; updateBadges(); }; endCell.appendChild(ei); row.appendChild(endCell);
    var spCell = mk("td"); var spS = mk("select"); spS.style.width = "100%";
    getSpeakerOptionsList().forEach(function(nm) { var o = mk("option"); o.value = nm; o.textContent = nm; o.selected = (seg.speaker === nm); spS.appendChild(o); });
    spS.onchange = function() { updateSpeakerName(i, spS.value); };
    spCell.appendChild(spS); row.appendChild(spCell);
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
    var arCell = mk("td"); var arT = mk("textarea"); arT.dir = "rtl"; arT.value = seg.arabic_text; arT.onchange = function() {
        segmentsData[i].arabic_text = arT.value; updateBadges();
        if (typeof buildVolumeTable === "function") buildVolumeTable(window._volumeLines || []);
    }; arCell.appendChild(arT); row.appendChild(arCell);
    var aCell = mk("td");
    var pb = mk("button"); pb.className = "action-btn green"; pb.textContent = "▶"; pb.title = "Play original audio"; pb.onclick = function() { previewRow(i, pb); };
    var rb = mk("button"); rb.className = "action-btn orange"; rb.textContent = "🔄"; rb.title = "Re-speak this line only"; rb.onclick = function() { regenerateLine(i, rb); };
    var ib = mk("button"); ib.className = "action-btn"; ib.textContent = "Insert"; ib.onclick = function() { insertSegmentAfter(i); };
    var db = mk("button"); db.className = "action-btn red"; db.textContent = "Delete"; db.onclick = function() { deleteSegment(i); };
    var lb = mk("button"); lb.className = "action-btn"; lb.textContent = seg.locked ? "🔒" : "🔓"; lb.title = seg.locked ? "Locked" : "Lock this line"; lb.onclick = function() { toggleLock(i); };
    aCell.appendChild(pb); aCell.appendChild(rb); aCell.appendChild(ib); aCell.appendChild(db); aCell.appendChild(lb);
    // No manual "Split" button here -- detected pauses are split
    // automatically right after transcription (see autoSplitAllPauses),
    // before the row is ever rendered.
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
            // No cap here — list every voice in the pool (used to stop at 8
            // per gender, which silently hid the rest of the account's
            // voices from this dropdown even though Browse Voice Library
            // could show them all).
            (voicePools[g] || []).forEach(function(p, i) {
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

// ===== VOICE LIBRARY BROWSER (preview the SAME voices offered in the Step 4 =====
// dropdown below — this used to search ElevenLabs' external shared-voice
// marketplace with its own filters, which is a different, much smaller pool
// than the account voices the dropdown lists. Now both read from the same
// ensureVoicePools()/availableVoices data (from /api/voices), so the two
// lists can never drift apart again.
var vlPage = 0;

function toggleVoiceLibraryBrowser() {
    var el = document.getElementById("voiceLibraryBrowser");
    if (!el) return;
    var wasHidden = el.classList.contains("hidden");
    el.classList.toggle("hidden");
    if (wasHidden) {
        vlPage = 0;
        searchVoiceLibrary(0);
    } else {
        stopPreview();
    }
}

function maskedVoiceList() {
    // Builds ONE ordered, name-masked list using the exact same voicePools
    // (and the exact same "Male voice N" / "Female voice N" numbering) that
    // the Step 4 dropdown's addGroup() uses — so voice #N shown here in
    // Browse is always the same voice as option #N in the dropdown, and the
    // real account voice name is never shown in Browse.
    var out = [];
    (voicePools.male || []).forEach(function (v, i) {
        out.push({ label: "🎲 Male voice " + (i + 1), preview_url: v.preview_url || "", age: v.age || "", use_case: v.use_case || "" });
    });
    (voicePools.female || []).forEach(function (v, i) {
        out.push({ label: "🎲 Female voice " + (i + 1), preview_url: v.preview_url || "", age: v.age || "", use_case: v.use_case || "" });
    });
    return out;
}

async function searchVoiceLibrary(page) {
    var box = document.getElementById("vlResults");
    var pager = document.getElementById("vlPager");
    if (!box) return;
    stopPreview();
    box.innerHTML = "<p class='note'>Loading voices...</p>";
    if (pager) pager.innerHTML = "";
    try {
        var ok = await ensureVoicePools();
        var list = maskedVoiceList();
        if (!ok || !list.length) { box.innerHTML = "<p class='note'>Could not load voices right now. Please try again.</p>"; return; }
        vlPage = page || 0;
        var pageSize = 6;
        var start = vlPage * pageSize;
        var pageVoices = list.slice(start, start + pageSize);
        if (!pageVoices.length) { box.innerHTML = "<p class='note'>No voices found.</p>"; return; }
        var html = "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:10px;'>";
        pageVoices.forEach(function (v) {
            var desc = [v.age, v.use_case].filter(Boolean).join(", ");
            html += "<div style='display:flex;flex-direction:column;align-items:center;gap:6px;padding:10px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;text-align:center;'>"
                + "<button type='button' class='btn-sm' onclick='playPreview(" + JSON.stringify(v.preview_url || "") + ", this)'" + (v.preview_url ? "" : " disabled") + " style='min-width:40px;'>▶</button>"
                + "<div><strong>" + v.label + "</strong>" + (desc ? "<br><span class='note' style='font-size:11px;'>" + desc + "</span>" : "") + "</div>"
                + "</div>";
        });
        html += "</div>";
        box.innerHTML = html;
        var hasPrev = vlPage > 0;
        var hasNext = start + pageSize < list.length;
        var nav = "";
        if (hasPrev) nav += "<a href='javascript:void(0)' onclick='searchVoiceLibrary(" + (vlPage - 1) + ")'>← Prev 6</a>&nbsp;&nbsp;";
        if (hasNext) nav += "<a href='javascript:void(0)' onclick='searchVoiceLibrary(" + (vlPage + 1) + ")'>Next 6 →</a>";
        if (pager) pager.innerHTML = nav;
    } catch (e) { box.innerHTML = "<p class='note'>Could not load voices right now. Please try again.</p>"; }
}

function stopPreview() {
    var audio = document.getElementById("vlPreviewAudio");
    if (audio && !audio.paused) audio.pause();
    if (vlCurrentPreviewBtn) { vlCurrentPreviewBtn.textContent = "▶"; vlCurrentPreviewBtn = null; }
}

var vlCurrentPreviewBtn = null;
function playPreview(url, btn) {
    if (!url) return;
    var audio = document.getElementById("vlPreviewAudio");
    if (!audio) {
        audio = document.createElement("audio");
        audio.id = "vlPreviewAudio";
        audio.style.display = "none";
        document.body.appendChild(audio);
        audio.addEventListener("ended", function () { if (vlCurrentPreviewBtn) vlCurrentPreviewBtn.textContent = "▶"; vlCurrentPreviewBtn = null; });
    }
    if (audio.src === url && !audio.paused) { stopPreview(); return; }
    if (vlCurrentPreviewBtn && vlCurrentPreviewBtn !== btn) vlCurrentPreviewBtn.textContent = "▶";
    audio.src = url;
    audio.play();
    btn.textContent = "⏸";
    vlCurrentPreviewBtn = btn;
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
            var engLen = (seg.text || "").length;
            var width = Math.max(8, (engLen / ENGLISH_CHARS_PER_SEC) * scale);
            box.style.cssText = "position:absolute;left:" + left + "px;top:4px;width:" + width + "px;height:26px;background:#42a5f5;border-radius:4px;cursor:grab;color:#fff;font-size:10px;line-height:26px;text-align:center;overflow:hidden;white-space:nowrap;";
            box.title = "Line " + (i + 1) + ": drag to shift (" + engLen + " English characters)";
            box.textContent = (i + 1) + (off ? " (" + (off > 0 ? "+" : "") + Math.round(off * 1000) + "ms)" : "");
            box.onmousedown = function(ev) {
                ev.preventDefault();
                var startX = ev.clientX;
                var startOff = off;
                // Collision must be checked against this block's actual VISUAL
                // width (time-equivalent), not seg.end - seg.start -- since the
                // block is now sized by English text length, its rendered width
                // no longer matches its timespan. Checking against the timespan
                // (as this used to) let a wide block's blue box visually run into
                // the next block even while "respecting" a collision limit that
                // no longer matched what was on screen.
                var widthTime = width / scale;
                var sameLane = segmentsData.filter(function(s, idx) { return idx !== i && (s.speaker || "Speaker 1") === spk && (s.arabic_text || "").trim(); });
                var move = function(e2) {
                    var no = startOff + (e2.clientX - startX) / scale;
                    no = Math.max(-2, Math.min(2, no));
                    if (seg.start + no < 0) no = -seg.start;
                    if (seg.start + no + widthTime > total) no = total - widthTime - seg.start;
                    for (var k = 0; k < sameLane.length; k++) {
                        var nb = sameLane[k];
                        var nbOff = segmentOffsets[nb.segment_id] || 0;
                        var nbWidthTime = Math.max(8 / scale, ((nb.text || "").length) / ENGLISH_CHARS_PER_SEC);
                        var nbStart = nb.start + nbOff;
                        var nbEnd = nbStart + nbWidthTime;
                        if (seg.start + no < nbEnd && seg.start + no + widthTime > nbStart) {
                            if (no > startOff) { no = nbStart - widthTime - seg.start; } else { no = nbEnd - seg.start; }
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
                s.tempo_mode = s.tempo_mode || "excellent";
            });
            remapDefaultSpeakerLabels();
            var autoSplitCount = autoSplitAllPauses();

            originalSegments = JSON.parse(JSON.stringify(segmentsData));
            totalDuration = data.full_duration || 0;

            var message = "Transcription complete.";
            if (data.detected_speakers > 0) {
                message += " Detected speakers: " + data.detected_speakers + ".";
            }
            if (autoSplitCount > 0) {
                message += " Auto-split " + autoSplitCount + " line" + (autoSplitCount > 1 ? "s" : "") + " at detected pause" + (autoSplitCount > 1 ? "s" : "") + ".";
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
            if (txt) txt.textContent = "Error — " + friendly(data.error || "Unknown error");
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
        var res = await fetch("/api/progress/generate?t=" + Date.now() + "&job_id=" + encodeURIComponent(currentJobId || ""));
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
                '<audio controls src="/api/download/' + encodeURIComponent(currentJobId || "") + '_final_dubbed.mp3?cache=' + Date.now() + '"></audio>' +
                '<div class="download-buttons"><a href="/api/download/' + encodeURIComponent(currentJobId || "") + '_final_dubbed.mp3?cache=' + Date.now() + '" download="final_dubbed.mp3">⬇️ Download MP3</a></div>';

            if (isVideoUpload) {
                document.getElementById("mergeSection").classList.remove("hidden");
                // Step 7 (lip-sync) stays hidden -- see LIPSYNC_ENABLED in
                // config.py.
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

            if (txt) txt.textContent = "Error — " + friendly(data.error || "Unknown error");
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

    // Reset speaker count/names + badges, and hide Step 1.5 until a new file is chosen
    var sc = document.getElementById("speakerCount"); if (sc) sc.value = "";
    var sn = document.getElementById("speakerNames"); if (sn) sn.value = "";
    var s15 = document.getElementById("step1_5Card"); if (s15) s15.classList.add("hidden");
    if (typeof updateBadges === "function") updateBadges();
}

// When a NEW file is chosen: warn if a project exists, then reset
(function () {
    if (typeof onFileSelected !== "function") return;
    var _origOnFile = onFileSelected;
    onFileSelected = function (input) {
        if (input.files && input.files[0]) {
            if (projectWasLoaded && !workspaceHasMedia) { attachMedia(input); return; }
            if (segmentsData.length &&
                !confirm("Choosing a new file will clear the current project (segments, translations, voices). Continue?")) {
                input.value = "";
                var fl = document.getElementById("fileUploadText");
                if (fl) fl.textContent = "Upload Media";
                return;
            }
            resetWorkspace();
        }
        _origOnFile(input);
    };
})();

// "Dub Another Video" button injected into Step 6 (fixDub() below relocates it
// into the video download row, next to the two download links, once a video
// has actually been merged; for audio-only results it stays at the end here).
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
        var fl = document.getElementById("fileUploadText"); if (fl) fl.textContent = "Upload Media";
        window.scrollTo({ top: 0, behavior: "smooth" });
        notify("info", "Workspace cleared. Upload your next video in Step 1.");
    };
    target.appendChild(btn);
})();

// ===== ADD-ON: download protection, safe reset, loaded-project media guard =====
var workspaceHasMedia = true;
var projectWasLoaded = false;
var resultsExist = false;
var resultsDownloaded = false;

function resetFileLabel() {
    var fl = document.getElementById("fileUploadText");
    if (fl) fl.textContent = "Upload Media";
}

function confirmResetSafe() {
    if (resultsExist && !resultsDownloaded) {
        if (!confirm("⚠️ You generated audio/video for this project.\n\nIt's saved to your Account page for 30 days, but this editing session (segments, translations, voice choices) will be lost if you continue.\n\nDid you download or note everything you need from this session?")) return false;
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
            // If project was loaded, route to attachMedia instead
            if (projectWasLoaded && !workspaceHasMedia) {
                attachMedia(input);
                return;
            }
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
        '<h3 id="buyModalTitle" style="color:#1a237e;margin-bottom:4px;">Buy Credits</h3>' +
        '<p id="buyModalSubtitle" style="font-size:12px;color:#6b7280;margin-bottom:16px;">100 credits = $1.00 · Credits never expire · Secure payment by Stripe</p>' +
        '<div id="buyPacks" style="display:flex;flex-direction:column;gap:10px;"></div>' +
        '<button id="buyCancelBtn" onclick="closeBuyModal()" style="margin-top:16px;width:100%;padding:10px;border-radius:10px;border:1px solid #e5e7eb;background:#f3f4f6;color:#6b7280;font-weight:600;cursor:pointer;font-family:inherit;">Cancel</button>' +
        '</div>';
    document.body.appendChild(modal);
})();

function openBuyModal() {
    var modal = document.getElementById("buyModal");
    var wrap = document.getElementById("buyPacks");
    var isAr = window.currentLang === "ar";
    var creditsWord = isAr ? "رصيد" : "credits";
    wrap.innerHTML = '<p style="font-size:13px;color:#6b7280;">' + (isAr ? "جارٍ تحميل الباقات..." : "Loading packs...") + '</p>';
    modal.style.display = "flex";
    fetch("/api/billing/packs").then(function (r) { return r.json(); }).then(function (data) {
        wrap.innerHTML = "";
        // Loop over whatever pack keys the admin panel actually defines,
        // instead of a fixed ["starter","standard","pro","business"] list —
        // that hardcoded list silently hid any pack with a different key
        // (including a 5th pack) even though it existed on the backend.
        Object.keys(data.packs || {}).forEach(function (key) {
            var p = data.packs[key];
            if (!p) return;
            var b = document.createElement("button");
            b.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-radius:10px;border:2px solid #e5e7eb;background:#fff;cursor:pointer;font-family:inherit;";
            b.innerHTML = '<span style="font-weight:700;color:#1a237e;">' + p.credits.toLocaleString() + ' ' + creditsWord + '</span><span style="font-weight:800;color:#059669;">$' + p.amount_usd.toFixed(2) + '</span>';
            b.onmouseenter = function () { b.style.borderColor = "#42a5f5"; };
            b.onmouseleave = function () { b.style.borderColor = "#e5e7eb"; };
            b.onclick = function () { buyPack(key, b); };
            wrap.appendChild(b);
        });
    }).catch(function () { wrap.innerHTML = '<p style="color:#dc2626;font-size:13px;">' + (isAr ? "تعذّر تحميل الباقات." : "Could not load packs.") + '</p>'; });
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
    guard("startTranscribe", 0, "Not enough credits — transcription costs 3 credits.");
    guard("generateAudio", 20, "Not enough credits — generation costs 1 credit per ~60 characters.");
    guard("mergeVideo", 1, "Not enough credits — merging costs 1 credit.");
    guard("runLipsync", 1, "Not enough credits — lip-sync is billed per second of video.");
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

    // NOTE: this used to force-hide the Lip-Sync card at page load via an
    // inline style (c.style.display = "none"), from back when Step 7 was
    // a disabled/placeholder feature with no working backend yet. That
    // inline style overrides classList-based show/hide regardless of CSS
    // specificity or !important, so once VEED Lip-Sync 2.0 shipped and the
    // real reveal logic started doing lipsyncSection.classList.remove
    // ("hidden") on job completion, this leftover block silently kept
    // re-hiding the card on every page load anyway -- classList no longer
    // had "hidden", but the inline style from here still did. That's the
    // actual cause of "Step 7 never appears even after the job finishes".
    // Removed now that lip-sync is a real, working feature.

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
        '<p class="note" id="volumeMatchNote">Every Arabic line was automatically loudness-matched to the original speaker\'s voice (see <strong>Auto</strong> column). Play 🔊 a dubbed line, fine-tune it with the slider (−6…+6 dB, live preview), then apply to rebuild the final MP3. Re-running Generate resets trims to auto.</p>' +
        '<div class="table-wrap"><table id="volumeTable"><thead><tr><th>#</th><th>Speaker</th><th>Line</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style="min-width:130px">Volume</th><th></th></tr></thead><tbody></tbody></table></div>' +
        '<button id="applyVolumesBtn" class="green">🔊 Apply changes & rebuild MP3<span class="badge" id="badgeApplyVolumes"></span></button> ' +
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
    if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
        if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        btn.textContent = "🔊 Apply changes & rebuild MP3";
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
            var r = await fetch("/api/progress/generate?t=" + Date.now() + "&job_id=" + encodeURIComponent(currentJobId || ""));
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
                if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
            if (au) { au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
        }).catch(function (e) { notify("error", e.message); }).finally(function () {
            btn.disabled = false; btn.textContent = "🔊 Apply changes & rebuild MP3";
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
        if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
    // Usage button removed from the user bar -- clicking the user's own
    // name (#userName, now an <a href="/account"> in index.html) opens the
    // Account page instead. See the "Help & Usage open in NEW tabs" click
    // delegation further down, which already opens href="/account" links
    // in a new tab and keeps working unchanged for this link too.
    // Custom voice upload box in Step 4
    var sv = document.getElementById("speakerVoicesSection");
    if (sv && !document.getElementById("customVoiceBox")) {
        var box = document.createElement("div");
        box.id = "customVoiceBox";
        box.className = "note";
        box.style.marginTop = "12px";
        box.innerHTML = '<span id="customVoiceNote"><strong>📤 Use your own voice clip:</strong> pick a speaker and upload an MP3/WAV clip (max 20 s) where that person speaks most of the time. The clip is not analyzed — the voice engine extracts the dominant voice, so music or other voices in it will reduce quality.</span><br>' +
            '<select id="cvSpeaker" style="width:auto;min-width:140px;margin:8px 6px 0 0;"></select>' +
            '<input type="file" id="cvFile" accept=".mp3,.wav,audio/mpeg,audio/wav" style="display:none;"><label for="cvFile" id="cvFileLabel" class="file-upload-area" style="margin-top:8px;margin-right:14px;cursor:pointer;">Choose File</label>' +
            '<button class="purple" id="cvUpload" style="margin-top:8px;">Upload as this speaker\'s voice<span class="badge" id="badgeCvUpload"></span></button>' +
            '<span id="cvStatus" style="margin-left:10px;font-size:12px;color:#6b7280;"></span>';
        sv.appendChild(box);
        document.getElementById("cvUpload").onclick = window.uploadCustomVoice;
        if (typeof updateBadges === "function") updateBadges();
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
        if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
            if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
            var cuts = (data.duration_cuts || 0);
            notify("success", "Final MP3 rebuilt" + (cuts > 0 ? " — " + cuts + " line(s) still trimmed." : " — no lines were trimmed."));
            // Refresh the Step 5.5 table's needs-attention marks from this rebuild's
            // real result -- a line's mark clears itself the instant it's no longer
            // in trimmed_segment_ids (e.g. the user allowed dead space / overlap, or
            // loosened Time Stretch, and this rebuild fixed it).
            if (Array.isArray(data.trimmed_segment_ids)) {
                var _trimmedNow3 = {};
                data.trimmed_segment_ids.forEach(function (sid) { _trimmedNow3[sid] = true; });
                (window._volumeLines || []).forEach(function (ln) { ln.trimmed = !!_trimmedNow3[ln.segment_id]; });
                if (typeof window.buildVolumeTable === "function") window.buildVolumeTable(window._volumeLines || []);
            }
            var au = document.querySelector("#audioResults audio");
            if (au) { au.pause(); au.src = "/api/download/" + encodeURIComponent(currentJobId || "") + "_final_dubbed.mp3?cache=" + Date.now(); au.load(); }
            btn.textContent = "🔊 Apply changes & rebuild MP3";
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
    fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: keep, job_id: currentJobId }) })
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
            if (keep.length) fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: keep, job_id: currentJobId }) }).catch(function () {});
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
        // The 17 pairs below were previously written WITHOUT the emoji that
        // actually prefixes each button/link in the live DOM. Matching for
        // buttons/links/labels/headers requires the text to *start with* the
        // English key (see tagAll()'s `indexOf(...) === 0` check below), so
        // any mismatched emoji silently broke translation for all of these --
        // they just never got tagged at all. Keeping the emoji on both sides
        // of each pair also keeps the button looking the same after the
        // switch, the same way the pre-existing "➕ Buy" pair already did.
        ["🔤 Add Tashkeel Only", "🔤 إضافة التشكيل فقط"],
        ["Detect Emotions from Voice", "كشف المشاعر من الصوت"],
        ["✨ Auto-Fix Timing", "✨ إصلاح التوقيت تلقائيًا"],
        ["📥 Import SRT/SBV", "📥 استيراد SRT/SBV"],
        ["💾 Save Project", "💾 حفظ المشروع"],
        ["📂 Load Project", "📂 تحميل المشروع"],
        ["Prepare Voice Cloning", "تحضير استنساخ الصوت"],
        ["Clone Selected Voices", "استنساخ الأصوات المحددة"],
        ["🎲 Auto-Assign All", "🎲 تعيين تلقائي للكل"],
        ["Generate Arabic Audio", "توليد الصوت العربي"],
        ["🎬 Merge Audio into Video", "🎬 دمج الصوت في الفيديو"],
        ["✅ Confirm Changes & Rebuild MP3", "✅ تأكيد التغييرات وإعادة بناء MP3"],
        ["↩️ Reset Offsets", "↩️ إصفار الإزاحات"],
        ["🎚️ Fine-Tune Timeline", "🎚️ ضبط الخط الزمني"],
        ["🆕 Dub Another Video", "🆕 دبلجة فيديو آخر"],
        ["Log Out", "تسجيل الخروج"],
        ["⬇️ Download MP3", "⬇️ تنزيل MP3"],
        ["⬇️ Download Dubbed Video (MP4)", "⬇️ تنزيل الفيديو المدبلج (MP4)"],
        ["⬇️ Download Pure Vocals (MP3)", "⬇️ تنزيل الصوت فقط (MP3)"],
        ["🔊 Apply changes & rebuild MP3", "🔊 تطبيق التغييرات وإعادة بناء MP3"],
        ["↺ Reset All Sliders", "↺ إعادة تعيين كل المنزلقات"],
        ["Upload as this speaker's voice", "رفعه كصوت لهذا المتحدث"],
        ["🧹 Clean old cloned voices", "🧹 تنظيف الأصوات المستنسخة القديمة"],
        ["➕ Buy", "➕ شراء"],
        // Second pass: Step 1.5 header, a batch of buttons/labels that had no
        // entry at all yet, and the Contact Us modal's controls.
        ["Step 1.5: Speaker Setup", "الخطوة 1.5: إعداد المتحدثين"],
        ["Choose Media File", "اختيار ملف الوسائط"],
        ["📤 SRT", "📤 تصدير SRT"],
        ["📤 SBV", "📤 تصدير SBV"],
        ["🔄 Load Voice Options", "🔄 تحميل خيارات الأصوات"],
        ["🌐 Browse Voice Library", "🌐 استعراض مكتبة الأصوات"],
        ["Number of speakers", "عدد المتحدثين"],
        ["Speaker names (comma separated, optional)", "أسماء المتحدثين (مفصولة بفواصل، اختياري)"],
        ["Final Duration Mode", "نمط المدة النهائية"],
        ["Choose File", "اختيار ملف"],
        ["⬇ Download voice sample", "⬇ تنزيل نموذج الصوت"],
        ["Contact Us", "اتصل بنا"],
        ["Name (optional)", "الاسم (اختياري)"],
        ["Email", "البريد الإلكتروني"],
        ["Message", "الرسالة"],
        ["Send Message", "إرسال الرسالة"],
        // Step 7: Lip-Sync (VEED Lip Sync 2.0 via fal.ai)
        ["Step 7: Lip-Sync (Premium)", "الخطوة 7: مزامنة الشفاه (مميزة)"],
        ["🎭 Lip-Sync Video", "🎭 مزامنة الشفاه"]
    ];
    // Any note/paragraph whose text is broken up by inline tags (<strong>,
    // <br>, <a>) is handled here via a full innerHTML swap, not through the
    // R-array fragment-replace mechanism below -- el.firstChild for such an
    // element is only the small leading text node before the first inline
    // tag, so an R-array .replace() can only ever touch that leading
    // fragment and can never reach text that comes after an inline tag.
    // That structural limit existed before any of these entries were added;
    // it isn't something a later wording edit broke.
    var BANNERS = {
        tempFileWarning: {
            en: '⚠️ <strong>Important:</strong> Your uploaded source file and any in-progress editing are temporary and are lost when the session ends or the server restarts. Once you generate a result, it\'s saved to your <a href="/account" style="color:#92400e;">Account</a> page for 30 days — download it any time from there.',
            ar: '⚠️ <strong>مهم:</strong> ملفك المصدر المرفوع وأي تحرير جارٍ مؤقتان ويُفقدان عند انتهاء الجلسة أو إعادة تشغيل الخادم. بعد توليد النتيجة، تُحفظ في صفحة <a href="/account" style="color:#92400e;">حسابك</a> لمدة 30 يومًا — نزّلها في أي وقت من هناك.'
        },
        resultSavedBanner: {
            en: '✅ This result is saved to your <a href="/account" style="color:#92400e;">Account</a> page for 30 days. Your uploaded source file is still temporary — download or keep editing before you close this session.',
            ar: '✅ تم حفظ هذه النتيجة في صفحة <a href="/account" style="color:#92400e;">حسابك</a> لمدة 30 يومًا. ملفك المصدر المرفوع لا يزال مؤقتًا — نزّله أو استمر في التحرير قبل إغلاق هذه الجلسة.'
        },
        step1SupportsNote: {
            en: 'Supports: MP3, WAV, MP4, AVI, MKV, MOV, WEBM.<br>Limits: <strong>60 seconds</strong> max duration, <strong>400 MB</strong> max file size.',
            ar: 'يدعم: MP3, WAV, MP4, AVI, MKV, MOV, WEBM.<br>الحدود: <strong>60 ثانية</strong> كحد أقصى للمدة، <strong>400 ميجابايت</strong> كحد أقصى لحجم الملف.'
        },
        step1CreditsNote: {
            en: '💡 Credits are our internal unit: <strong>100 credits = $1.00</strong> (1 credit = $0.01).<br>A typical full dub costs only a few credits.',
            ar: '💡 الائتمانات وحدتنا الداخلية: <strong>100 ائتمان = 1.00 دولار</strong> (الائتمان الواحد = 0.01 دولار).<br>الدبلجة الكاملة النموذجية تكلف بضعة ائتمانات فقط.'
        },
        attachMediaNote: {
            en: '📼 <strong>Project loaded.</strong> Upload the matching original audio/video file to enable preview, re-speak, emotion detection, auto-fix, and video merge.',
            ar: '📼 <strong>تم تحميل المشروع.</strong> ارفع ملف الصوت أو الفيديو الأصلي المطابق لتفعيل المعاينة، وإعادة النطق، وكشف المشاعر، والإصلاح التلقائي، ودمج الفيديو.'
        },
        volumeMatchNote: {
            en: 'Every Arabic line was automatically loudness-matched to the original speaker\'s voice (see <strong>Auto</strong> column). Play 🔊 a dubbed line, fine-tune it with the slider (−6…+6 dB, live preview), then apply to rebuild the final MP3. Re-running Generate resets trims to auto.',
            ar: 'تمت مطابقة مستوى كل سطر عربي تلقائيًا مع صوت المتحدث الأصلي (انظر عمود <strong>Auto</strong>). شغّل 🔊 السطر المدبلج، واضبطه بالمنزلق (−6...+6 ديسيبل مع معاينة مباشرة)، ثم طبّق لإعادة بناء ملف MP3 النهائي. تكرار توليد الصوت يعيد الإزاحات إلى القيم التلقائية.'
        },
        customVoiceNote: {
            // This note lives inside the same box as a <select>, <input> and
            // <button> that JS populates/binds separately (speaker dropdown,
            // file picker, upload button). Translating the whole box via
            // innerHTML would wipe the select's options and detach the
            // button's click handler on every language toggle, so only this
            // wrapped span is swapped -- the interactive controls next to it
            // are never touched.
            en: '<strong>📤 Use your own voice clip:</strong> pick a speaker and upload an MP3/WAV clip (max 20 s) where that person speaks most of the time. The clip is not analyzed — the voice engine extracts the dominant voice, so music or other voices in it will reduce quality.',
            ar: '<strong>📤 استخدم مقطع صوتك الخاص:</strong> اختر متحدثًا وارفع مقطع MP3/WAV (بحد أقصى 20 ثانية) يتحدث فيه ذلك الشخص معظم الوقت. المقطع لا يُحلَّل — محرك الصوت يستخرج منه الصوت الغالب، لذا فإن وجود موسيقى أو أصوات أخرى فيه سيقلل الجودة.'
        },
        lipsyncNote: {
            // Contains an inline <strong id="lipsyncRateNote"> that
            // updateBadges() overwrites with the real per-second rate --
            // going through BANNERS (full innerHTML swap) rather than the
            // R-array (which only ever touches a leading text node) so the
            // embedded tag survives the swap; both language variants keep
            // the exact same id inside so getElementById keeps finding it.
            en: 'Matches the mouth movements in your video to the new Arabic audio, using a premium third-party AI service. This re-processes your final dubbed video and costs <strong id="lipsyncRateNote">10</strong> credits per second of video.',
            ar: 'تُطابق حركة الشفاه في فيديوك مع الصوت العربي الجديد، باستخدام خدمة ذكاء اصطناعي مدفوعة من جهة خارجية. تُعيد هذه الخطوة معالجة فيديوك المدبلج النهائي بالكامل وتكلّف <strong id="lipsyncRateNote">10</strong> رصيد لكل ثانية من الفيديو.'
        }
    };
    // Plain textContent swaps for elements tagAll() never reaches: table
    // <th> headers (th is deliberately excluded from tagAll's selector --
    // see the note above it) and <option> labels (<select> children aren't
    // scanned either). Keyed by id, applied the same way BANNERS is below.
    var TABLE_TXT = {
        thNum: { en: "#", ar: "#" },
        thStart: { en: "Start", ar: "البداية" },
        thEnd: { en: "End", ar: "النهاية" },
        thSpeaker: { en: "Speaker", ar: "المتحدث" },
        thGender: { en: "Gender", ar: "الجنس" },
        thStyle: { en: "Style / Emotion", ar: "الأسلوب / المشاعر" },
        thEnglish: { en: "English", ar: "الإنجليزية" },
        thArabic: { en: "Arabic", ar: "العربية" },
        thActions: { en: "Actions", ar: "الإجراءات" },
        thCloneQ: { en: "Clone?", ar: "استنساخ؟" },
        thCloneSpeaker: { en: "Speaker", ar: "المتحدث" },
        thSpeechFound: { en: "Speech Found", ar: "الكلام المتوفر" },
        thQualityGuidance: { en: "Quality Guidance", ar: "إرشادات الجودة" },
        thVoiceTableSpeaker: { en: "Speaker", ar: "المتحدث" },
        thVoiceTableVoice: { en: "Voice", ar: "الصوت" },
        optExactDuration: { en: "Exact input duration", ar: "مدة الإدخال بالضبط" },
        optExtendDuration: { en: "Extend duration", ar: "تمديد المدة" },
        creditsWord: { en: "credits", ar: "رصيد" },
        // "➕ Buy" credits modal (excluded from tagAll() via #buyModal, so it
        // needs its own id-keyed entries here instead).
        buyModalTitle: { en: "Buy Credits", ar: "شراء الرصيد" },
        buyModalSubtitle: {
            en: "100 credits = $1.00 · Credits never expire · Secure payment by Stripe",
            ar: "100 رصيد = 1.00 دولار · الرصيد لا تنتهي صلاحيته · دفع آمن عبر Stripe"
        },
        buyCancelBtn: { en: "Cancel", ar: "إلغاء" }
    };
    // R-array keys below are now the FULL exact text of each plain (no
    // inline-tag) note, not a short prefix. Earlier, several keys were only
    // the first few words of a longer sentence (e.g. "Pick a voice for each
    // speaker" as the key for a full multi-clause note) -- .replace() only
    // swaps that matched fragment, so the rest of the original English
    // sentence was left in place right after the Arabic text, showing both
    // languages at once. Matching the full sentence makes the swap total.
    var R = [
        ["Tell us how many speakers are in the file, and optionally their names. This fills the Speaker dropdown in Step 2.", "أخبرنا بعدد المتحدثين في الملف، وأسمائهم اختياريًا. هذا يملأ قائمة المتحدثين في الخطوة 2."],
        ['If you give fewer names than the number of speakers, the rest are labeled "Speaker 3", "Speaker 4", etc.', "إذا أعطيت أسماءً أقل من عدد المتحدثين، يُسمّى الباقون “متحدث 3”، “متحدث 4”، وهكذا."],
        ["Cloning copies each speaker's own voice from the video. Optional — you can also pick studio library voices in Step 4.", "ينسخ الاستنساخ صوت كل متحدث من الفيديو. اختياري — يمكنك اختيار أصوات المكتبة في الخطوة 4."],
        ["Review the available audio for each speaker. Uncheck any speaker you want to skip.", "راجع الصوت المتوفر لكل متحدث. أزل التحديد عن أي متحدث تريد تخطي استنساخه."],
        ["Pick a voice for each speaker. Cloned voices come from your video; numbered voices are high-quality studio library voices (names hidden on purpose). Two speakers never share the same numbered voice.", "اختر صوتًا لكل متحدث. الأصوات المستنسخة من الفيديو؛ والأصوات المرقمة من مكتبة الاستوديو، وأسماؤها مخفية عمدًا. لا يشترك متحدثان في نفس الصوت المرقّم."],
        ["Listen to ready-made Arabic voices below. To use one, select it from the dropdown in the table.", "استمع إلى أصوات عربية جاهزة أدناه. لاستخدام أحدها، اخترْه من القائمة المنسدلة في الجدول."],
        ["👉 Select a voice for each speaker from the dropdown below.", "👉 اختر صوتًا لكل متحدث من القائمة المنسدلة أدناه."],
        ["💡 Voice generation supports emotions and cloned voices. Costs are charged by the character and shown in credits (100 credits = $1).", "💡 يدعم توليد الصوت المشاعر والأصوات المستنسخة. تُحتسب التكلفة بالأحرف وتُعرض بالائتمانات (100 ائتمان = 1 دولار)."],
        ["Drag each block left/right to align it with the lip movement. Blocks stop at adjacent segments to prevent overlap. Then confirm to rebuild the MP3.", "اسحب كل كتلة يسارًا/يمينًا لمطابقة حركة الشفاه. تتوقف الكتل عند المقاطع المجاورة لمنع التداخل، ثم أكّد لإعادة بناء MP3."],
        ["Combines the dubbed Arabic audio with the original background music and video.", "يدمج الصوت العربي المدبلج مع موسيقى الخلفية الأصلية والفيديو."],
        ["Questions, feedback, or need help? Send us a message and we'll get back to you by email.", "أسئلة أو ملاحظات أو تحتاج مساعدة؟ أرسل لنا رسالة وسنرد عليك بالبريد الإلكتروني."],
        ["Applies DSP filters: removes rumble + adds crispness with high-shelf boost. Instant processing, no ML artifacts.", "يطبّق مرشحات معالجة رقمية: يزيل الضجيج المنخفض ويضيف وضوحًا بتعزيز الترددات العالية. معالجة فورية دون تشويش ناتج عن الذكاء الاصطناعي."],
        ["✨ Enhance Background Audio", "✨ تحسين جودة الصوت الخلفي"],
        ["Show Arabic audio length overlay", "إظهار طبقة مدة الصوت العربي"],
        // "Upload Media" is a <span>, which tagAll() always matches against R
        // (only H3/BUTTON/A/LABEL use P) -- it was mistakenly added to P in
        // an earlier pass, so it was never actually being looked up and never
        // translated. Belongs here instead.
        ["Upload Media", "ارفع الوسائط"],
        // Third pass: the one-line subtitles injected under each step
        // heading (the SUBS object further down), and the Step 6 timeline
        // legend (three short labels next to color swatches -- each is the
        // *entire* text of its <span>, so a plain R-array match is safe here
        // even though the swatch markup comes before the label in the DOM).
        ["Fix timings, edit text, translate, and protect lines with 🔒.", "أصلح التوقيت، حرّر النص، ترجم، واحمِ الأسطر بـ 🔒."],
        ["Optional: clone each speaker's own voice from the video.", "اختياري: استنسخ صوت كل متحدث من الفيديو."],
        ["See how much clean speech each speaker has before cloning.", "اطّلع على مقدار الكلام الواضح المتوفر لكل متحدث قبل الاستنساخ."],
        ["Assign a cloned or studio voice to every speaker.", "عيّن صوتًا مستنسخًا أو من الاستوديو لكل متحدث."],
        ["Generate the final Arabic audio with emotions and exact timing.", "ولّد الصوت العربي النهائي بالمشاعر والتوقيت الدقيق."],
        ["kept", "محتفظ به"],
        ["faded / trimmed", "تلاشٍ / تقليم"],
        ["overlap allowed", "يُسمح بالتداخل"],
        ["Also generate a lip-synced video", "أنشئ أيضًا فيديو بمزامنة الشفاه"],
        ["I hereby certify that I have all necessary rights or consents to upload and translate this audio/video, which could result in the cloning of the associated voices.", "أقرّ بأنني أملك جميع الحقوق أو الموافقات اللازمة لرفع هذا الملف الصوتي أو المرئي وترجمته، وهو ما قد يؤدي إلى استنساخ الأصوات المرتبطة به."]
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
        // Suffixes for the three balance-guard messages above -- each one
        // chains after the "Not enough credits" prefix match fires first,
        // so both halves end up translated in sequence.
        [" — transcription costs 3 credits.", " — تكلفة النسخ 3 أرصدة."],
        [" — generation costs 1 credit per ~60 characters.", " — تكلفة التوليد رصيد واحد لكل ~60 حرفًا."],
        [" — merging costs 1 credit.", " — تكلفة الدمج رصيد واحد."],
        [" — lip-sync is billed per second of video.", " — تُحتسب مزامنة الشفاه لكل ثانية من الفيديو."],
        ["Upload failed:", "فشل الرفع:"],
        ["Rebuild failed:", "فشل إعادة البناء:"],
        // The specific "X failed: " entries below must stay ABOVE the
        // generic "failed:" catch-all right after them -- this array is
        // matched top-to-bottom and each match mutates the string in place,
        // so if the generic entry fired first it would translate just
        // "failed:" and leave the rest of the message (the "X" part) in
        // English, out of order (e.g. "Tashkeel فشل:" instead of "فشل
        // التشكيل:"). Discovered when a simulation of the exact runtime
        // algorithm against real message text caught it before it shipped.
        ["Tashkeel failed: ", "فشل التشكيل: "],
        ["Preview failed: ", "فشلت المعاينة: "],
        ["Remix failed: ", "فشل إعادة المزج: "],
        ["Time Stretch update failed: ", "فشل تحديث تمديد الوقت: "],
        ["Transcribe failed: ", "فشل التفريغ: "],
        ["Regenerate failed: ", "فشلت إعادة التوليد: "],
        ["Credit sync failed: ", "فشلت مزامنة الرصيد: "],
        ["Lip-sync failed to start: ", "فشل بدء مزامنة الشفاه: "],
        ["Lip-sync failed: ", "فشلت مزامنة الشفاه: "],
        ["failed:", "فشل:"],
        ["Workspace cleared.", "تم مسح مساحة العمل."],
        ["Uploading and starting transcription...", "جارٍ الرفع وبدء التفريغة..."],
        // ===== Batch 4: remaining untranslated notify() messages =====
        // Built and verified by simulating this exact array + the runtime's
        // sequential indexOf/replace algorithm against real message text
        // (with sample values standing in for variables) before insertion,
        // specifically to catch ordering collisions like the one above.
        // Long-before-short pairs come first where one key is a substring
        // of another (otherwise the short one would partially consume the
        // long one's match before the long entry gets its turn).
        ["Could not load the voice library. Check the server configuration.", "تعذّر تحميل مكتبة الأصوات. تحقق من إعدادات الخادم."],
        ["Could not load the voice library.", "تعذّر تحميل مكتبة الأصوات."],
        ["Voice library unavailable. Check the server configuration.", "مكتبة الأصوات غير متاحة. تحقق من إعدادات الخادم."],
        ["Voice library unavailable.", "مكتبة الأصوات غير متاحة."],
        ["Enter the Translation AI key in Step 2 first.", "أدخل مفتاح الذكاء الاصطناعي للترجمة في الخطوة 2 أولاً."],
        ["No unlocked Arabic text found. Locked lines are skipped.", "لم يُعثر على نص عربي غير مقفل. الأسطر المقفلة تُستثنى."],
        ["Transcribe or load a project first.", "فرّغ الملف صوتيًا أو حمّل مشروعًا أولاً."],
        ["Preview unavailable: source audio not found.", "المعاينة غير متاحة: الصوت المصدر غير موجود."],
        ["No cues found in subtitle file.", "لم يُعثر على أسطر في ملف الترجمة."],
        ["Nothing to export.", "لا يوجد شيء للتصدير."],
        ["Media attached successfully. All functions are now enabled.", "أُرفقت الوسائط بنجاح. جميع الوظائف مفعّلة الآن."],
        ["Choose an audio or video file first.", "اختر ملف صوت أو فيديو أولاً."],
        ["Choose an audio or video file.", "اختر ملف صوت أو فيديو."],
        ["Line locked: Auto-Fix, Translate and Tashkeel will skip it.", "السطر مقفل: سيتخطاه الإصلاح التلقائي والترجمة والتشكيل."],
        ["Line unlocked.", "السطر غير مقفل."],
        ["Manual line inserted. Use ✨ Auto-Fix to sync its time.", "أُدرج سطر يدوي. استخدم ✨ الإصلاح التلقائي لمزامنة توقيته."],
        ["Could not load voices. Enter the Voice Engine API key in Step 3 first.", "تعذّر تحميل الأصوات. أدخل مفتاح محرك الصوت في الخطوة 3 أولاً."],
        ["Enter the Voice Engine API key in Step 3 first, then try again.", "أدخل مفتاح محرك الصوت في الخطوة 3 أولاً، ثم أعد المحاولة."],
        ["Enter the Voice Engine API key in Step 3.", "أدخل مفتاح محرك الصوت في الخطوة 3."],
        ["No segments to fix.", "لا توجد مقاطع لإصلاحها."],
        ["No original transcription available.", "لا يوجد تفريغ أصلي متاح."],
        ["Original transcription has no words to match.", "التفريغ الأصلي لا يحتوي كلمات للمطابقة."],
        ["No segments found.", "لم يُعثر على مقاطع."],
        ["Review the guidance below. Speakers marked ❌ are unchecked automatically.", "راجع الإرشادات أدناه. المتحدثون المعلّمون بـ ❌ يُلغى تحديدهم تلقائيًا."],
        ["Review the guidance. Speakers marked ❌ are unchecked automatically.", "راجع الإرشادات. المتحدثون المعلّمون بـ ❌ يُلغى تحديدهم تلقائيًا."],
        ["Transcribe first.", "فرّغ الملف صوتيًا أولاً."],
        ["Select at least one speaker to clone.", "اختر متحدثًا واحدًا على الأقل للاستنساخ."],
        ["All lines are locked — nothing to translate.", "جميع الأسطر مقفلة — لا شيء للترجمة."],
        ["Starting emotion detection...", "جارٍ بدء كشف المشاعر..."],
        ["Fill at least one Arabic translation.", "املأ ترجمة عربية واحدة على الأقل."],
        ["Starting Arabic audio generation...", "جارٍ بدء توليد الصوت العربي..."],
        ["No job found.", "لم يُعثر على مهمة."],
        ["No job.", "لا توجد مهمة."],
        ["Merging dubbed audio with video and background music...", "جارٍ دمج الصوت المدبلج مع الفيديو وموسيقى الخلفية..."],
        ["Starting lip-sync — this re-processes the full video and can take a few minutes...", "جارٍ بدء مزامنة الشفاه — تُعاد معالجة الفيديو الكامل وقد يستغرق ذلك بضع دقائق..."],
        ["Lip-sync complete.", "اكتملت مزامنة الشفاه."],
        ["This line has no Arabic text yet.", "هذا السطر لا يحتوي نصًا عربيًا بعد."],
        ["Offsets reset.", "أُعيدت الإزاحات."],
        ["No offsets to apply — drag some blocks first.", "لا توجد إزاحات لتطبيقها — اسحب بعض الكتل أولاً."],
        ["Rebuilding final audio with your offsets...", "جارٍ إعادة بناء الصوت النهائي بإزاحاتك..."],
        ["Choose an MP3 or WAV clip first.", "اختر مقطع MP3 أو WAV أولاً."],
        ["Only MP3 or WAV files are allowed.", "يُسمح فقط بملفات MP3 أو WAV."],
        ["Could not read that audio file.", "تعذّر قراءة ملف الصوت هذا."],
        ["Custom voice box not ready — refresh the page.", "صندوق الصوت المخصص غير جاهز — أعد تحميل الصفحة."],
        ["Custom voice box not ready - refresh the page.", "صندوق الصوت المخصص غير جاهز - أعد تحميل الصفحة."],
        ["Server error — is the server redeployed?", "خطأ في الخادم — هل أُعيد نشر الخادم؟"],
        ["Server error.", "خطأ في الخادم."],
        ["Custom voice created. The cloned voice stays in the dropdown.", "أُنشئ الصوت المخصص. يبقى الصوت المستنسخ في القائمة المنسدلة."],
        ["All lines unlocked.", "جميع الأسطر غير مقفلة."],
        ["All lines locked.", "جميع الأسطر مقفلة."],
        ["Restored your session. Reconnecting to your last job on the server...", "استُعيدت جلستك. جارٍ إعادة الاتصال بآخر مهمة على الخادم..."],
        ["Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.", "استُعيدت جلستك السابقة من هذا المتصفح. أعد رفع الملف الأصلي لتفعيل المعاينة/الاستنساخ/الدمج."],
        ["Your last media is no longer on the server (it expires after ~6 hours or a restart). Re-upload the original file to continue.", "وسائطك الأخيرة لم تعد موجودة على الخادم (تنتهي صلاحيتها بعد ~6 ساعات أو عند إعادة التشغيل). أعد رفع الملف الأصلي للمتابعة."],
        ["📼 Loaded projects have no media on the server. Preview, re-speak, emotions, auto-fix and merge need a fresh upload. Editing, translate, tashkeel, SRT export and Generate still work.", "📼 المشاريع المحمّلة لا تحتوي وسائط على الخادم. المعاينة وإعادة النطق وكشف المشاعر والإصلاح التلقائي والدمج تحتاج رفعًا جديدًا. التحرير والترجمة والتشكيل وتصدير SRT والتوليد تعمل كالمعتاد."],
        ["🎉 Payment complete! Your credits have been added.", "🎉 اكتمل الدفع! أُضيف رصيدك."],
        ["📼 Voice cloning needs the original audio on the server, and loaded projects have none. Either re-upload the same video in Step 1, or skip cloning and pick studio library voices in Step 4.", "📼 يحتاج استنساخ الصوت إلى الصوت الأصلي على الخادم، والمشاريع المحمّلة لا تحتوي عليه. إمّا أعد رفع نفس الفيديو في الخطوة 1، أو تخطَّ الاستنساخ واختر أصواتًا من مكتبة الاستوديو في الخطوة 4."],
        ["⏳ Audio generation is still running. Wait for it to finish before starting a new video — switching now could mix the two audios.", "⏳ توليد الصوت لا يزال قيد التشغيل. انتظر حتى ينتهي قبل بدء فيديو جديد — التبديل الآن قد يخلط الصوتين."],
        ["Sync found no paid Stripe sessions for this account.", "لم يُعثر على جلسات Stripe مدفوعة لهذا الحساب."],
        ["Transcription failed.", "فشل التفريغ."],
        ["Audio generation failed.", "فشل توليد الصوت."],
        ["Preview playback failed: ", "فشل تشغيل المعاينة: "],
        ["Adding tashkeel to ", "جارٍ إضافة التشكيل إلى "],
        ["Tashkeel added to ", "أُضيف التشكيل إلى "],
        ["🎙️ The cloned voice(s) for ", "🎙️ الصوت (الأصوات) المستنسخة لـ "],
        ["Failed to attach media: ", "فشل إرفاق الوسائط: "],
        ["Load failed: ", "فشل التحميل: "],
        ["Auto-Fix: ", "الإصلاح التلقائي: "],
        ["Subtitle import: ", "استيراد الترجمة: "],
        ["Cloning ", "جارٍ استنساخ "],
        ["Re-speaking line ", "جارٍ إعادة نطق السطر "],
        ["New mix built with ", "بُني مزيج جديد بـ "],
        ["Translating ", "جارٍ الترجمة "],
        ["No voice for: ", "لا يوجد صوت لـ: "],
        ["No voice for ", "لا يوجد صوت لـ "],
        ["This clip is ", "مدة هذا المقطع تبلغ "],
        ["File too large (", "الملف كبير جدًا ("],
        ["Words not in the official list were removed. Style: '", "أُزيلت كلمات غير موجودة في القائمة الرسمية. النمط: '"],
        ["Clip is ", "مدة المقطع تبلغ "],
        ["Final MP3 rebuilt", "أُعيد بناء MP3 النهائي"],
        ["Cleanup endpoint not found (status ", "نقطة تنظيف الصوت غير موجودة (الحالة "],
        ["Credit sync error: ", "خطأ في مزامنة الرصيد: "],
        ["Credit fulfillment: ", "تنفيذ الرصيد: "],
        ["Preview plays matched audio ", "تعرض المعاينة الصوت المطابق "],
        ["(row shows ", "(الصف يعرض "],
        [" (balance: ", " (الرصيد: "],
        [" Cost: ", " التكلفة: "],
        [" credits.", " رصيد."],
        [" speaker(s) assigned to their cloned voices.", " متحدثًا تم تعيين صوته المستنسخ."],
        [" segments translated. Locked lines untouched.", " مقطعًا مُترجَمًا. الأسطر المقفلة لم تُمس."],
        [" segments updated.", " مقطعًا مُحدَّثًا."],
        [" You can change any speaker's voice in the Step 4 table.", " يمكنك تغيير صوت أي متحدث من جدول الخطوة 4."],
        [" Change any speaker's voice in the Step 4 table.", " غيّر صوت أي متحدث من جدول الخطوة 4."],
        [" Pick a voice per speaker below.", " اختر صوتًا لكل متحدث أدناه."],
        [" — final MP3 rebuilt with your per-line trim.", " — أُعيد بناء ملف MP3 النهائي بضبطك لكل سطر."],
        [" — final MP3 rebuilt with your mix.", " — أُعيد بناء ملف MP3 النهائي بمزيجك."],
        [" to auto-matched values. Press Apply to rebuild.", " إلى القيم المطابقة تلقائيًا. اضغط تطبيق لإعادة البناء."],
        [" to the auto-matched volumes.", " إلى مستويات الصوت المطابقة تلقائيًا."],
        ["Sliders restored to the measured original-matched volumes.", "أُعيدت المنزلقات إلى المستويات المقاسة المطابقة للأصل."],
        [" old cloned voice(s).", " صوتًا مستنسخًا قديمًا."],
        [" cloned voice(s) from your account.", " صوتًا مستنسخًا من حسابك."],
        [" errors)", " أخطاء)"],
        [" unlocked line(s)...", " سطرًا غير مقفل..."],
        [" unlocked line(s). Locked lines untouched.", " سطرًا غير مقفل. الأسطر المقفلة لم تُمس."],
        [" Use 🔒 to protect lines from Auto-Fix, Translate and Tashkeel.", " استخدم 🔒 لحماية الأسطر من الإصلاح التلقائي والترجمة والتشكيل."],
        [" unlocked line(s) to Arabic (locked lines skipped)...", " سطرًا غير مقفل إلى العربية (الأسطر المقفلة مستثناة)..."],
        [". Pick voices in Step 4 (or Auto-Assign) first.", ". اختر أصواتًا في الخطوة 4 (أو التعيين التلقائي) أولاً."],
        [". Pick one in Step 4 first.", ". اختر صوتًا في الخطوة 4 أولاً."],
        [" seconds long. This build accepts up to 60 seconds — please trim it first.", " ثانية. يقبل هذا الإصدار حتى 60 ثانية — يرجى تقليمه أولاً."],
        ["This clip is only ", "مدة هذا المقطع فقط "],
        [" seconds long. The minimum is ", " ثانية. الحد الأدنى "],
        ["For a lip-synced clip, the limit is ", "بالنسبة لمقطع بمزامنة الشفاه، الحد الأقصى هو "],
        [" seconds — please trim it first.", " ثانية — يرجى تقليمه أولاً."],
        ["Heads up: this clip is under ", "تنبيه: مدة هذا المقطع أقل من "],
        [" seconds. Voice cloning can still run, but a longer clip usually sounds more convincing.", " ثانية. لا يزال بإمكان استنساخ الصوت العمل، لكن المقطع الأطول عادةً ما يبدو أكثر إقناعًا."],
        ["Also generate a lip-synced video", "أنشئ أيضًا فيديو بمزامنة الشفاه"],
        ["Lip-sync selected: clip must be ", "تم اختيار مزامنة الشفاه: يجب أن تكون مدة المقطع "],
        ["Clip must be ", "يجب أن تكون مدة المقطع "],
        [" MB). The limit is 400 MB — a 1-minute 1080p clip is usually well under 150 MB.", " ميجابايت). الحد الأقصى 400 ميجابايت — عادةً ما يكون مقطع بدقة 1080p لمدة دقيقة واحدة أقل من 150 ميجابايت بكثير."],
        [" ⚠️ stretched to the limit.", " ⚠️ تم التمديد إلى الحد الأقصى."],
        [" lines. The Step 6 player now uses it.", " أسطر. مشغّل الخطوة 6 يستخدمه الآن."],
        [" only...", " فقط..."],
        [" voice(s)... this may take a minute.", " صوت... قد يستغرق هذا دقيقة."],
        [") — redeploy main.py with the /api/cleanup_voices block.", ") — أعد نشر main.py مع كتلة /api/cleanup_voices."],
        ["s — the limit is 20 seconds.", " ثانية — الحد الأقصى 20 ثانية."],
        ["s - the limit is 20 seconds.", " ثانية - الحد الأقصى 20 ثانية."],
        [" — no lines were trimmed.", " — لم يُقلَّم أي سطر."],
        [" line(s) still trimmed.", " سطرًا لا يزال مقلَّمًا."],
        [" Please upload the matching audio/video file to enable preview, re-speak, and other functions.", " يرجى رفع ملف الصوت/الفيديو المطابق لتفعيل المعاينة وإعادة النطق والوظائف الأخرى."],
        [" Upload your next video in Step 1.", " ارفع الفيديو التالي في الخطوة 1."],
        [" Original media is not on the server — media features are disabled (see the yellow notice).", " الوسائط الأصلية غير موجودة على الخادم — ميزات الوسائط معطّلة (انظر التنبيه الأصفر)."],
        [" credits from your purchase have been added.", " ائتمانًا أُضيف من مشترياتك."],
        [" credits added from your purchase(s).", " ائتمانًا أُضيف من مشترياتك."],
        [" credits added from your purchase.", " ائتمانًا أُضيف من مشترياتك."],
        [". Run ✨ Auto-Fix to correct the row.", ". استخدم ✨ الإصلاح التلقائي لتصحيح هذا السطر."],
        ["Use ➕ Buy to get a pack.", "استخدم ➕ شراء للحصول على باقة."],
        ["Use ➕ Buy.", "استخدم ➕ شراء."],
        [" no longer exist in your voice account (old cloned voices are removed automatically). Re-clone in Step 3.5 or pick a voice in Step 4 before generating.", " لم تعد موجودة في حساب صوتك (تُحذف الأصوات المستنسخة القديمة تلقائيًا). أعد الاستنساخ في الخطوة 3.5 أو اختر صوتًا في الخطوة 4 قبل التوليد."],
        ["re-spoken: ", "أُعيد نطقه: "],
        ["re-stretched: ", "أُعيد تمديده: "],
        ["Line ", "السطر "],
    ];

    function tagAll() {
        // "th" was removed from this selector: the segments table's <th>Start</th>
        // time-column header was matching the same P-array entry as the big
        // green "Start" button (both are plain "Start" text on a control tag),
        // so the header was incorrectly getting the button's imperative "ابدأ"
        // translation. No table headers are translated for now.
        document.querySelectorAll("h3, button, a, p, .note, span, label").forEach(function (el) {
            if (el.dataset && el.dataset.i18n) return;
            if (el.closest && (el.closest("#notifyPanel") || el.closest("#buyModal"))) return;
            if (el.id && BANNERS[el.id]) return;
            var base = (el.textContent || "").trim();
            if (!base) return;
            var isCtl = /^(H3|BUTTON|A|LABEL)$/.test(el.tagName);
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
            // Direct-swap the two retention banners (see BANNERS above) --
            // bypasses the R-array fragment-replace path entirely.
            Object.keys(BANNERS).forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.innerHTML = BANNERS[id][lang === "ar" ? "ar" : "en"];
            });
            // Table headers, dropdown option labels and the "credits" word
            // next to the balance (see TABLE_TXT above) -- plain text, no
            // inline tags, so textContent is enough and safer than innerHTML.
            Object.keys(TABLE_TXT).forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.textContent = TABLE_TXT[id][lang === "ar" ? "ar" : "en"];
            });
            // Tooltips: attribute text, not element text, so neither tagAll()
            // nor TABLE_TXT reaches these -- set directly here.
            var userNameLink = document.getElementById("userName");
            if (userNameLink) userNameLink.title = (lang === "ar") ? "عرض حسابك" : "View your account";
            var appContactBtn = document.getElementById("appContactIconBtn");
            if (appContactBtn) {
                var contactTitle = (lang === "ar") ? "اتصل بنا" : "Contact Us";
                appContactBtn.title = contactTitle;
                appContactBtn.setAttribute("aria-label", contactTitle);
            }
            document.body.classList.toggle("lang-ar", lang === "ar");
            document.documentElement.lang = (lang === "ar") ? "ar" : "en";
            var lb = document.getElementById("langBtn");
            if (lb) lb.textContent = (lang === "en") ? "🌐 عربي" : "🌐 English";
            // Subtitle and Help/FAQ link: previously the Arabic version was
            // just appended after the always-visible English text, so both
            // showed at once in Arabic mode. Now each pair is mutually
            // exclusive -- exactly one language shows at a time.
            var subEn = document.getElementById("subtitleEn");
            if (subEn) subEn.style.display = (lang === "ar") ? "none" : "";
            var subAr = document.getElementById("subtitleAr");
            if (subAr) subAr.style.display = (lang === "ar") ? "" : "none";
            var helpEn = document.getElementById("helpEnPart");
            if (helpEn) helpEn.style.display = (lang === "ar") ? "none" : "";
            var helpAr = document.getElementById("helpArPart");
            if (helpAr) helpAr.style.display = (lang === "ar") ? "" : "none";
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
        box.innerHTML = '<span id="customVoiceNote"><strong>📤 Use your own voice clip:</strong> pick a speaker and upload an MP3/WAV clip (max 20 s) where that person speaks most of the time. The clip is not analyzed — the voice engine extracts the dominant voice, so music or other voices in it will reduce quality.</span><br>' +
            '<select id="cvSpeaker" style="width:auto;min-width:140px;margin:8px 6px 0 0;"></select>' +
            '<input type="file" id="cvFile" accept=".mp3,.wav,audio/mpeg,audio/wav" style="display:none;"><label for="cvFile" id="cvFileLabel" class="file-upload-area" style="margin-top:8px;margin-right:14px;cursor:pointer;">Choose File</label>' +
            '<button class="purple" id="cvUpload" style="margin-top:8px;">Upload as this speaker\'s voice<span class="badge" id="badgeCvUpload"></span></button> ' +
            '<button class="red" id="cvClean" style="margin-top:8px;">🧹 Clean old cloned voices</button>' +
            '<span id="cvStatus" style="margin-left:10px;font-size:12px;color:#6b7280;"></span>';
        bindCv();
        if (typeof updateBadges === "function") updateBadges();
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
        fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: [], job_id: currentJobId }) })
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
            fetch("/api/cleanup_voices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: [], job_id: currentJobId }) }).catch(function () {});
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
            if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
        if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
            if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
        if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
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
    window.deadSpaceAllowed = window.deadSpaceAllowed || {};
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
                        obj.dead_space_allowed = window.deadSpaceAllowed || {};
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
            thead.innerHTML = "<tr><th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; lines overlapping it are NOT faded'>No overlap</th><th title='Checked = let this line run into the silent gap before the next line (or, for the last line, to the end of the audio) instead of fading at its own original end'>Dead space</th><th title='How much this line may be sped up or slowed down to fit its slot'>Time Stretch</th><th>\u25B6 Orig</th><th>\uD83D\uDD0A Dub</th><th>Auto</th><th style='min-width:130px'>Volume</th><th></th></tr>";
        }
        var tbody = document.querySelector("#volumeTable tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        (lines || []).forEach(function (ln, i) {
            var seg = segmentsData.find(function (s) { return s.segment_id === ln.segment_id; }) || {};
            var tr = document.createElement("tr");
            // Needs-attention mark: driven straight off this line's current data,
            // so it disappears on its own the next time this table is rebuilt
            // (after Apply/Regenerate/Time-Stretch) once ln.trimmed and
            // ln.tempo_warning are both no longer true -- no separate clear step.
            var needsAttention = !!(ln.trimmed || ln.tempo_warning);
            var warnMsg = ln.trimmed
                ? "This line's Arabic audio is cut short in the final mix \u2014 it doesn't fit its slot even after stretching. Try a looser Time Stretch, allow overlap/dead space for it, or shorten the line."
                : "This line needed the maximum Time Stretch setting to fit its slot.";
            if (needsAttention) { tr.style.background = "rgba(245,158,11,0.14)"; tr.title = warnMsg; }
            function td(html) { var c = document.createElement("td"); c.innerHTML = html; tr.appendChild(c); return c; }
            td((needsAttention ? '<span title="' + warnMsg.replace(/"/g, "'") + '" style="margin-right:4px;">\u26A0\uFE0F</span>' : "") + String(i + 1));
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
                if (btn) btn.textContent = "\uD83D\uDD0A Apply changes & rebuild MP3 \u2022";
            };
            cN.appendChild(cb); tr.appendChild(cN);
            var cD = document.createElement("td");
            var cbD = document.createElement("input"); cbD.type = "checkbox";
            cbD.checked = !!window.deadSpaceAllowed[ln.segment_id];
            cbD.title = "Checked = let this line run into the silent gap before the next line (or, for the last line, to the end of the audio) instead of fading at its own original end.";
            cbD.onchange = function () {
                if (cbD.checked) window.deadSpaceAllowed[ln.segment_id] = true;
                else delete window.deadSpaceAllowed[ln.segment_id];
                if (typeof renderTimeline === "function") renderTimeline();
                var btn = document.getElementById("applyVolumesBtn");
                if (btn) btn.textContent = "🔊 Apply changes & rebuild MP3 •";
            };
            cD.appendChild(cbD); tr.appendChild(cD);
            var cT = document.createElement("td");
            var selT = document.createElement("select");
            selT.title = "How much this line's Arabic audio may be sped up or slowed down to fit its slot.";
            [["excellent", "Excellent (Recommended)"], ["good", "Good"], ["maximum", "Maximum"]].forEach(function (opt) {
                var o = document.createElement("option"); o.value = opt[0]; o.textContent = opt[1]; selT.appendChild(o);
            });
            selT.value = seg.tempo_mode || "excellent";
            selT.onchange = function () {
                seg.tempo_mode = selT.value;
                selT.disabled = true;
                Promise.resolve(restretchLine(seg)).then(function () { selT.disabled = false; }, function () { selT.disabled = false; });
            };
            cT.appendChild(selT); tr.appendChild(cT);
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
    // DISABLED: this predates draw9() below (v4), which draws the same
    // cross-speaker fade indicator but correctly SKIPS same-speaker
    // neighbors (`if ((q.speaker...) === (seg.speaker...)) return;`). This
    // function never had that check, so it was flagging a line as
    // "faded/trimmed" just because another line by the SAME speaker started
    // after it -- not an actual overlap between two different speakers.
    // It also drew its fade box as a free-floating child of the LANE (not
    // clipped to the block, unlike draw9's), which is why it could render
    // on top of the green Arabic-audio-length overlay. Left as a no-op
    // rather than deleted, since schedule7/the renderTimeline wrap and the
    // (unrelated, still-needed) checkGenerateProgress wrap further below
    // both still reference it.
    var pending7 = false;
    function schedule7() { if (pending7) return; pending7 = true; requestAnimationFrame(function () { pending7 = false; draw7(); }); }
    function draw7() {
        return;
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
                var r = await fetch("/api/progress/generate?t=" + Date.now() + "&job_id=" + encodeURIComponent(currentJobId || ""));
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
        if (thr && !thr.dataset.v9) { thr.dataset.v9 = "1"; thr.innerHTML = "<th>#</th><th>Speaker</th><th>Line</th><th title='Unchecked = this line may be talked over; intruders are NOT faded'>No overlap</th><th title='Checked = let this line run into the silent gap before the next line (or, for the last line, to the end of the audio) instead of fading at its own original end'>Dead space</th><th title='How much this line may be sped up or slowed down to fit its slot'>Time Stretch</th><th>▶ Orig</th><th>🔊 Dub</th><th>Auto</th><th style='min-width:130px'>Volume</th><th></th>"; }
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
            if (window._revivePending) notify("info", "Restored your session. Reconnecting to your last job on the server...");
    else notify("info", "Restored your previous session from this browser. Re-upload the original file to enable preview/clone/merge.");
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
    // DISABLED: draw9() below (v4, "fixed widths") replaced this with an
    // equivalent cross-speaker fade tail on a FIXED-width block. Both used to
    // run on every renderTimeline() AND both kept writing their own formula
    // into the block's own style.width -- draw8 grows the block to the real
    // audio duration, draw9 sizes it to the English text length. Since each
    // one's write is a mutation the other's MutationObserver (hook8/hook9)
    // is watching, they kept re-triggering each other forever: the block
    // (and the green Arabic-audio-length overlay, which reads the block's
    // live width) flickered between the two widths. Left as a no-op rather
    // than deleted, since sched8/hook8/the renderTimeline wrap below still
    // reference draw8() -- this keeps all of that harmless without touching
    // the unrelated fixStep2/fixLockAll/fixVolume/etc. in this same IIFE.
    function draw8() {
        return;
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
        // DISABLED alongside draw8() above -- see that function's comment.
        // Left as a no-op (never installs the observer) rather than deleted,
        // since it's still called from the renderTimeline wrap below and
        // from the 300ms document-body watchdog further down.
        return;
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
            // Block width follows the English line length (same formula as the base
            // renderTimeline), not the Step 2 table's timespan -- this IIFE runs last
            // among the fade-overlay wrappers, so its own width assertion is what
            // actually sticks; keep `slot` below for the audio-duration/collision math,
            // which is about real time positions and is unaffected by visual width.
            var engLen = (seg.text || "").length;
            var slotPx = Math.max(8, (engLen / ENGLISH_CHARS_PER_SEC) * scale);
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

// ===== UI UPDATE v5: remove stray lock-all button beside Step 2 title, keep the header one =====
(function () {
    if (window._uiV5) return; window._uiV5 = true;
    function isStrayLock(b) {
        if (!b || b.tagName !== "BUTTON") return false;
        if (b.closest("#segmentsTable thead")) return false;           // keep the header one
        var id = (b.id || "") + " " + (b.className || "");
        var tx = (b.textContent || "").trim();
        var ti = (b.title || "");
        return (/lockAll/i.test(id)) || ((tx === "\uD83D\uDD13" || tx === "\uD83D\uDD12") && /lock/i.test(ti) && !b.classList.contains("action-btn"));
    }
    function killStray() {
        document.querySelectorAll("button").forEach(function (b) { if (isStrayLock(b)) b.remove(); });
    }
    // Block the old injector at the door: swallow buttons appended to the Step 2 heading
    function shieldHeading() {
        var h = document.querySelector("#editorSection h3");
        if (h && !h._lockShield) {
            h._lockShield = true;
            var orig = h.appendChild.bind(h);
            h.appendChild = function (n) { if (n && n.tagName === "BUTTON") return n; return orig(n); };
        }
    }
    killStray(); shieldHeading();
    var rounds = 0;
    var iv = setInterval(function () { killStray(); shieldHeading(); if (++rounds > 30) clearInterval(iv); }, 1200);
    new MutationObserver(function () { killStray(); }).observe(document.body, { childList: true, subtree: true });
})();

// ===== UI UPDATE v6: clear saved workspace on logout (fresh start after proper logout) =====
(function () {
    if (window._uiV6) return; window._uiV6 = true;
    var KEYS = ["lisan_workspace_v1"];
    function clearWs() {
        try { KEYS.forEach(function (k) { localStorage.removeItem(k); }); } catch (e) {}
    }
    // Wrap doLogout whenever it exists
    function wrapLogout() {
        if (typeof window.doLogout === "function" && !window.doLogout._v6) {
            var orig = window.doLogout;
            window.doLogout = function () { clearWs(); return orig.apply(this, arguments); };
            window.doLogout._v6 = true;
        }
    }
    wrapLogout();
    var iv = setInterval(function () { wrapLogout(); }, 1500);
    setTimeout(function () { clearInterval(iv); }, 30000);
    // Fallback 1: catch logout clicks even if they bypass doLogout
    document.addEventListener("click", function (e) {
        var el = e.target && e.target.closest ? e.target.closest("button, a") : null;
        if (!el) return;
        var tx = (el.textContent || "").trim().toLowerCase();
        var id = (el.id || "").toLowerCase();
        if (id.indexOf("logout") > -1 || /log ?out|sign ?out|تسجيل الخروج/.test(tx)) clearWs();
    }, true);
    // Fallback 2: catch direct calls to the logout API
    if (!window._fetchV6) {
        window._fetchV6 = true;
        var _f = window.fetch;
        window.fetch = function (url, opts) {
            try { if (String(url).indexOf("/api/logout") > -1) clearWs(); } catch (e) {}
            return _f.apply(this, arguments);
        };
    }
})();

// ===== UI UPDATE v7: no leave-popup inside profile, Help/Usage in new tabs, server-revive restore =====
(function () {
    if (window._uiV7) return; window._uiV7 = true;

    // If a saved job exists, restore should wait for the server probe before claiming media is gone
    try {
        var raw = localStorage.getItem("lisan_workspace_v1");
        if (raw && JSON.parse(raw).currentJobId) window._revivePending = true;
    } catch (e) {}

    // 1) beforeunload gatekeeper (capture phase runs before older listeners)
    window.addEventListener("beforeunload", function (e) {
        var generating = false;
        try { generating = !!window.generatePollTimer || !!window._mergeRunning || !!window._mergePollTimer; } catch (err) {}
        var internal = window._internalNav && !window._forceWarn;
        if ((internal && !generating) || (!generating && !window._forceWarn)) { e.stopImmediatePropagation(); return; }
        e.returnValue = "";
    }, true);
    document.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
        if (a) { var h = a.getAttribute("href") || ""; if (h.charAt(0) === "/" && h.indexOf("//") !== 0) window._internalNav = true; }
    }, true);

    // 2) Help & Usage open in NEW tabs: the working page never unloads
    document.addEventListener("click", function (e) {
        var el = e.target && e.target.closest ? e.target.closest("a,button") : null;
        if (!el) return;
        var href = (el.getAttribute && el.getAttribute("href")) || "";
        var tx = (el.textContent || "").trim();
        var target = null;
        if (href === "/help") target = "/help";
        else if (href === "/account" || /usage|account|النقاط|الاستخدام/i.test(tx)) target = "/account";
        if (!target) return;
        if (el.tagName === "A" && el.getAttribute("target") === "_blank") return;
        e.preventDefault(); e.stopPropagation();
        window.open(target, "_blank", "noopener");
    }, true);

    // 3) Server-revive: reuse the app's own progress handlers to bring Step 6 / timeline / audio back
    function probeAndRevive() {
        window._revivePending = false;
        if (!window.currentJobId) return;
        function banner() {
            if (typeof window._markNoMedia === "function") window._markNoMedia();
            notify("info", "Your last media is no longer on the server (it expires after ~6 hours or a restart). Re-upload the original file to continue.");
        }
        fetch("/api/progress/generate?t=" + Date.now(), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.status === "done" && d.result) {
                    window._mediaAlive = true;
                    if (typeof window.checkGenerateProgress === "function") window.checkGenerateProgress();
                    ["checkMergeProgress", "pollMerge", "checkMerge"].forEach(function (n) {
                        if (typeof window[n] === "function") { try { window[n](); } catch (err) {} }
                    });
                } else banner();
            })
            .catch(banner);
    }
    window._reviveProbe = probeAndRevive;
    var tries = 0;
    var iv = setInterval(function () {
        tries++;
        if (window.currentJobId && document.getElementById("segmentsTable")) { clearInterval(iv); setTimeout(probeAndRevive, 600); }
        else if (tries > 20) clearInterval(iv);
    }, 500);
})();



// ===== AUTO-STRETCH TIMELINE ON WINDOW RESIZE =====
(function() {
    let resizeTimer;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimer);
        // Wait 200ms after user stops resizing to prevent lag
        resizeTimer = setTimeout(function() {
            if (typeof renderTimeline === 'function') {
                renderTimeline();
            }
        }, 200);
    });
})();

// ===== Floating quick-nav between steps (hover the edge tab to jump) =====
(function () {
    if (document.getElementById("stepNavWidget")) return;

    var STEPS = [
        { id: "step1Card", label: "1 · Upload" },
        { id: "editorSection", label: "2 · Edit Segments" },
        { id: "voicesSection", label: "3 · Voice Cloning" },
        { id: "cloneAnalysisSection", label: "3.5 · Choose Speakers" },
        { id: "speakerVoicesSection", label: "4 · Speaker Voices" },
        { id: "generateSection", label: "5 · Generate Audio" },
        { id: "resultSection", label: "6 · Final Result" },
        { id: "lipsyncSection", label: "7 · Lip-Sync" }
    ];

    var widget = document.createElement("div");
    widget.id = "stepNavWidget";

    var panel = document.createElement("div");
    panel.id = "stepNavPanel";

    var handle = document.createElement("div");
    handle.id = "stepNavHandle";
    handle.textContent = "☰";
    handle.title = "Jump to a step";

    widget.appendChild(panel);
    widget.appendChild(handle);
    document.body.appendChild(widget);

    function refreshPanel() {
        panel.innerHTML = "";
        var any = false;
        STEPS.forEach(function (s) {
            var el = document.getElementById(s.id);
            if (!el || el.classList.contains("hidden")) return;
            any = true;
            var a = document.createElement("a");
            a.textContent = s.label;
            a.onclick = function () { el.scrollIntoView({ behavior: "smooth", block: "start" }); };
            panel.appendChild(a);
        });
        if (!any) {
            var p = document.createElement("span");
            p.style.cssText = "font-size:12px;color:#6b7280;padding:4px 6px;";
            p.textContent = "No steps to show yet.";
            panel.appendChild(p);
        }
    }

    widget.addEventListener("mouseenter", refreshPanel);
    refreshPanel();
})();

// ===== TIMELINE: Arabic-text tooltips + playhead marker synced to the results player =====
(function () {
    if (window._timelineTipPlayheadV1) return; window._timelineTipPlayheadV1 = true;

    // --- Arabic text tooltip on each segment block ---
    function applySegmentTooltips() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData || !segmentsData.length) return;
        var divs = wrap.querySelectorAll("div");
        for (var i = 0; i < divs.length; i++) {
            var b = divs[i];
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") === -1 || st.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg) continue;
            var txt = (seg.arabic_text || "").trim();
            b.title = txt ? ("Line " + num + ": " + txt) : ("Line " + num + ": drag to shift");
        }
    }

    // --- Playhead marker: follows the Step 6 results <audio> element ---
    function timelineScale() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData || !segmentsData.length) return null;
        var total = (typeof totalDuration === "number" && totalDuration > 0) ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        return { wrap: wrap, scale: (wrap.clientWidth || 900) / total };
    }
    function ensurePlayhead(wrap) {
        var m = document.getElementById("timelinePlayhead");
        if (!m) {
            m = document.createElement("div");
            m.id = "timelinePlayhead";
            m.style.cssText = "position:absolute;top:0;bottom:0;width:2px;background:#ef4444;z-index:20;pointer-events:none;display:none;box-shadow:0 0 4px rgba(239,68,68,0.8);";
            wrap.appendChild(m);
        } else if (m.parentNode !== wrap) {
            wrap.appendChild(m);
        }
        return m;
    }
    var phRaf = null;
    function tickPlayhead(audio) {
        var g = timelineScale();
        if (!g) { phRaf = null; return; }
        var m = ensurePlayhead(g.wrap);
        m.style.left = Math.max(0, audio.currentTime * g.scale) + "px";
        if (audio.paused || audio.ended) { phRaf = null; return; }
        phRaf = requestAnimationFrame(function () { tickPlayhead(audio); });
    }
    function attachPlayhead(audio) {
        if (!audio || audio.dataset.playheadHooked) return;
        audio.dataset.playheadHooked = "1";
        audio.addEventListener("play", function () {
            var g = timelineScale(); if (!g) return;
            ensurePlayhead(g.wrap).style.display = "block";
            if (phRaf) cancelAnimationFrame(phRaf);
            tickPlayhead(audio);
        });
        audio.addEventListener("pause", function () { if (phRaf) { cancelAnimationFrame(phRaf); phRaf = null; } });
        audio.addEventListener("ended", function () {
            if (phRaf) { cancelAnimationFrame(phRaf); phRaf = null; }
            var m = document.getElementById("timelinePlayhead");
            if (m) m.style.display = "none";
        });
        audio.addEventListener("seeked", function () {
            var g = timelineScale(); if (!g) return;
            var m = ensurePlayhead(g.wrap);
            m.style.left = Math.max(0, audio.currentTime * g.scale) + "px";
        });
    }
    function scanForPlayer() {
        var au = document.querySelector("#audioResults audio");
        if (au) attachPlayhead(au);
    }
    scanForPlayer();
    var audioResultsEl = document.getElementById("audioResults");
    if (audioResultsEl && typeof MutationObserver !== "undefined") {
        new MutationObserver(scanForPlayer).observe(audioResultsEl, { childList: true, subtree: true });
    }

    // Both the tooltip pass and the playhead re-attach need to run after every
    // timeline render (renderTimeline wipes #timelineWrap's contents each time).
    if (typeof renderTimeline === "function") {
        var _rtTipPh = renderTimeline;
        renderTimeline = function () {
            var r = _rtTipPh.apply(this, arguments);
            applySegmentTooltips();
            var au = document.querySelector("#audioResults audio");
            if (au && !au.paused) {
                var g = timelineScale();
                if (g) { var m = ensurePlayhead(g.wrap); m.style.display = "block"; m.style.left = Math.max(0, au.currentTime * g.scale) + "px"; }
            }
            return r;
        };
    }
})();

// ===== TIMELINE: overlay showing each line's actual Arabic audio duration =====
// vs its slot — the timeline block's own width is always the segment's
// original slot (end - start). This draws ONE continuous bar per line: it
// starts aligned with the block's own left edge, is tinted lighter for
// however much of the slot the actually-generated Arabic audio fills, and,
// only when the audio is LONGER than the slot, continues past the block's
// right edge in a darker shade for the extra length — a single element, not
// two separate pieces, so there is nothing that can visually double up.
// Only appears once a line has been generated (needs window._lineDurations,
// populated after Step 5 Generate completes).
(function () {
    if (window._timelineDubDurationV2) return; window._timelineDubDurationV2 = true;
    // A stray element from the earlier two-piece version (dubDurFill /
    // dubDurOverflow) would be exactly the "doubled" look reported — make
    // sure none linger from before this rewrite.
    document.querySelectorAll(".dubDurFill, .dubDurOverflow").forEach(function (el) { el.remove(); });

    // Bars are kept and UPDATED in place (never blindly removed + recreated)
    // whenever the numbers haven't actually changed. renderTimeline() gets
    // re-invoked constantly for unrelated reasons (any DOM change anywhere
    // on the page retriggers a 300ms watchdog that calls it again), and a
    // blind remove-then-recreate every single time was itself a DOM mutation
    // that retriggered that same watchdog. Only touching the DOM when a
    // value actually differs breaks that loop.
    window._dubOverlayNodes = window._dubOverlayNodes || {};
    function drawDubDurationOverlay() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || !segmentsData || !segmentsData.length) return;
        if (window._hideArabicDubOverlay) {
            // Toggle is off -- drop any bars/legend already drawn and stop.
            Object.keys(window._dubOverlayNodes).forEach(function (sid) {
                try { window._dubOverlayNodes[sid].remove(); } catch (e) {}
                delete window._dubOverlayNodes[sid];
            });
            var leg0 = document.getElementById("timelineLegendDub");
            if (leg0) leg0.remove();
            return;
        }
        var total = (typeof totalDuration === "number" && totalDuration > 0) ? totalDuration : Math.max.apply(null, segmentsData.map(function (s) { return s.end; }).concat([1]));
        var scale = (wrap.clientWidth || 900) / total;
        var divs = wrap.querySelectorAll("div");
        var any = false;
        var seen = {};
        for (var i = 0; i < divs.length; i++) {
            var b = divs[i];
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") === -1 || st.indexOf("grab") === -1) continue;
            var num = parseInt(b.textContent, 10);
            if (!num || num < 1 || num > segmentsData.length) continue;
            var seg = segmentsData[num - 1];
            if (!seg || !(seg.arabic_text || "").trim()) continue;
            var dubDur = (window._lineDurations || {})[seg.segment_id] || 0;
            if (dubDur <= 0) continue;
            var lane = b.parentElement;
            if (!lane) continue;
            any = true;
            seen[seg.segment_id] = true;
            var slot = Math.max(seg.end - seg.start, 0.01);
            var blockW = b.offsetWidth || Math.max(8, slot * scale);
            var totalPx = Math.max(blockW, dubDur * scale);
            var hasOverflow = (totalPx - blockW) > 1;
            if (!hasOverflow) totalPx = blockW;

            var bar = window._dubOverlayNodes[seg.segment_id];
            if (bar && (!bar.isConnected || bar.parentNode !== lane)) {
                try { bar.remove(); } catch (e) {}
                bar = null;
            }
            if (!bar) {
                bar = document.createElement("div");
                bar.className = "dubDurBar";
                bar.style.cssText = "position:absolute;pointer-events:none;z-index:3;border-radius:0 4px 4px 0;";
                lane.appendChild(bar);
                window._dubOverlayNodes[seg.segment_id] = bar;
            }
            var topStr = b.offsetTop + "px", hStr = b.offsetHeight + "px", leftStr = b.offsetLeft + "px", wStr = totalPx + "px";
            if (bar.style.top !== topStr) bar.style.top = topStr;
            if (bar.style.height !== hStr) bar.style.height = hStr;
            if (bar.style.left !== leftStr) bar.style.left = leftStr;
            if (bar.style.width !== wStr) bar.style.width = wStr;
            var bg;
            if (hasOverflow) {
                var pct = Math.max(0, Math.min(100, (blockW / totalPx) * 100));
                var p1 = Math.max(0, pct - 0.4).toFixed(2), p2 = Math.min(100, pct + 0.4).toFixed(2);
                bg = "linear-gradient(90deg, rgba(34,197,94,0.35) 0%, rgba(34,197,94,0.35) " + p1 + "%, #15803d " + p1 + "%, #15803d " + p2 + "%, rgba(34,197,94,0.6) " + p2 + "%, rgba(34,197,94,0.6) 100%)";
            } else {
                bg = "rgba(34,197,94,0.35)";
            }
            if (bar.style.background !== bg) bar.style.background = bg;
            var title = hasOverflow
                ? "Arabic audio: " + dubDur.toFixed(2) + "s — fills its " + slot.toFixed(2) + "s slot and runs " + (dubDur - slot).toFixed(2) + "s past it"
                : "Arabic audio: " + dubDur.toFixed(2) + "s of a " + slot.toFixed(2) + "s slot";
            if (bar.title !== title) bar.title = title;
        }
        // Drop overlay bars for segments that no longer qualify (deleted,
        // no longer generated, etc.) so they don't linger.
        Object.keys(window._dubOverlayNodes).forEach(function (sid) {
            if (seen[sid]) return;
            try { window._dubOverlayNodes[sid].remove(); } catch (e) {}
            delete window._dubOverlayNodes[sid];
        });
        if (any && !document.getElementById("timelineLegendDub")) {
            var leg = document.createElement("div");
            leg.id = "timelineLegendDub";
            leg.style.cssText = "display:flex;gap:16px;justify-content:flex-end;align-items:center;margin-top:4px;font-size:11px;color:#64748b;";
            leg.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:16px;height:12px;background:linear-gradient(90deg, rgba(34,197,94,0.55) 0 60%, rgba(34,197,94,0.15) 60% 100%);border:1px solid #15803d;border-radius:3px;display:inline-block;"></span>Arabic audio length (within slot)</span>'
                + '<span style="display:inline-flex;align-items:center;gap:5px;"><span style="width:12px;height:12px;background:rgba(34,197,94,0.55);border:1px dashed #15803d;border-radius:3px;display:inline-block;"></span>Arabic audio runs past its slot</span>';
            var after = document.getElementById("timelineLegendFinal") || wrap;
            if (after && after.parentNode) after.parentNode.insertBefore(leg, after.nextSibling);
        }
    }

    // Live drag-sync: the block's own left/width update instantly during a
    // drag (mousemove writes box.style.left directly, without a full
    // renderTimeline() call). The bar is a SIBLING of the block (never a
    // child — the block clips its children, which would cut off the part
    // that runs past its edge), so it needs an explicit redraw to track the
    // block while dragging. This watches each block's style attribute and
    // redraws on every animation frame while a drag is in progress — the
    // same pattern already used above for the fade-overlay hooks (hook8/
    // hook9 + sched8/sched9). drawDubDurationOverlay() never writes to a
    // block's own style, so there's no feedback-loop risk.
    var pendDub = false;
    function scheduleDubDraw() {
        if (pendDub) return;
        pendDub = true;
        requestAnimationFrame(function () { pendDub = false; drawDubDurationOverlay(); });
    }
    function hookDubBlocks() {
        var wrap = document.getElementById("timelineWrap");
        if (!wrap || typeof MutationObserver === "undefined") return;
        wrap.querySelectorAll("div").forEach(function (b) {
            var st = b.getAttribute("style") || "";
            if (st.indexOf("cursor") > -1 && st.indexOf("grab") > -1 && !b.dataset.hookDub) {
                b.dataset.hookDub = "1";
                new MutationObserver(scheduleDubDraw).observe(b, { attributes: true, attributeFilter: ["style"] });
            }
        });
    }

    if (typeof renderTimeline === "function") {
        var _rtDub = renderTimeline;
        renderTimeline = function () {
            var r = _rtDub.apply(this, arguments);
            drawDubDurationOverlay();
            hookDubBlocks();
            return r;
        };
    }
})();
