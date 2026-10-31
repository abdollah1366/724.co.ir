/* فهرست اسکنی برای صفحه‌های انبار: هر اسکن کالا را (با بچ، اگر کد دوبعدی یا برچسب بچ بود) به جدول اضافه می‌کند؛
 * اسکن دوباره همان کالا تعداد را یکی بالا می‌برد. هنگام ارسال فرم، فهرست به شکل JSON در فیلد items گذاشته می‌شود.
 * نشانه‌گذاری: <form data-scan-list data-scan-url="..."> با <input data-scan> ، <tbody data-rows> و <input type=hidden name=items>.
 */
(function () {
  "use strict";
  document.querySelectorAll("form[data-scan-list]").forEach(function (form) {
    var input = form.querySelector("input[data-scan]");
    var tbody = form.querySelector("[data-rows]");
    var hidden = form.querySelector("input[name=items]");
    var msg = form.querySelector("[data-msg]");
    var whSelect = form.querySelector("[name=warehouse]");
    var rows = [];

    function say(text, ok) {
      if (!msg) return;
      msg.className = text ? "alert " + (ok ? "alert-info" : "alert-error") : "";
      msg.textContent = text || "";
    }
    function render() {
      tbody.innerHTML = "";
      rows.forEach(function (r, i) {
        var tr = document.createElement("tr");
        tr.innerHTML = "<td></td><td></td><td></td><td class='num'></td><td><input type='number' min='0' class='qty-input'></td>" +
          "<td><button type='button' class='link-button'>حذف</button></td>";
        tr.children[0].textContent = r.name;
        tr.children[1].textContent = r.batch || "— (به ترتیب انقضا)";
        tr.children[2].textContent = r.expiry || "";
        tr.children[3].textContent = r.on_hand == null ? "" : r.on_hand;
        var q = tr.querySelector("input");
        q.value = r.quantity;
        q.addEventListener("change", function () { r.quantity = Math.max(0, parseInt(q.value || "0", 10)); });
        tr.querySelector("button").addEventListener("click", function () { rows.splice(i, 1); render(); });
        if (i === 0) tr.classList.add("scan-flash");
        tbody.appendChild(tr);
      });
      var empty = form.querySelector("[data-empty]");
      if (empty) empty.hidden = rows.length > 0;
    }
    function add(code) {
      code = (code || "").trim();
      if (!code) return;
      var url = form.dataset.scanUrl + "?code=" + encodeURIComponent(code) + (whSelect ? "&warehouse=" + whSelect.value : "");
      fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.error) { say(d.message || "کالا پیدا نشد: " + code, false); Scanner.beep(false); return; }
        var key = d.product.id + ":" + (d.batch_id || "");
        var existing = rows.filter(function (r) { return r.key === key; })[0];
        var step = d.factor || 1;
        if (existing) {
          existing.quantity += step;
          rows.splice(rows.indexOf(existing), 1);
          rows.unshift(existing);
        } else {
          rows.unshift({ key: key, product: d.product.id, batch_id: d.batch_id, name: d.product.name, batch: d.batch_id ? d.batch : "",
                         expiry: d.expiry || "", on_hand: d.on_hand, quantity: step });
        }
        say("«" + d.product.name + "» " + (d.batch_id ? "بچ " + d.batch + " " : "") + "+" + step, true);
        Scanner.beep(true);
        render();
      }).catch(function () { say("ارتباط با سرور برقرار نشد.", false); });
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); add(input.value); input.value = ""; }
    });
    input.addEventListener("scan", function (e) { add(e.detail); input.value = ""; input.focus(); });
    if (whSelect) whSelect.addEventListener("change", function () {
      if (rows.length && !confirm("با تغییر انبار، فهرست اسکن‌شده پاک شود؟")) return;
      rows = []; render();
    });
    form.addEventListener("submit", function (e) {
      if (!rows.length) { e.preventDefault(); say("هنوز کالایی اسکن نشده است.", false); return; }
      hidden.value = JSON.stringify(rows.map(function (r) { return { product: r.product, batch: r.batch_id, quantity: r.quantity }; }));
    });
    render();
  });
})();
