(function () {
  "use strict";
  var deferredPrompt = null;
  var installButtons = document.querySelectorAll("[data-pwa-install]");

  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("/service-worker.js", { scope: "/" })
      .catch(function (error) { console.error("Could not register the POS offline fallback.", error); });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    Array.prototype.forEach.call(installButtons, function (button) { button.hidden = false; });
  });

  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    Array.prototype.forEach.call(installButtons, function (button) { button.hidden = true; });
  });

  Array.prototype.forEach.call(installButtons, function (button) {
    button.addEventListener("click", function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function () {
        deferredPrompt = null;
        Array.prototype.forEach.call(installButtons, function (installButton) { installButton.hidden = true; });
      });
    });
  });
})();
