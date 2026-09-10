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
            td(ln.orig_db === null || ln.orig_db === undefined ? "—" : ln.orig_db + " dB");
            td(ln.dub_db === null || ln.dub_db === undefined ? "—" : ln.dub_db + " dB");
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