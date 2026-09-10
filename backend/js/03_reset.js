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
    if ((resultsExist && !resultsDownloaded) || generating) {
        e.preventDefault();
        e.returnValue = "";
        return "";
    }
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

