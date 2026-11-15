/* =====================================================================
   POS loader: the bottle cap. Pair with posloader.css.

   Full-screen overlay (same API as before):
     PosLoader.show(message, subtext)   show the overlay
     PosLoader.hide()                   hide it (calls are counted, so nested use is safe)
     PosLoader.wrap(promise, message)   show while a promise runs, then hide
     PosLoader.markup()                 HTML string of the big-cap animation, for inline use

   Buttons:
     PosLoader.btn(button, promise)     little cap runs across the button until the promise settles
     PosLoader.watch(options)           automatic: any POST/PUT/PATCH/DELETE fired by a click shows the
                                        little cap in the clicked button; slower requests with no
                                        button (keyboard shortcuts) show the full-screen overlay.
                                        options: { buttonDelay: 120, overlayDelay: 900 }   (0 = no overlay)
   It starts by itself: link clicks and form posts show the overlay while the next page loads.
   Opt a button, link or form out with data-no-loader. Forms with data-loader="Message" show the overlay on submit.
   Tune timing: PosLoader.config.minOverlay / minButton (milliseconds).
   ===================================================================== */
var PosLoader = (function () {
    'use strict';
    var uid = 0, overlay, stageHost, msgEl, subEl, count = 0, shownAt = 0, hideTimer = null, lastFocus = null;
    var config = {
        minOverlay: 1800,                                   // overlay stays at least this long once shown
        minButton: 700,                                     // little cap stays at least this long once shown
        refocus: '#scan, #barcode, [data-scan-input]',      // where focus returns for the cashier
        overlayWatchdog: 45000                              // an automatic overlay never stays longer than this
    };

    /* ---------------- big cap (overlay) ---------------- */
    function flutes(n, rv, rp, c) {
        var d = '', i, a0, a1, ap;
        c = c || 60;
        for (i = 0; i < n; i++) {
            a0 = i * 2 * Math.PI / n - Math.PI / 2; a1 = (i + 1) * 2 * Math.PI / n - Math.PI / 2; ap = (a0 + a1) / 2;
            if (!i) d += 'M' + (c + rv * Math.cos(a0)).toFixed(2) + ' ' + (c + rv * Math.sin(a0)).toFixed(2);
            d += 'Q' + (c + rp * Math.cos(ap)).toFixed(2) + ' ' + (c + rp * Math.sin(ap)).toFixed(2) + ' ' +
                 (c + rv * Math.cos(a1)).toFixed(2) + ' ' + (c + rv * Math.sin(a1)).toFixed(2);
        }
        return d + 'Z';
    }
    function lines(n, r1, r2, off, stroke, w) {
        var s = '', i, a;
        for (i = 0; i < n; i++) {
            a = (i + off) * 2 * Math.PI / n - Math.PI / 2;
            s += '<line x1="' + (60 + r1 * Math.cos(a)).toFixed(1) + '" y1="' + (60 + r1 * Math.sin(a)).toFixed(1) + '" x2="' + (60 + r2 * Math.cos(a)).toFixed(1) + '" y2="' + (60 + r2 * Math.sin(a)).toFixed(1) + '" stroke="' + stroke + '" stroke-width="' + w + '"/>';
        }
        return s;
    }
    function capLayers() {
        var id = ++uid, edge = flutes(21, 50, 63), html = '', i, n = 11;
        for (i = 0; i < n; i++) {
            html += '<div class="pl-lay" style="transform:translateZ(' + (5 - i * 1.1).toFixed(2) + 'px)"><svg viewBox="0 0 120 120"><path d="' + edge + '" fill="rgb(' + (178 - i * 11) + ',' + (24 - i) + ',' + (28 - i) + ')" stroke="rgba(60,0,0,.5)" stroke-width=".5"/></svg></div>';
        }
        html += '<div class="pl-lay pl-front"><svg viewBox="0 0 120 120"><defs>' +
            '<linearGradient id="e' + id + '" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f26a60"/><stop offset=".45" stop-color="#c0242a"/><stop offset="1" stop-color="#6b0a10"/></linearGradient>' +
            '<radialGradient id="t' + id + '" cx="32%" cy="26%" r="95%"><stop offset="0" stop-color="#ff4a45"/><stop offset=".35" stop-color="#d1161d"/><stop offset=".8" stop-color="#9a0b13"/><stop offset="1" stop-color="#6e070d"/></radialGradient>' +
            '<linearGradient id="g' + id + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".6"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>' +
            '<path d="' + edge + '" fill="url(#e' + id + ')" stroke="rgba(70,0,0,.55)" stroke-width=".7"/>' +
            lines(21, 47, 58, .5, 'rgba(70,0,6,.55)', 1.3) + lines(21, 48, 60, 0, 'rgba(255,200,190,.45)', 1.1) +
            '<circle cx="60" cy="60" r="47" fill="url(#t' + id + ')"/>' +
            '<circle cx="60" cy="60" r="46.2" fill="none" stroke="rgba(255,190,180,.55)" stroke-width="1.2"/>' +
            '<circle cx="60" cy="60" r="47.8" fill="none" stroke="rgba(60,0,4,.55)" stroke-width="1"/>' +
            '<path d="M22 50C26 30 44 17 66 16C50 24 36 36 30 56Z" fill="url(#g' + id + ')" opacity=".7"/>' +
            '</svg></div>';
        html += '<div class="pl-lay pl-back"><svg viewBox="0 0 120 120"><defs>' +
            '<linearGradient id="s' + id + '" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#d9403f"/><stop offset=".5" stop-color="#a51a20"/><stop offset="1" stop-color="#5e0a0f"/></linearGradient>' +
            '<radialGradient id="c' + id + '" cx="40%" cy="35%" r="75%"><stop offset="0" stop-color="#ffffff"/><stop offset=".25" stop-color="#c9ccd0"/><stop offset=".55" stop-color="#8a8e94"/><stop offset=".8" stop-color="#d4d7db"/><stop offset="1" stop-color="#6f737a"/></radialGradient>' +
            '<linearGradient id="r' + id + '" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f4f5f6"/><stop offset=".5" stop-color="#7d8187"/><stop offset="1" stop-color="#cfd2d6"/></linearGradient></defs>' +
            '<path d="' + edge + '" fill="url(#s' + id + ')" stroke="rgba(60,0,0,.5)" stroke-width=".7"/>' +
            lines(21, 46, 58, .5, 'rgba(60,0,6,.5)', 1.2) +
            '<circle cx="60" cy="60" r="47" fill="url(#r' + id + ')" stroke="rgba(0,0,0,.45)" stroke-width="1"/>' +
            '<circle cx="60" cy="60" r="41" fill="url(#c' + id + ')" stroke="rgba(0,0,0,.35)" stroke-width="1"/>' +
            '<path d="M30 48C34 36 46 29 60 28" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="3" stroke-linecap="round"/></svg></div>';
        return html;
    }
    function markup() {
        return '<div class="pl-stage" aria-hidden="true">' +
            '<span class="pl-wall l"></span><span class="pl-wall r"></span><div class="pl-floorline"></div>' +
            '<div class="pl-shadow-x"><div class="pl-shadow"></div></div>' +
            '<div class="pl-cap-x"><div class="pl-cap-y"><div class="pl-cap-sq"><div class="pl-cap-spin">' + capLayers() + '</div></div></div></div>' +
            '</div>';
    }

    /* ---------------- overlay control ---------------- */
    function build() {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.className = 'pl-overlay';
        overlay.setAttribute('role', 'status');
        overlay.setAttribute('aria-live', 'polite');
        overlay.tabIndex = -1;
        overlay.innerHTML = '<div class="pl-box"><div class="pl-host"></div><p class="pl-msg"></p><p class="pl-sub"></p><div class="pl-bar"></div></div>';
        document.body.appendChild(overlay);
        stageHost = overlay.querySelector('.pl-host');
        msgEl = overlay.querySelector('.pl-msg');
        subEl = overlay.querySelector('.pl-sub');
    }

    // While the overlay is up, swallow keys before any page shortcut sees them (a double F9 cannot post a sale twice)
    document.addEventListener('keydown', function (e) {
        if (count > 0 && overlay && overlay.classList.contains('is-on')) { e.preventDefault(); e.stopImmediatePropagation(); }
    }, true);

    function show(message, sub) {
        build();
        clearTimeout(hideTimer);
        count++;
        msgEl.textContent = message || 'Loading';
        subEl.textContent = sub || 'Please wait';
        if (count === 1) {
            lastFocus = document.activeElement;
            shownAt = Date.now();
            stageHost.innerHTML = markup();          // rebuilt so the cap always starts from the first frame
            overlay.classList.add('is-on');
            document.body.setAttribute('aria-busy', 'true');
            overlay.focus({ preventScroll: true });
        }
    }

    function hide(force) {
        if (!overlay) return;
        count = force ? 0 : Math.max(0, count - 1);
        if (count > 0) return;
        clearTimeout(hideTimer);
        var wait = force ? 0 : Math.max(0, config.minOverlay - (Date.now() - shownAt));
        hideTimer = setTimeout(function () {
            overlay.classList.remove('is-on');
            document.body.removeAttribute('aria-busy');
            setTimeout(function () { if (!count) stageHost.innerHTML = ''; }, 350);   // stop animating once hidden
            var target = (lastFocus && lastFocus !== document.body && document.contains(lastFocus)) ? lastFocus : document.querySelector(config.refocus);
            if (target && target.focus) target.focus({ preventScroll: true });
        }, wait);
    }

    function wrap(promise, message, sub) {
        show(message, sub);
        return Promise.resolve(promise).then(
            function (v) { hide(); return v; },
            function (e) { hide(); throw e; }
        );
    }

    /* ---------------- little cap inside a button ---------------- */
    var MINI = '<svg viewBox="0 0 24 24" aria-hidden="true"><defs><radialGradient id="pcg" cx="35%" cy="30%" r="80%">' +
        '<stop offset="0" stop-color="#ffffff"/><stop offset=".45" stop-color="#cfd2d6"/><stop offset="1" stop-color="#7d8187"/></radialGradient></defs>' +
        '<path d="' + flutes(14, 9.4, 11.8, 12) + '" fill="#b9bcc1" stroke="#2b0507" stroke-width=".9"/>' +
        '<circle cx="12" cy="12" r="8" fill="url(#pcg)" stroke="#2b0507" stroke-width=".8"/></svg>';

    function mount(btn, st) {
        var cs = getComputedStyle(btn), size, w = btn.clientWidth, h = btn.clientHeight;
        st.pos = btn.style.position;
        if (cs.position === 'static') btn.style.position = 'relative';
        size = Math.max(12, Math.min(22, Math.round(h * 0.5)));
        btn.style.setProperty('--pc-s', size + 'px');
        btn.style.setProperty('--pc-run', Math.max(20, w - size - 12) + 'px');
        btn.style.setProperty('--pc-hop', Math.max(4, Math.min(14, h - size - 8)) + 'px');
        btn.style.setProperty('--pc-t', Math.max(.9, Math.min(2, w / 160)).toFixed(2) + 's');
        st.track = document.createElement('span');
        st.track.className = 'pc-track';
        st.track.setAttribute('aria-hidden', 'true');
        st.track.innerHTML = '<span class="pc-bx"><span class="pc-by"><span class="pc-bs" style="display:block">' + MINI + '</span></span></span>';
        btn.appendChild(st.track);
        btn.classList.add('pc-busy');
        btn.setAttribute('aria-busy', 'true');
    }
    function unmount(btn, st) {
        if (st.track && st.track.parentNode) st.track.parentNode.removeChild(st.track);
        btn.classList.remove('pc-busy');
        btn.removeAttribute('aria-busy');
        btn.style.position = st.pos || '';
        ['--pc-s', '--pc-run', '--pc-hop', '--pc-t'].forEach(function (p) { btn.style.removeProperty(p); });
        st.track = null;
    }
    // Returns a function that releases the busy state. Counted per button, so overlapping requests are safe.
    function start(btn) {
        if (btn.tagName === 'INPUT') return function () {};      // <input type=submit> cannot hold a cap; the overlay covers it
        var st = btn.__pc || (btn.__pc = { n: 0, t: 0 }), released = false;
        st.n++;
        if (st.n === 1) { st.t = Date.now(); mount(btn, st); }
        return function () {
            if (released) return; released = true;
            setTimeout(function () { if (--st.n === 0) unmount(btn, st); }, Math.max(0, config.minButton - (Date.now() - st.t)));
        };
    }
    function btnBusy(btn, promise) {
        var stop = start(btn);
        return Promise.resolve(promise).then(function (v) { stop(); return v; }, function (e) { stop(); throw e; });
    }

    // A busy button ignores further clicks and Enter presses, so nothing is posted twice
    var lastClick = { el: null, at: 0 };
    document.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('button, input[type=submit], input[type=button], [data-pc]');
        if (!b || b.hasAttribute('data-no-loader')) return;
        if (b.classList.contains('pc-busy')) { e.preventDefault(); e.stopImmediatePropagation(); return; }
        lastClick = { el: b, at: Date.now() };
    }, true);

    /* ---------------- automatic wiring around fetch ---------------- */
    function watch(opts) {
        if (window.__plWatch) return;
        window.__plWatch = true;
        opts = opts || {};
        var btnDelay = opts.buttonDelay == null ? 120 : opts.buttonDelay;
        var ovDelay = opts.overlayDelay == null ? 900 : opts.overlayDelay;
        var orig = window.fetch.bind(window);
        window.fetch = function (input, init) {
            var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
            var t1 = null, t2 = null, wd = null, stopBtn = null, overlayed = false, btn = null;
            if (method !== 'GET' && method !== 'HEAD') {      // polling and page loads (GET) never trigger a loader
                if (lastClick.el && Date.now() - lastClick.at < 1200 && document.contains(lastClick.el)) btn = lastClick.el;
                if (btn) {
                    t1 = setTimeout(function () { stopBtn = start(btn); }, btnDelay);
                } else if (ovDelay > 0) {
                    t2 = setTimeout(function () {
                        overlayed = true; show('Working', 'Please wait');
                        wd = setTimeout(function () { overlayed = false; hide(true); }, config.overlayWatchdog);
                    }, ovDelay);
                }
            }
            function done() {
                clearTimeout(t1); clearTimeout(t2); clearTimeout(wd);
                if (stopBtn) stopBtn();
                if (overlayed) hide();
            }
            var p = orig(input, init);
            p.then(done, done);
            return p;
        };
    }

    /* Forms with data-loader="Message" show the overlay on submit */
    document.addEventListener('submit', function (e) {
        var f = e.target;
        if (f && f.matches && f.matches('form[data-loader]')) show(f.getAttribute('data-loader') || 'Please wait');
    });
    window.addEventListener('pageshow', function (e) { if (e.persisted) hide(true); });

    /* ---------------- page navigation: links and plain form posts ---------------- */
    var navTimer = null, navDog = null;
    function navStart(btn, message) {
        if (btn && !btn.hasAttribute('data-no-loader')) start(btn);      // released by pageshow or the watchdog
        clearTimeout(navTimer); clearTimeout(navDog);
        navTimer = setTimeout(function () {
            show(message || 'Loading');
            // a download or a blocked navigation never replaces this page, so do not stay stuck
            navDog = setTimeout(function () { hide(true); releaseAll(); }, config.navWatchdog);
        }, config.navDelay);
    }
    function releaseAll() {
        Array.prototype.forEach.call(document.querySelectorAll('.pc-busy'), function (b) {
            if (b.__pc) { b.__pc.n = 0; unmount(b, b.__pc); }
        });
    }
    config.navDelay = 200;          // ms before the overlay appears for a page change (quick pages never flash it)
    config.navWatchdog = 20000;     // ms after which a navigation that never replaced the page is given up

    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a || a.hasAttribute('data-no-loader') || a.hasAttribute('download')) return;
        var href = a.getAttribute('href');
        if (!href || href.charAt(0) === '#' || /^(javascript|mailto|tel):/i.test(href)) return;
        if (a.target && a.target !== '_self') return;
        if (a.origin !== location.origin) return;
        if (a.pathname === location.pathname && a.search === location.search && a.hash) return;
        navStart(a.classList.contains('btn') ? a : null, a.getAttribute('data-loader'));
    });
    document.addEventListener('submit', function (e) {
        var f = e.target;
        if (e.defaultPrevented || !f || !f.matches || f.hasAttribute('data-no-loader') || f.hasAttribute('data-loader')) return;
        if ((f.target && f.target !== '_self') || (f.method || '').toLowerCase() === 'dialog') return;
        navStart(e.submitter || null, null);
    });
    window.addEventListener('pageshow', function (e) { if (e.persisted) { releaseAll(); hide(true); } });

    /* Start by itself on every page that includes this file */
    function auto() { watch(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', auto); else auto();

    return { show: show, hide: hide, wrap: wrap, markup: markup, btn: btnBusy, watch: watch, config: config };
})();
