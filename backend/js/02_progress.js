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


