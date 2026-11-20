(function () {
  "use strict";
  var nav = document.getElementById("nav-sidebar");
  if (!nav) return;

  var paths = {
    people: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M21 19v-1a4 4 0 0 0-3-3.9",
    stock: "M21 8l-9-5-9 5v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8",
    payments: "M3 7h18v10H3zM12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.6 2.8-.2-.1a1.8 1.8 0 0 0-1.8 0l-.2.1h-3.2l-.1-.2a1.8 1.8 0 0 0-1.5-1.1h-.2l-1.6-2.8.1-.2a1.7 1.7 0 0 0 0-1.8l-.1-.2V9.3l.2-.1a1.8 1.8 0 0 0 1.1-1.5v-.2l2.8-1.6.2.1a1.7 1.7 0 0 0 1.8 0l.2-.1h3.2l.1.2a1.8 1.8 0 0 0 1.5 1.1h.2l1.6 2.8-.1.2a1.7 1.7 0 0 0 0 1.8l.1.2v3.2z",
    record: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
    list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"
  };
  function iconPath(label) {
    var value = label.toLowerCase();
    if (/user|group|staff|role|permission/.test(value)) return paths.people;
    if (/stock|product|supplier|category|brand|inventory/.test(value)) return paths.stock;
    if (/sale|payment|refund|till|cash|expense/.test(value)) return paths.payments;
    if (/setting|integration|backup|sync/.test(value)) return paths.settings;
    if (/audit|login|approval|history/.test(value)) return paths.record;
    return paths.list;
  }
  function decorate(link) {
    var label = link.textContent.trim();
    if (!label || link.classList.contains("admin-nav-entry")) return;
    link.setAttribute("aria-label", label);
    link.setAttribute("title", label);
    var icon = document.createElement("span");
    icon.className = "admin-nav-entry-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<svg viewBox="0 0 24 24"><path d="' + iconPath(label) + '"/></svg>';
    var text = document.createElement("span");
    text.className = "admin-nav-entry-label";
    text.textContent = label;
    while (link.firstChild) link.removeChild(link.firstChild);
    link.appendChild(icon);
    link.appendChild(text);
    link.classList.add("admin-nav-entry");
  }
  Array.prototype.forEach.call(nav.querySelectorAll(".module caption a, .module th[scope='row'] a"), decorate);

  var button = document.createElement("button");
  button.type = "button";
  button.className = "admin-nav-mode";
  button.innerHTML = '<span class="admin-nav-mode-icon" aria-hidden="true"></span><span class="admin-nav-mode-label"></span>';
  nav.insertBefore(button, nav.firstChild);
  var icon = button.querySelector(".admin-nav-mode-icon");
  var label = button.querySelector(".admin-nav-mode-label");
  var compact = false;
  function paint() {
    document.body.classList.toggle("admin-nav-icons-only", compact);
    icon.textContent = compact ? ">" : "<";
    label.textContent = compact ? "Show text labels" : "Icons only";
    var accessibleLabel = compact ? "Show navigation text labels" : "Show icons only";
    button.setAttribute("aria-label", accessibleLabel);
    button.setAttribute("title", accessibleLabel);
    button.setAttribute("aria-pressed", compact ? "true" : "false");
  }
  button.addEventListener("click", function () {
    compact = !compact;
    paint();
  });
  paint();
})();
