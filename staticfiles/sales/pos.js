/* Cashier sales screen. All selling rules are enforced by the server (sales/services.py); this file only shows the
   sale and asks for input. Esc or closing a box never grants an approval: restricted actions are refused by the
   server until a supervisor or manager has approved them. */
(function () {
  "use strict";
  var KEYMAP = JSON.parse(document.getElementById("keymap").textContent);
  var CAPS = JSON.parse(document.getElementById("caps").textContent);
  var API = "/api/sales/";
  var $ = function (id) { return document.getElementById(id); };
  var scan = $("scan");
  var state = { cart: null, held: 0, sel: 0, lastSale: null, busy: false, heldCount: 0 };
  var locking = false;

  // ---- helpers ---------------------------------------------------------------------------------
  function fmt(n) { return Number(n).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function csrf() { var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  function el(tag, text, cls) { var e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
  function api(method, url, body) {
    var opts = { method: method, credentials: "same-origin", headers: { "X-CSRFToken": csrf() } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (d) { d._status = r.status; return d; })
        .catch(function () { return { ok: false, error: "Unexpected server response.", _status: r.status }; });
    }).catch(function () { return { ok: false, error: "Cannot reach the shop server. Check the network cable, then try again.", _status: 0 }; });
  }
  var chain = Promise.resolve();
  function enqueue(fn) { var p = chain.then(fn); chain = p.catch(function () {}); return p; }   // one change at a time, in scan order

  var flashTimer = null;
  function flash(text, ok) {
    var f = $("flash"); f.textContent = text; f.className = ok ? "ok" : ""; f.hidden = false;
    clearTimeout(flashTimer); flashTimer = setTimeout(function () { f.hidden = true; }, ok ? 2500 : 6000);
  }
  var audio = null;
  function beep() {
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var o = audio.createOscillator(), g = audio.createGain(); o.frequency.value = 220; g.gain.value = 0.08;
      o.connect(g); g.connect(audio.destination); o.start(); setTimeout(function () { o.stop(); }, 180); } catch (e) {}
  }
  function fail(r) { beep(); flash(r.error || "Something went wrong."); }
  function expired() { flash("Your session ended. Signing you out..."); setTimeout(function () { location.href = "/app/pos/"; }, 1500); }
  function lockPos() {
    if (locking) return;
    locking = true;
    enqueue(function () {
      return api("POST", "/api/pos/lock/", {}).then(function (r) {
        if (!r.ok) { locking = false; fail(r); return r; }
        location.replace("/app/pos/?sleep=1&till=" + encodeURIComponent(document.body.getAttribute("data-till")));
        return r;
      });
    });
  }

  // ---- dialogs ---------------------------------------------------------------------------------
  var dialogs = Array.prototype.slice.call(document.querySelectorAll("dialog"));
  function anyOpen() { return dialogs.some(function (d) { return d.open; }); }
  function openD(id, focusId) {
    var d = $(id); if (d.open) return;
    d.showModal();
    var t = focusId ? $(focusId) : d.querySelector("input:not([type=checkbox]),select,button[autofocus],button");
    if (t) { t.focus(); if (t.select && t.tagName === "INPUT") t.select(); }
  }
  function closeD(id) { var d = $(id); if (d.open) d.close(); }
  dialogs.forEach(function (d) {
    d.addEventListener("close", function () { if (!anyOpen()) scan.focus(); });
    d.addEventListener("click", function (e) { if (e.target.closest("[data-close]")) d.close(); });
  });
  function say(id, text) { $(id).textContent = text || ""; }

  // ---- cart ------------------------------------------------------------------------------------
  function lines() { return state.cart ? state.cart.items : []; }
  function selLine() { var l = lines(); return l.length ? l[Math.min(state.sel, l.length - 1)] : null; }

  function render() {
    var c = state.cart, body = $("lines"); body.innerHTML = "";
    $("empty").hidden = lines().length > 0;
    lines().forEach(function (l, i) {
      var tr = document.createElement("tr"); if (i === state.sel) tr.className = "sel";
      tr.appendChild(el("td", String(i + 1), "pk"));
      var name = el("td", l.name);
      if (l.pack) name.appendChild(el("span", " (" + l.pack + " of " + l.units + ")", "pk"));
      if (l.price_changed) name.appendChild(el("span", "price changed", "tag"));
      tr.appendChild(name);
      tr.appendChild(el("td", String(l.quantity), "num"));
      tr.appendChild(el("td", fmt(l.unit_price), "num"));
      tr.appendChild(el("td", Number(l.discount) > 0 ? "-" + fmt(l.discount) : "", "num"));
      tr.appendChild(el("td", fmt(l.line_total), "num"));
      tr.addEventListener("click", function () { state.sel = i; render(); scan.focus(); });
      body.appendChild(tr);
    });
    var sel = body.querySelector("tr.sel"); if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
    $("t-items").textContent = c ? c.count : 0;
    $("t-sub").textContent = c ? fmt(c.subtotal) : "0.00";
    $("t-disc").textContent = c ? fmt(c.discount) : "0.00";
    $("t-total").textContent = c ? fmt(c.total) : "0.00";
    $("cust").textContent = (c && c.customer) || "-";
    $("held").textContent = state.heldCount;
  }

  function showLast(line, errorText) {
    var box = $("last"); box.innerHTML = ""; box.className = "card" + (errorText ? " err" : "");
    if (errorText) { box.appendChild(el("span", errorText, "nm")); return; }
    if (!line) { box.appendChild(el("span", "Scan the next item.", "hint")); return; }
    box.appendChild(el("span", line.name + (line.pack ? " (" + line.pack + ")" : ""), "nm"));
    box.appendChild(el("span", line.quantity + " x " + fmt(line.unit_price) + " = " + fmt(line.line_total), "amt"));
  }

  function applyResponse(r) {
    if (r.cart === undefined) return;
    state.cart = r.cart && r.cart.id ? r.cart : null;
    if (r.added && state.cart) {
      var idx = state.cart.items.findIndex(function (l) { return l.id === r.added; });
      if (idx >= 0) state.sel = idx;
    }
    if (state.sel >= lines().length) state.sel = Math.max(0, lines().length - 1);
    render();
    showLast(selLine());     // the line the cashier is on (or "scan the next item" when the sale is empty)
  }
  function refreshHeldCount() {
    return api("GET", API + "held/").then(function (r) { if (r.ok) { state.heldCount = r.held.length; render(); } return r; });
  }

  // ---- approvals -------------------------------------------------------------------------------
  var apprResolve = null, apprReq = null;
  function askApproval(req) {
    return new Promise(function (resolve) {
      apprResolve = resolve; apprReq = req;
      $("s-appr").textContent = req.label + ". A supervisor or manager must approve.";
      say("e-appr", ""); $("a-secret").value = ""; $("a-reason").value = "";
      openD("d-appr", "a-user");
    });
  }
  $("d-appr").addEventListener("close", function () { if (apprResolve) { var r = apprResolve; apprResolve = null; r(null); } });
  $("f-appr").addEventListener("submit", function (e) {
    e.preventDefault();
    var reason = $("a-reason").value.trim();
    if (!$("a-user").value.trim() || !$("a-secret").value) { say("e-appr", "Enter the supervisor's username and PIN or password."); return; }
    if (!reason) { say("e-appr", "A reason is required."); return; }
    api("POST", "/api/approvals/request/", {
      action: apprReq.action, approver_username: $("a-user").value, secret: $("a-secret").value,
      reason: reason, object_ref: apprReq.object_ref
    }).then(function (r) {
      $("a-secret").value = "";
      if (!r.ok) { say("e-appr", r.error || "Approval refused."); $("a-secret").focus(); return; }
      var resolve = apprResolve; apprResolve = null; closeD("d-appr"); $("a-user").value = "";
      if (resolve) resolve({ token: r.required === false ? "" : r.token, reason: reason });
    });
  });

  /* POST a cart change. If the server says a supervisor must approve, ask for it and send the very same
     request again with the single-use token (and the reason the supervisor gave). */
  function act(path, body) {
    return enqueue(function () {
      return api("POST", API + path, body).then(function (r) {
        if (r.ok) { applyResponse(r); return r; }
        if (r._status === 401) { expired(); return r; }
        if (!r.approval_required) { fail(r); return r; }
        return askApproval(r.approval_required).then(function (ok) {
          if (ok === null) { flash("Cancelled. Nothing was changed.", true); return r; }
          return api("POST", API + path, Object.assign({}, body, { approval_token: ok.token, reason: ok.reason })).then(function (r2) {
            if (r2.ok) { applyResponse(r2); return r2; }
            fail(r2); return r2;
          });
        });
      });
    });
  }

  // ---- scanning --------------------------------------------------------------------------------
  function submitScan() {
    var v = scan.value.trim(); scan.value = ""; if (!v) return;
    var qty = 1, m = v.match(/^(\d{1,4})\*(.+)$/);
    if (m) { qty = parseInt(m[1], 10); v = m[2]; }
    act("cart/add/", { code: v, quantity: qty }).then(function (r) {
      if (r.ok) return;
      showLast(null, r.error || "Not added.");
      if (/not found/i.test(r.error || "") || r._status === 0) { scan.value = v; scan.select(); }
    });
  }
  scan.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); submitScan(); } });

  // ---- selected-line actions -------------------------------------------------------------------
  function needLine() { var l = selLine(); if (!l) { flash("Select an item first (Up / Down)."); beep(); } return l; }

  function openQty() {
    var l = needLine(); if (!l) return;
    $("s-qty").textContent = l.name + " (now " + l.quantity + ")"; $("i-qty").value = l.quantity; say("e-qty", ""); openD("d-qty", "i-qty");
  }
  $("f-qty").addEventListener("submit", function (e) {
    e.preventDefault(); var l = selLine(); if (!l) return;
    var q = $("i-qty").value.trim(); closeD("d-qty");
    if (q === "0") removeSelected(l);
    else act("cart/quantity/", { item_id: l.id, quantity: q });
  });

  function removeSelected(line) {
    var l = line || needLine(); if (!l) return;
    act("cart/remove/", { item_id: l.id, reason: "Removed at the till" });
  }

  function openDiscount() {
    var l = needLine(); if (!l) return;
    $("s-disc").textContent = l.name + ", line total " + fmt(Number(l.line_total) + Number(l.discount));
    $("i-dval").value = ""; say("e-disc", ""); openD("d-disc", "i-dval");
  }
  $("f-disc").addEventListener("submit", function (e) {
    e.preventDefault(); var l = selLine(); if (!l) return;
    var v = $("i-dval").value.trim(); if (v === "") { say("e-disc", "Enter a value (0 removes the discount)."); return; }
    closeD("d-disc");
    var body = { item_id: l.id }; body[$("i-dmode").value === "PERCENT" ? "percent" : "amount"] = v;
    act("cart/discount/", body);
  });

  function openPrice() {
    var l = needLine(); if (!l) return;
    $("s-price").textContent = l.name + " (now " + fmt(l.unit_price) + ", shelf price " + fmt(l.list_price) + ")";
    $("i-price").value = l.unit_price; say("e-price", ""); openD("d-price", "i-price");
  }
  $("f-price").addEventListener("submit", function (e) {
    e.preventDefault(); var l = selLine(); if (!l) return;
    closeD("d-price");
    act("cart/price/", { item_id: l.id, price: $("i-price").value.trim() });
  });

  // ---- customer, hold, retrieve ----------------------------------------------------------------
  function openCust() { $("i-cust").value = (state.cart && state.cart.customer) || ""; say("e-cust", ""); openD("d-cust", "i-cust"); }
  $("f-cust").addEventListener("submit", function (e) {
    e.preventDefault(); var name = $("i-cust").value.trim(); closeD("d-cust");
    act("cart/customer/", { name: name });
  });

  function openHold() {
    if (!CAPS.hold) { flash("You are not allowed to hold sales."); return; }
    if (!state.cart || !lines().length) { flash("There is nothing to hold."); return; }
    $("i-hold").value = state.cart.customer || ""; say("e-hold", ""); openD("d-hold", "i-hold");
  }
  $("f-hold").addEventListener("submit", function (e) {
    e.preventDefault(); closeD("d-hold");
    act("cart/hold/", { label: $("i-hold").value.trim() }).then(function (r) {
      if (r.ok) { showLast(null); flash("Sale held.", true); refreshHeldCount(); }
    });
  });

  // Keyboard-driven lists (search results, held sales): Up / Down / Enter.
  function setupList(dialogId, tbodyId, onPick) {
    var dlg = $(dialogId), body = $(tbodyId), items = [], idx = 0;
    function paint() {
      Array.prototype.forEach.call(body.children, function (tr, i) { tr.classList.toggle("sel", i === idx); });
      var s = body.querySelector("tr.sel"); if (s && s.scrollIntoView) s.scrollIntoView({ block: "nearest" });
    }
    dlg.addEventListener("keydown", function (e) {
      if (!items.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(items.length - 1, idx + 1); paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(0, idx - 1); paint(); }
      else if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); onPick(items[idx]); }
    });
    return {
      fill: function (list, cellsFor) {
        items = list; idx = 0; body.innerHTML = "";
        list.forEach(function (it, i) {
          var tr = document.createElement("tr"); tr.className = "pick";
          cellsFor(it).forEach(function (c) { tr.appendChild(c); });
          tr.addEventListener("click", function () { idx = i; onPick(it); });
          body.appendChild(tr);
        });
        paint();
      }
    };
  }

  var heldList = setupList("d-held", "held-rows", function (h) {
    closeD("d-held");
    act("held/resume/", { sale_id: h.id }).then(function (r) { if (r.ok) { flash("Sale retrieved.", true); refreshHeldCount(); } });
  });
  function openRetrieve() {
    if (!CAPS.hold) { flash("You are not allowed to retrieve held sales."); return; }
    say("e-held", "");
    api("GET", API + "held/").then(function (r) {
      if (!r.ok) { if (r._status === 401) return expired(); return fail(r); }
      state.heldCount = r.held.length; render();
      if (!r.held.length) { flash("No held sales on this till.", true); return; }
      heldList.fill(r.held, function (h) {
        var when = h.held_at ? new Date(h.held_at).toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" }) : "";
        return [el("td", h.label || "(no label)"), el("td", h.cashier), el("td", when), el("td", h.items + " item" + (h.items === 1 ? "" : "s"), "num"), el("td", fmt(h.total), "num")];
      });
      openD("d-held", "d-held");
    });
  }

  // ---- search ----------------------------------------------------------------------------------
  var searchList = setupList("d-search", "results", function (it) {
    closeD("d-search");
    act("cart/add/", { product_id: it.product_id, quantity: 1 });
  });
  var searchSeq = 0, searchTimer = null;
  function openSearch() {
    $("q").value = scan.value.trim(); scan.value = ""; say("e-search", ""); $("results").innerHTML = ""; openD("d-search", "q");
    if ($("q").value.length >= 2) runSearch();
  }
  function runSearch() {
    var q = $("q").value.trim(), n = ++searchSeq;
    if (q.length < 2) { searchList.fill([], function () { return []; }); return; }
    api("GET", "/api/catalog/search/?q=" + encodeURIComponent(q)).then(function (r) {
      if (n !== searchSeq) return;
      if (!r.ok) { say("e-search", r.error || "Search failed."); return; }
      say("e-search", r.items.length ? "" : "Nothing found.");
      searchList.fill(r.items, function (it) {
        var nm = el("td", it.name); if (it.brand) nm.appendChild(el("span", "  " + it.brand, "pk"));
        return [nm, el("td", it.stock <= 0 ? "Out of stock" : "", "pk"), el("td", fmt(it.price), "num")];
      });
    });
  }
  $("q").addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 150); });

  // ---- payment and completing ------------------------------------------------------------------
  function paidValue() { var v = parseFloat(($("i-paid").value || "").replace(/,/g, "")); return isNaN(v) ? 0 : v; }
  function paintChange() {
    var total = state.cart ? Number(state.cart.total) : 0, change = paidValue() - total;
    $("p-change").textContent = change >= 0 ? fmt(change) : "short " + fmt(-change);
  }
  function openPay() {
    if (!CAPS.pay) { flash("You are not allowed to accept payments."); return; }
    if (!state.cart || !lines().length) { flash("Scan an item first."); beep(); return; }
    $("p-total").textContent = fmt(state.cart.total); $("i-paid").value = state.cart.total; say("e-pay", "");
    $("b-pay").disabled = false; paintChange(); openD("d-pay", "i-paid");
  }
  $("i-paid").addEventListener("input", paintChange);
  $("f-pay").addEventListener("submit", function (e) {
    e.preventDefault();
    if (state.busy || !state.cart) return;
    state.busy = true; $("b-pay").disabled = true;
    enqueue(function () { return api("POST", API + "checkout/cash/", { tendered: $("i-paid").value.trim() }); }).then(function (r) {
      state.busy = false; $("b-pay").disabled = false;
      if (r.ok) { saleDone(r); return; }
      if (r._status === 401) return expired();
      say("e-pay", r.error || "The sale could not be completed.");
      api("GET", API + "cart/").then(function (c) { if (c.ok) applyResponse(c); });   // show what is really in the sale
    });
  });

  function saleDone(r) {
    state.lastSale = { id: r.sale_id, number: r.number, originalPrinted: false };
    closeD("d-pay");
    $("s-done").textContent = "Receipt " + r.number + " | Total " + fmt(r.total);
    $("done-change").textContent = fmt(r.change);
    var auto = autoPrint(); $("c-auto").checked = auto;
    $("done-print").textContent = auto ? "Printing the receipt..." : "Press " + KEYMAP.print + " to print the receipt.";
    applyResponse({ cart: r.cart }); showLast(null);
    openD("d-done", "b-new");
    if (auto) printLast();
  }
  function autoPrint() { try { return localStorage.getItem("pos-autoprint") !== "0"; } catch (e) { return true; } }
  $("c-auto").addEventListener("change", function () { try { localStorage.setItem("pos-autoprint", $("c-auto").checked ? "1" : "0"); } catch (e) {} });
  $("b-new").addEventListener("click", function () { closeD("d-done"); showLast(null); });
  $("b-print-done").addEventListener("click", function () { printLast(); });

  // ---- printing --------------------------------------------------------------------------------
  /* The first print is the ORIGINAL (once, by the selling cashier). Anything after that is a marked duplicate
     that needs the reprint permission. The server decides; the screen just asks and prints what comes back. */
  function printLast() {
    var s = state.lastSale;
    if (!s) { flash("No sale to print yet."); return; }
    var first = !s.originalPrinted, boxId = $("d-done").open ? "done-print" : null;
    var call = first ? api("GET", API + "receipt/" + s.id + "/") : api("POST", API + "receipt/" + s.id + "/reprint/", {});
    enqueue(function () { return call; }).then(function (r) {
      if (!r.ok) {
        var msg = r.error || "Could not print.";
        if (first && /already printed/i.test(msg)) s.originalPrinted = true;
        if (boxId) say(boxId, msg); else fail(r);
        beep(); return;
      }
      s.originalPrinted = true;
      PosPrint.print(r.lines, r.paper_mm);
      var done = first ? "Receipt sent to the printer." : "Duplicate sent to the printer.";
      if (boxId) say(boxId, done + (CAPS.reprint ? "" : " Another copy needs a supervisor.")); else flash(done, true);
    });
  }

  // ---- shift summary ---------------------------------------------------------------------------
  function openShift() {
    say("e-shift", "");
    api("GET", API + "shift/summary/").then(function (r) {
      if (!r.ok) { if (r._status === 401) return expired(); return fail(r); }
      var s = r.shift;
      var box = $("shift-stats"); box.innerHTML = "";
      [["Opening float", fmt(s.opening_float)], ["Sales made", String(s.sales_count)], ["Sales total", fmt(s.sales_total)],
       ["Cash sales", fmt(s.cash_sales)], ["Cash expected in drawer", fmt(s.cash_in_drawer_expected)]].forEach(function (p) {
        var row = el("div", undefined, "stat"); row.appendChild(el("span", p[0])); row.appendChild(el("b", p[1])); box.appendChild(row);
      });
      openD("d-shift", "d-shift");
    });
  }

  // ---- supervisor menu -------------------------------------------------------------------------
  function superAction(kind) {
    closeD("d-super");
    if (kind === "remove") removeSelected();
    else if (kind === "price") openPrice();
    else if (kind === "discount") openDiscount();
    else if (kind === "void") {
      if (!state.cart) { flash("There is no sale to void."); return; }
      act("cart/void/", { reason: "Sale cancelled at the till" }).then(function (r) { if (r.ok) { showLast(null); flash("Sale voided.", true); } });
    }
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-sup]"), function (b) {
    b.addEventListener("click", function () { superAction(b.getAttribute("data-sup")); });
  });
  $("d-super").addEventListener("keydown", function (e) {
    var map = { "1": "remove", "2": "price", "3": "discount", "4": "void" };
    if (map[e.key]) { e.preventDefault(); superAction(map[e.key]); }
  });

  // ---- keyboard --------------------------------------------------------------------------------
  var byKey = {}; Object.keys(KEYMAP).forEach(function (a) { byKey[KEYMAP[a]] = a; });
  function combo(e) {
    var k;
    if (/^Key[A-Z]$/.test(e.code || "")) k = e.code.slice(3); else if (/^Digit\d$/.test(e.code || "")) k = e.code.slice(5); else k = e.key;
    if (!k) return "";
    if (k.length === 1) k = k.toUpperCase();
    var m = []; if (e.ctrlKey) m.push("Ctrl"); if (e.altKey) m.push("Alt"); if (e.shiftKey) m.push("Shift"); m.push(k);
    return m.join("+");
  }
  function run(action) {
    switch (action) {
      case "help": openD("d-help", "d-help"); break;
      case "search": openSearch(); break;
      case "scan": scan.focus(); scan.select(); break;
      case "quantity": openQty(); break;
      case "hold": openHold(); break;
      case "retrieve": openRetrieve(); break;
      case "discount": openDiscount(); break;
      case "payment": case "complete": openPay(); break;
      case "print": printLast(); break;
      case "shift": openShift(); break;
      case "supervisor": openD("d-super", "d-super"); break;
      case "remove_line": removeSelected(); break;
      case "customer": openCust(); break;
      case "lock": lockPos(); break;
    }
  }
  document.addEventListener("keydown", function (e) {
    if (e.isComposing) return;
    if (combo(e) === "Ctrl+Alt+L") { e.preventDefault(); lockPos(); return; }
    var action = byKey[combo(e)];
    if (!anyOpen() && (e.key === "ArrowDown" || e.key === "ArrowUp") && !e.ctrlKey && !e.altKey) {
      if (lines().length) { e.preventDefault(); state.sel = Math.max(0, Math.min(lines().length - 1, state.sel + (e.key === "ArrowDown" ? 1 : -1))); render(); showLast(selLine()); }
      return;
    }
    if (!anyOpen() && e.key === "Escape") { scan.value = ""; return; }
    if (!action) return;
    if (action === "remove_line" && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") && e.target.value !== "") return;  // normal editing
    e.preventDefault();   // stops the browser acting on F5 (refresh), F11 (full screen) and so on
    if (anyOpen()) {
      if (action === "complete" && $("d-pay").open) $("f-pay").requestSubmit();
      else if (action === "print" && $("d-done").open) printLast();
      else if (action === "help" && $("d-help").open) closeD("d-help");
      return;
    }
    run(action);
  }, true);

  Array.prototype.forEach.call(document.querySelectorAll("[data-act]"), function (b) {
    b.addEventListener("click", function () { run(b.getAttribute("data-act")); });
  });
  $("b-lock").addEventListener("click", lockPos);
  window.addEventListener("winespos-lock-request", lockPos);
  document.addEventListener("click", function (e) {
    if (!anyOpen() && !e.target.closest("button,input,select,textarea,a,tr")) scan.focus();
  });
  window.addEventListener("focus", function () { if (!anyOpen()) scan.focus(); });

  $("b-logout").addEventListener("click", function () {
    if (lines().length && !window.confirm("There is a sale in progress. It stays saved on this till. Log out anyway?")) { scan.focus(); return; }
    api("POST", "/api/auth/logout/", {}).then(function () { location.href = "/app/pos/"; });
  });

  // ---- start -----------------------------------------------------------------------------------
  function tick() { $("clock").textContent = new Date().toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" }); }
  tick(); setInterval(tick, 20000);
  api("GET", API + "cart/").then(function (r) {
    if (r._status === 401 || r._status === 403) return expired();
    if (r.ok) { applyResponse(r); if (selLine()) showLast(selLine()); }
    else flash(r.error || "Could not load the sale.");
    refreshHeldCount();
    scan.focus();
  });
})();
