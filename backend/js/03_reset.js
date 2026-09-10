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

