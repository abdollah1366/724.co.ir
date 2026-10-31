/* اسکن بارکد با بارکدخوان یا دوربین (صندوق فروش، میز کار انبار، درخواست کالا، چاپ برچسب).
 *
 * بارکدخوان USB/بلوتوث مثل صفحه‌کلید متن را تایپ و Enter می‌زند؛ کافی است فیلد فوکوس داشته باشد.
 * دوربین: کنار هر <input data-scan> دکمه 📷 ساخته می‌شود. اول از BarcodeDetector مرورگر (کروم اندروید) استفاده می‌شود؛
 * اگر نبود، کتابخانه ZXing از همین سایت بارگذاری می‌شود (بدون CDN خارجی). دوربین فقط روی https یا localhost کار می‌کند.
 * پس از خواندن: مقدار در فیلد گذاشته و رویداد «scan» روی فیلد فرستاده می‌شود؛ اگر data-scan="submit" باشد فرم ارسال می‌شود.
 */
(function () {
  "use strict";
  var FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "data_matrix", "qr_code"];
  var tag = document.querySelector("script[data-zxing]");
  var ZXING_URL = tag ? tag.dataset.zxing : null;

  function beep(ok) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      var ctx = new Ctx();
      var osc = ctx.createOscillator();
      osc.frequency.value = ok === false ? 280 : 1250;
      osc.connect(ctx.destination);
      osc.start();
      setTimeout(function () { osc.stop(); ctx.close(); }, ok === false ? 260 : 90);
    } catch (e) { /* بی‌صدا */ }
    if (navigator.vibrate) navigator.vibrate(ok === false ? [80, 60, 80] : 40);
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (window.ZXing) return resolve();
      var s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function overlay() {
    var box = document.createElement("div");
    box.className = "scan-overlay";
    box.innerHTML = '<div class="scan-box"><video playsinline muted autoplay></video><div class="scan-frame"></div>' +
      '<p class="scan-msg">بارکد یا کد دوبعدی را جلوی دوربین بگیرید…</p>' +
      '<button type="button" class="btn btn-outline scan-close">بستن</button></div>';
    document.body.appendChild(box);
    return box;
  }

  function openCamera(onCode) {
    var box = overlay();
    var video = box.querySelector("video");
    var msg = box.querySelector(".scan-msg");
    var stream = null, stopped = false, reader = null;

    function close() {
      stopped = true;
      if (reader) { try { reader.reset(); } catch (e) {} }
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
      box.remove();
    }
    function done(text) {
      if (stopped) return;
      close();
      beep(true);
      onCode(text);
    }
    box.querySelector(".scan-close").addEventListener("click", close);
    box.addEventListener("click", function (e) { if (e.target === box) close(); });

    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      msg.textContent = "دوربین فقط وقتی کار می‌کند که سایت با https باز شده باشد. از بارکدخوان استفاده کنید.";
      return;
    }
    var constraints = { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false };

    if ("BarcodeDetector" in window) {
      navigator.mediaDevices.getUserMedia(constraints).then(function (s) {
        stream = s;
        video.srcObject = s;
        return window.BarcodeDetector.getSupportedFormats();
      }).then(function (supported) {
        var detector = new window.BarcodeDetector({ formats: FORMATS.filter(function (f) { return supported.indexOf(f) !== -1; }) });
        (function tick() {
          if (stopped) return;
          if (video.readyState < 2) return requestAnimationFrame(tick);
          detector.detect(video).then(function (codes) {
            if (codes.length) done(codes[0].rawValue); else setTimeout(tick, 120);
          }).catch(function () { setTimeout(tick, 250); });
        })();
      }).catch(function (err) { msg.textContent = "دسترسی به دوربین ممکن نشد: " + (err && err.message || err); });
      return;
    }
    if (!ZXING_URL) { msg.textContent = "این مرورگر خواندن بارکد با دوربین را پشتیبانی نمی‌کند."; return; }
    msg.textContent = "در حال آماده‌سازی خواننده بارکد…";
    loadScript(ZXING_URL).then(function () {
      var Z = window.ZXing;
      var hints = new Map();
      hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.CODE_128,
        Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.ITF, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.DATA_MATRIX, Z.BarcodeFormat.QR_CODE]);
      hints.set(Z.DecodeHintType.TRY_HARDER, true);
      reader = new Z.BrowserMultiFormatReader(hints, 250);
      msg.textContent = "بارکد یا کد دوبعدی را جلوی دوربین بگیرید…";
      return reader.decodeFromConstraints(constraints, video, function (result) {
        if (result) done(result.getText());
      });
    }).catch(function (err) { msg.textContent = "دسترسی به دوربین ممکن نشد: " + (err && err.message || err); });
  }

  function attach(input) {
    if (input.dataset.scanReady) return;
    input.dataset.scanReady = "1";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-small btn-outline scan-cam";
    btn.title = "اسکن با دوربین";
    btn.textContent = "📷 دوربین";
    input.insertAdjacentElement("afterend", btn);
    btn.addEventListener("click", function () {
      openCamera(function (text) {
        input.value = text;
        input.dispatchEvent(new CustomEvent("scan", { bubbles: true, detail: text }));
        if (input.dataset.scan === "submit" && input.form) {
          if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
        }
      });
    });
  }

  window.Scanner = { beep: beep, open: openCamera, attach: attach };
  document.querySelectorAll("input[data-scan]").forEach(attach);
})();
