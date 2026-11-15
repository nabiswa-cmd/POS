/* Prints a receipt from the lines the server laid out (sales.receipt_text). A hidden frame is used so the
   till screen stays where it is. 58 mm paper is 32 characters wide, 80 mm is 48; the font is sized to fit. */
window.PosPrint = (function () {
  function print(lines, paperMm) {
    var chars = Math.max.apply(null, lines.map(function (l) { return l.length; }).concat([32]));
    var usablePx = (paperMm - 4) / 25.4 * 96;                 // paper width minus 2 mm margins, in CSS pixels
    var fontPx = Math.max(8, Math.floor(usablePx / (chars * 0.62) * 10) / 10);
    var f = document.createElement("iframe");
    f.setAttribute("aria-hidden", "true");
    f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
    document.body.appendChild(f);
    var d = f.contentWindow.document;
    d.open();
    d.write("<!doctype html><html><head><meta charset='utf-8'><title>Receipt</title><style>" +
      "@page{size:" + paperMm + "mm auto;margin:2mm}html,body{margin:0;padding:0;background:#fff;color:#000}" +
      "pre{margin:0;font:" + fontPx + "px/1.3 Consolas,'Lucida Console','Courier New',monospace;white-space:pre}" +
      "</style></head><body><pre id='r'></pre></body></html>");
    d.close();
    d.getElementById("r").textContent = lines.join("\n");
    setTimeout(function () {
      try { f.contentWindow.focus(); f.contentWindow.print(); } catch (e) {}
      setTimeout(function () { if (f.parentNode) f.parentNode.removeChild(f); }, 60000);
    }, 150);
  }
  return { print: print };
})();
