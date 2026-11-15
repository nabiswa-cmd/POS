/* Phone camera barcode scanner.
   Usage:  CamScan.attach(inputElement)   adds a "Camera" button next to the input; a scanned code is typed into the
           input and Enter is pressed for it, so the page behaves exactly as if a hardware scanner had been used.
   Uses the browser's built-in BarcodeDetector when there is one (Chrome on Android), otherwise loads the bundled
   ZXing reader. Browsers only allow the camera on HTTPS pages (or localhost): on a plain http://192.168... address the
   button explains that instead of failing silently. */
var CamScan = (function () {
    "use strict";
    var FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "qr_code"];
    var ZXING_SRC = (document.currentScript && document.currentScript.src || "").replace(/camscan\.js.*$/, "vendor/zxing.min.js");
    var state = null;

    function secure() { return window.isSecureContext || /^(localhost|127\.0\.0\.1)$/.test(location.hostname); }
    function hasCamera() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia); }
    function beep() {
        try { var C = window.AudioContext || window.webkitAudioContext, c = new C(), o = c.createOscillator(); o.frequency.value = 1100;
              o.connect(c.destination); o.start(); setTimeout(function () { o.stop(); c.close(); }, 90); } catch (e) {}
        try { if (navigator.vibrate) navigator.vibrate(60); } catch (e) {}
    }
    function loadZxing() {
        return new Promise(function (ok, bad) {
            if (window.ZXing) return ok(window.ZXing);
            var s = document.createElement("script"); s.src = ZXING_SRC;
            s.onload = function () { window.ZXing ? ok(window.ZXing) : bad(new Error("reader missing")); };
            s.onerror = function () { bad(new Error("reader missing")); };
            document.head.appendChild(s);
        });
    }

    function close() {
        if (!state) return;
        var s = state; state = null;
        s.dead = true;
        if (s.raf) cancelAnimationFrame(s.raf);
        if (s.reader) { try { s.reader.reset(); } catch (e) {} }
        if (s.stream) s.stream.getTracks().forEach(function (t) { t.stop(); });
        s.root.remove();
        document.removeEventListener("keydown", s.onKey, true);
    }

    function open(onCode, opts) {
        if (state) return;
        opts = opts || {};
        var root = document.createElement("div");
        root.className = "cs-overlay"; root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true"); root.setAttribute("aria-label", "Scan with camera");
        root.innerHTML = '<div class="cs-bar"><span class="cs-title">Point the camera at the barcode</span><button type="button" class="cs-torch" hidden>Light</button><button type="button" class="cs-x" aria-label="Close camera">&times;</button></div>' +
            '<div class="cs-view"><video playsinline muted autoplay></video><div class="cs-frame"></div></div><p class="cs-msg" role="status"></p>';
        document.body.appendChild(root);
        var video = root.querySelector("video"), msg = root.querySelector(".cs-msg"), torch = root.querySelector(".cs-torch");
        state = { root: root, dead: false };
        var s = state, lastCode = "", lastAt = 0;
        function say(t) { msg.textContent = t || ""; }
        function found(code) {
            var now = Date.now();
            if (!code || (code === lastCode && now - lastAt < 2000)) return;       // the same label held in view counts once
            lastCode = code; lastAt = now; beep(); say("Read " + code);
            try { onCode(code); } catch (e) {}
            if (!opts.keepOpen) close();
        }
        s.onKey = function (e) { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); } };
        document.addEventListener("keydown", s.onKey, true);
        root.addEventListener("click", function (e) { e.stopPropagation(); });
        root.querySelector(".cs-x").addEventListener("click", close);

        if (!hasCamera() || !secure()) {
            say(!secure() ? "The camera only works on a secure (https) address. Open the shop's https address, or type the code." : "This browser cannot use the camera.");
            return;
        }
        var constraints = { audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } } };
        navigator.mediaDevices.getUserMedia(constraints).then(function (stream) {
            if (s.dead) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
            s.stream = stream; video.srcObject = stream;
            var track = stream.getVideoTracks()[0], caps = track.getCapabilities ? track.getCapabilities() : {};
            if (caps.torch) { torch.hidden = false; var on = false; torch.addEventListener("click", function () { on = !on; track.applyConstraints({ advanced: [{ torch: on }] }).catch(function () {}); }); }
            say("Looking for a barcode...");
            if ("BarcodeDetector" in window) {
                var det;
                try { det = new window.BarcodeDetector({ formats: FORMATS }); } catch (e) { det = new window.BarcodeDetector(); }
                var busy = false, tick = function () {
                    if (s.dead) return;
                    if (!busy && video.readyState >= 2) {
                        busy = true;
                        det.detect(video).then(function (r) { if (r && r.length) found(r[0].rawValue); }).catch(function () {}).then(function () { busy = false; });
                    }
                    s.raf = requestAnimationFrame(tick);
                };
                tick();
            } else {
                loadZxing().then(function (Z) {
                    if (s.dead) return;
                    var hints = new Map(), f = Z.BarcodeFormat;
                    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [f.EAN_13, f.EAN_8, f.UPC_A, f.UPC_E, f.CODE_128, f.CODE_39, f.ITF, f.QR_CODE]);
                    hints.set(Z.DecodeHintType.TRY_HARDER, true);
                    s.reader = new Z.BrowserMultiFormatReader(hints, 250);
                    s.reader.decodeFromStream(stream, video, function (res) { if (res && !s.dead) found(res.getText()); });
                }).catch(function () { say("The barcode reader could not load. Type the code instead."); });
            }
        }).catch(function (err) {
            say(err && err.name === "NotAllowedError" ? "Camera permission was refused. Allow the camera for this site in the browser settings." :
                "Could not start the camera (" + (err && err.name || "error") + "). Type the code instead.");
        });
    }

    /* Add a Camera button after an input. Hardware scanners keep working; this is only an extra. */
    function attach(input, btn) {
        if (!input) return;
        btn = btn || document.createElement("button");
        btn.type = "button";
        if (!btn.parentNode) { btn.className = "cs-btn ghost"; btn.textContent = "Camera"; input.insertAdjacentElement("afterend", btn); }
        btn.addEventListener("click", function () {
            open(function (code) {
                input.value = code;
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
            });
        });
        return btn;
    }
    return { open: open, close: close, attach: attach, supported: function () { return hasCamera() && secure(); } };
})();
