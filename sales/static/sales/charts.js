/* Charts screen: draws SVG charts from /manage/charts/data/. No libraries, so it works with no internet. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg", DATA = null, MODE = { products: "revenue" };

  function kes(n) { return "KES " + Math.round(Number(n) || 0).toLocaleString("en-KE"); }
  function num(n) { return Math.round(Number(n) || 0).toLocaleString("en-KE"); }
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function el(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function shortDay(iso) { var d = new Date(iso + "T00:00:00"); return d.toLocaleDateString("en-KE", { day: "numeric", month: "short" }); }
  function longDay(iso) { var d = new Date(iso + "T00:00:00"); return d.toLocaleDateString("en-KE", { weekday: "short", day: "numeric", month: "short", year: "numeric" }); }
  function niceMax(v) {
    if (v <= 0) return 4;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p, n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return n * p;
  }
  function compact(n) { return n >= 1e6 ? (n / 1e6).toFixed(n % 1e6 ? 1 : 0) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n % 1e3 ? 1 : 0) + "k" : String(n); }

  function tooltip(host) {
    var t = host.querySelector(".tip");
    if (!t) { t = document.createElement("div"); t.className = "tip"; t.setAttribute("aria-hidden", "true"); host.appendChild(t); }
    return {
      show: function (x, y, html) {
        t.innerHTML = html; t.classList.add("on");
        var w = t.offsetWidth, hostW = host.clientWidth, left = Math.min(Math.max(x - w / 2, 0), hostW - w);
        t.style.left = left + "px"; t.style.top = Math.max(y - t.offsetHeight - 12, 0) + "px";
      },
      hide: function () { t.classList.remove("on"); }
    };
  }

  /* ---- line chart (one series, hover crosshair) ---- */
  function line(host, rows, label) {
    host.innerHTML = "";
    if (!rows.some(function (r) { return r.value > 0; })) { host.innerHTML = '<div class="empty-note">No sales in this period.</div>'; return; }
    var W = Math.max(host.clientWidth, 280), H = W < 480 ? 220 : 260, m = { l: 48, r: 10, t: 10, b: 26 };
    var max = niceMax(Math.max.apply(null, rows.map(function (r) { return r.value; }))), iw = W - m.l - m.r, ih = H - m.t - m.b;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": label }, host);
    var X = function (i) { return m.l + (rows.length === 1 ? iw / 2 : i * iw / (rows.length - 1)); }, Y = function (v) { return m.t + ih - v / max * ih; };
    for (var g = 0; g <= 4; g++) {
      var v = max * g / 4, y = Y(v);
      el("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, "class": g ? "grid" : "axis" }, svg);
      var tx = el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, svg); tx.textContent = compact(v);
    }
    var step = Math.max(1, Math.ceil(rows.length / (W < 480 ? 4 : 7)));
    rows.forEach(function (r, i) { if (i % step === 0) { var t = el("text", { x: X(i), y: H - 8, "text-anchor": i === 0 ? "start" : "middle" }, svg); t.textContent = shortDay(r.key); } });
    var d = rows.map(function (r, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(r.value).toFixed(1); }).join("");
    el("path", { d: d + "L" + X(rows.length - 1).toFixed(1) + " " + Y(0) + "L" + X(0).toFixed(1) + " " + Y(0) + "Z", "class": "area" }, svg);
    el("path", { d: d, "class": "line" }, svg);
    var cross = el("line", { y1: m.t, y2: m.t + ih, "class": "cross", visibility: "hidden" }, svg);
    var dot = el("circle", { r: 5, "class": "dot", visibility: "hidden" }, svg);
    var tip = tooltip(host);
    var hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", tabindex: 0 }, svg);
    function at(clientX) {
      var b = svg.getBoundingClientRect(), px = (clientX - b.left) * (W / b.width);
      var i = rows.length === 1 ? 0 : Math.round((px - m.l) / iw * (rows.length - 1)); i = Math.max(0, Math.min(rows.length - 1, i));
      var r = rows[i], x = X(i), y = Y(r.value), s = b.width / W;
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible");
      tip.show(x * s, y * s, longDay(r.key) + "<b>" + kes(r.value) + "</b>" + r.count + " sale" + (r.count === 1 ? "" : "s"));
    }
    hit.addEventListener("pointermove", function (e) { at(e.clientX); });
    hit.addEventListener("pointerdown", function (e) { at(e.clientX); });
    hit.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hide(); });
  }

  /* ---- column chart (busy hours) ---- */
  function columns(host, rows, label, fmtLabel) {
    host.innerHTML = "";
    if (!rows.some(function (r) { return r.value > 0; })) { host.innerHTML = '<div class="empty-note">No sales in this period.</div>'; return; }
    var W = Math.max(host.clientWidth, 280), H = 220, m = { l: 48, r: 6, t: 10, b: 26 };
    var max = niceMax(Math.max.apply(null, rows.map(function (r) { return r.value; }))), iw = W - m.l - m.r, ih = H - m.t - m.b, slot = iw / rows.length, bw = Math.max(3, slot - 2);
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": label }, host), tip = tooltip(host);
    for (var g = 0; g <= 4; g++) {
      var v = max * g / 4, y = m.t + ih - v / max * ih;
      el("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, "class": g ? "grid" : "axis" }, svg);
      var tx = el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, svg); tx.textContent = compact(v);
    }
    rows.forEach(function (r, i) {
      var h = r.value / max * ih, x = m.l + i * slot + (slot - bw) / 2, y = m.t + ih - h, rad = Math.min(4, bw / 2, h);
      if (h > 0) el("path", { d: "M" + x + " " + (y + h) + "V" + (y + rad) + "Q" + x + " " + y + " " + (x + rad) + " " + y + "H" + (x + bw - rad) + "Q" + (x + bw) + " " + y + " " + (x + bw) + " " + (y + rad) + "V" + (y + h) + "Z", "class": "bar" }, svg);
      if (i % (W < 480 ? 4 : 3) === 0) { var t = el("text", { x: x + bw / 2, y: H - 8, "text-anchor": "middle" }, svg); t.textContent = fmtLabel(r); }
      var z = el("rect", { x: m.l + i * slot, y: m.t, width: slot, height: ih, fill: "transparent" }, svg);
      var show = function () { var b = svg.getBoundingClientRect(), s = b.width / W; tip.show((x + bw / 2) * s, y * s, esc(r.tip) + "<b>" + kes(r.value) + "</b>" + r.count + " sale" + (r.count === 1 ? "" : "s")); };
      z.addEventListener("pointerenter", show); z.addEventListener("pointerdown", show); z.addEventListener("pointerleave", tip.hide);
    });
  }

  /* ---- horizontal bars ---- */
  function hbars(host, rows, fmt, emptyText) {
    if (!rows.length) { host.innerHTML = '<div class="empty-note">' + esc(emptyText || "Nothing to show for this period.") + "</div>"; return; }
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; })) || 1;
    host.innerHTML = '<div class="hb">' + rows.map(function (r) {
      return '<div class="hb-row" title="' + esc(r.label + ": " + fmt(r.value) + (r.sub ? " (" + r.sub + ")" : "")) + '"><span class="hb-label">' + esc(r.label) +
        '</span><span class="hb-bar"><i style="width:' + (r.value / max * 100).toFixed(1) + '%"></i></span><span class="hb-val">' + esc(fmt(r.value)) + "</span></div>";
    }).join("") + "</div>";
  }

  function table(id, head, rows) {
    var h = $(id); if (!h) return;
    h.innerHTML = rows.length ? "<table><tr>" + head.map(function (x, i) { return '<th class="' + (i ? "num" : "") + '">' + esc(x) + "</th>"; }).join("") + "</tr>" +
      rows.map(function (r) { return "<tr>" + r.map(function (c, i) { return '<td class="' + (i ? "num" : "") + '">' + esc(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table>" : "";
  }

  function render() {
    if (!DATA) return;
    var k = DATA.kpi;
    $("k-total").textContent = kes(k.total); $("k-count").textContent = num(k.count); $("k-avg").textContent = kes(k.average);
    $("k-best").textContent = k.best_day ? shortDay(k.best_day) : "-"; $("k-best-sub").textContent = k.best_day ? kes(k.best_day_total) : "";
    $("k-refunds").textContent = kes(k.refunds);

    var daily = DATA.daily.map(function (r) { return { key: r.day, value: r.total, count: r.count }; });
    line($("c-daily"), daily, "Sales per day, " + shortDay(DATA.from) + " to " + shortDay(DATA.to));
    table("t-daily", ["Day", "Sales (KES)", "Sales count"], DATA.daily.map(function (r) { return [r.day, num(r.total), r.count]; }));

    var byRev = MODE.products === "revenue", src = byRev ? DATA.top_revenue : DATA.top_units;
    hbars($("c-products"), src.map(function (r) {
      var units = r.bottles + (r.measures ? r.measures : 0);
      return { label: r.name, value: byRev ? r.revenue : units, sub: byRev ? num(units) + " sold" : kes(r.revenue) };
    }), byRev ? kes : function (v) { return num(v) + " sold"; }, "No sales in this period.");
    table("t-products", ["Product", "Revenue (KES)", "Bottles / units", "Measures"], src.map(function (r) { return [r.name, num(r.revenue), r.bottles, r.measures]; }));

    columns($("c-hours"), DATA.hourly.map(function (r) { return { value: r.total, count: r.count, hour: r.hour, tip: r.hour + ":00 to " + r.hour + ":59" }; }), "Sales by hour of day", function (r) { return r.hour + ":00"; });
    table("t-hours", ["Hour", "Sales (KES)", "Sales count"], DATA.hourly.filter(function (r) { return r.count; }).map(function (r) { return [r.hour + ":00", num(r.total), r.count]; }));

    hbars($("c-pay"), DATA.payments.map(function (r) { return { label: r.label, value: r.total, sub: r.count + " payments" }; }), kes);
    hbars($("c-cat"), DATA.categories.map(function (r) { return { label: r.label, value: r.total }; }), kes);
    hbars($("c-cash"), DATA.cashiers.map(function (r) { return { label: r.label, value: r.total, sub: r.count + " sales" }; }), kes);

    var ns = $("c-slow");
    ns.innerHTML = DATA.not_selling.length ? "<table><tr><th>Product</th><th class=\"num\">In stock</th></tr>" + DATA.not_selling.map(function (r) { return "<tr><td>" + esc(r.name) + '</td><td class="num">' + num(r.stock) + "</td></tr>"; }).join("") + "</table>" : '<div class="empty-note">Every product in stock sold at least once in this period.</div>';
  }

  function load() {
    var f = $("f-from").value, t = $("f-to").value, host = $("viz");
    host.setAttribute("aria-busy", "true");
    fetch("/manage/charts/data/?from=" + encodeURIComponent(f) + "&to=" + encodeURIComponent(t), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { DATA = d; host.removeAttribute("aria-busy"); render(); })
      .catch(function () { host.removeAttribute("aria-busy"); $("viz-err").hidden = false; });
  }

  document.querySelectorAll("[data-products]").forEach(function (b) {
    b.addEventListener("click", function () {
      MODE.products = b.getAttribute("data-products");
      document.querySelectorAll("[data-products]").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      render();
    });
  });
  var timer; window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(render, 150); });
  document.addEventListener("DOMContentLoaded", load);
})();
