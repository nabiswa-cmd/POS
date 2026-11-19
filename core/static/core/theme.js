/* Light / dark switch. Dark is the default; the choice is remembered on this PC. */
(function () {
  var KEY = "pos-theme", root = document.documentElement;
  function read() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function write(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  root.setAttribute("data-theme", read() === "light" ? "light" : "dark");
  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll("[data-theme-toggle]");
    function paint() {
      var light = root.getAttribute("data-theme") === "light";
      buttons.forEach(function (b) { var l = b.querySelector(".t-label"); if (l) l.textContent = light ? "Light" : "Dark"; b.setAttribute("aria-pressed", light ? "true" : "false"); });
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
        root.setAttribute("data-theme", next); write(next); paint();
      });
    });
    paint();
    /* Phones: tables on manager pages become stacked cards (labels taken from the header cells). */
    if (document.body.classList.contains("has-shell")) {
      document.querySelectorAll("main table").forEach(function (t) {
        var rows = Array.prototype.slice.call(t.querySelectorAll("tr"));
        var headRow = rows.filter(function (r) { return r.querySelector("th") && !r.querySelector("td"); })[0];
        if (!headRow) return;
        var heads = Array.prototype.map.call(headRow.children, function (h) { return h.textContent.trim(); });
        t.classList.add("stack");
        headRow.classList.add("hdr");
        rows.forEach(function (r) {
          if (r === headRow) return;
          Array.prototype.forEach.call(r.children, function (c, i) { if (heads[i] && !c.hasAttribute("colspan")) c.setAttribute("data-label", heads[i]); });
        });
      });
    }
    document.querySelectorAll(".toast").forEach(function (t) {
      function close() { t.classList.add("out"); setTimeout(function () { t.remove(); }, 320); }
      t.addEventListener("click", close);
      setTimeout(close, t.classList.contains("error") ? 8000 : 4500);
    });
  });
})();
