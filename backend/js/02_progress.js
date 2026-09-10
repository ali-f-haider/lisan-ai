// ===== SAFE PROGRESS MESSAGES — does NOT replace real progress =====
var transcribeLastPercent = 0;
var generateLastPercent = 0;
var transcribeLastUpdate = Date.now();
var generateLastUpdate = Date.now();

var friendlyWaitMessages = [
    "Still working — large videos can take a few minutes on CPU.",
    "The system is processing audio carefully. Please keep this page open.",
    "Speaker detection and vocal separation are the slowest steps.",
    "If the percentage is moving, everything is fine.",
    "High-quality processing takes longer, but gives better results.",
    "Please do not refresh the page while processing.",
    "The server is still alive. Waiting for the next processing update.",
    "Some steps may stay at one percentage for a while, especially speaker detection.",
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

