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
    document.querySelectorAll(".toast").forEach(function (t) {
      function close() { t.classList.add("out"); setTimeout(function () { t.remove(); }, 320); }
      t.addEventListener("click", close);
      setTimeout(close, t.classList.contains("error") ? 8000 : 4500);
    });
  });
})();
