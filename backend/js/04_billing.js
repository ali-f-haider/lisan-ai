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

