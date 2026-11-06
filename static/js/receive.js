/* فرم «ورود کالا از شرکت»: اسکن یا جست‌وجوی نام ← ردیف با بچ، انقضا، تعداد، قیمت، محل و کد اصالت.
 * کد دوبعدی جعبه (GS1) کالا، بچ، انقضا و سریال را پر می‌کند؛ اسکن دوباره همان کالا و بچ تعداد را بالا می‌برد.
 * هنگام ارسال، ردیف‌ها به شکل JSON در فیلد items می‌روند. پس از خطای سرور، ردیف‌ها از #receive-rows بازسازی می‌شوند.
 */
(function () {
  "use strict";
  var form = document.getElementById("receive-form");
  if (!form) return;
  var input = form.querySelector("[data-receive-scan]");
  var tbody = form.querySelector("[data-rows]");
  var msg = form.querySelector("[data-msg]");
  var picks = form.querySelector("[data-picks]");
  var whSelect = form.querySelector("[name=warehouse]");
  var locations = JSON.parse(document.getElementById("receive-locations").textContent);
  var rows = [];
  var fa = new Intl.NumberFormat("fa-IR");

  function toLatin(s) {
    return String(s == null ? "" : s).replace(/[۰-۹]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹".indexOf(d); })
      .replace(/[٠-٩]/g, function (d) { return "٠١٢٣٤٥٦٧٨٩".indexOf(d); }).replace(/[,٬\s]/g, "");
  }
  function num(s) { var n = parseInt(toLatin(s), 10); return isNaN(n) ? 0 : n; }
  function say(text, ok) {
    msg.className = text ? "alert " + (ok ? "alert-info" : "alert-error") : "";
    msg.textContent = text || "";
  }
  function locationsFor(info) {
    return locations.filter(function (l) { return String(l.wh) === whSelect.value && l.cond === info.cond; });
  }
  function el(tag, attrs) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function field(row, key, attrs, cell) {
    var i = el("input", attrs);
    i.setAttribute("data-f", key);
    i.value = row[key] == null ? "" : row[key];
    i.addEventListener("input", function () { row[key] = i.value; total(); });
    cell.appendChild(i);
    return i;
  }
  function total() {
    var sum = 0;
    rows.forEach(function (r) { sum += num(r.quantity) * num(r.unit_cost); });
    form.querySelector("[data-total]").textContent = fa.format(sum);
    tbody.querySelectorAll("tr").forEach(function (tr, i) {
      var r = rows[i];
      if (r) tr.querySelector("[data-line-total]").textContent = fa.format(num(r.quantity) * num(r.unit_cost));
    });
  }
  function render() {
    tbody.innerHTML = "";
    rows.forEach(function (r, idx) {
      var info = r.info;
      var tr = document.createElement("tr");
      var c = [];
      for (var k = 0; k < 9; k++) { c.push(document.createElement("td")); tr.appendChild(c[k]); }
      c[0].textContent = info.name;
      if (info.cond !== "room") { var cond = el("div", { "class": "muted small" }); cond.textContent = info.cond_label; c[0].appendChild(cond); }
      if (info.track_batch || info.track_serial) {
        field(r, "batch", { type: "text", maxlength: "60", dir: "ltr", required: "", placeholder: info.track_serial ? "سریال" : "بچ" }, c[1]);
      } else { c[1].textContent = "—"; }
      if (info.track_expiry) {
        field(r, "expiry", { type: "text", dir: "ltr", size: "10", required: "", placeholder: "۱۴۰۶/۰۵" }, c[2]);
      } else { c[2].textContent = "—"; }
      field(r, "quantity", { type: "text", inputmode: "numeric", "class": "qty-input", required: "" }, c[3]);
      field(r, "unit_cost", { type: "text", inputmode: "numeric", size: "10", required: "" }, c[4]);
      c[5].className = "num"; c[5].setAttribute("data-line-total", "");
      var locs = locationsFor(info);
      if (!locs.length) {
        c[6].innerHTML = "<span class='alert-error small'>محل مناسب تعریف نشده</span>";
      } else if (locs.length === 1) {
        c[6].textContent = locs[0].name; r.location = locs[0].id;
      } else {
        var sel = el("select");
        locs.forEach(function (l) { var o = el("option", { value: l.id }); o.textContent = l.name; sel.appendChild(o); });
        if (r.location && locs.some(function (l) { return l.id === Number(r.location); })) sel.value = r.location; else r.location = locs[0].id;
        sel.addEventListener("change", function () { r.location = Number(sel.value); });
        c[6].appendChild(sel);
      }
      if (info.auth) {
        field(r, "auth_code", { type: "text", maxlength: "80", dir: "ltr", size: "12", required: "", placeholder: "کد UID / رهگیری" }, c[7]);
        var lab = el("label", { "class": "small" });
        var cb = el("input", { type: "checkbox" });
        cb.checked = !!r.auth_ok;
        cb.addEventListener("change", function () { r.auth_ok = cb.checked; });
        lab.appendChild(cb); lab.appendChild(document.createTextNode(" اصالت استعلام و تأیید شد"));
        c[7].appendChild(lab);
      } else {
        field(r, "auth_code", { type: "text", maxlength: "80", dir: "ltr", size: "12", placeholder: "اختیاری" }, c[7]);
      }
      var del = el("button", { type: "button", "class": "link-button" });
      del.textContent = "حذف";
      del.addEventListener("click", function () { rows.splice(idx, 1); render(); });
      c[8].appendChild(del);
      if (idx === 0 && r.fresh) { tr.classList.add("scan-flash"); r.fresh = false; }
      tbody.appendChild(tr);
    });
    form.querySelector("[data-empty]").hidden = rows.length > 0;
    total();
  }
  function addRow(info) {
    var batch = info.track_serial ? (info.serial || info.batch || "") : (info.batch || "");
    var key = info.id + ":" + batch;
    var step = info.factor || 1;
    var existing = !info.track_serial && rows.filter(function (r) { return r.info.id === info.id && (r.batch || "") === batch; })[0];
    if (existing) {
      existing.quantity = num(existing.quantity) + step;
      rows.splice(rows.indexOf(existing), 1);
      rows.unshift(existing);
      existing.fresh = true;
    } else {
      rows.unshift({ info: info, key: key, product: info.id, batch: batch, expiry: (info.expiry || "").replace(/-/g, "/"),
                     quantity: info.track_serial ? 1 : step, unit_cost: info.last_cost || "",
                     auth_code: info.track_serial ? "" : (info.serial || ""), auth_ok: false, fresh: true });
    }
    say("«" + info.name + "»" + (batch ? " بچ " + batch : "") + " +" + (info.track_serial ? 1 : step), true);
    if (window.Scanner) Scanner.beep(true);
    render();
  }
  function lookup(text) {
    text = (text || "").trim();
    picks.hidden = true; picks.innerHTML = "";
    if (!text) return;
    fetch(form.dataset.lookupUrl + "?q=" + encodeURIComponent(text), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var res = d.results || [];
        if (!res.length) { say("کالایی با «" + text + "» پیدا نشد. اگر کالای جدید است، اول آن را در پنل مدیریت ← محصولات تعریف کنید.", false); if (window.Scanner) Scanner.beep(false); return; }
        if (res.length === 1) { addRow(res[0]); return; }
        say("چند کالا پیدا شد؛ یکی را انتخاب کنید:", true);
        res.forEach(function (info) {
          var li = document.createElement("li");
          var b = el("button", { type: "button", "class": "btn btn-small btn-outline" });
          b.textContent = info.name + (info.sku ? " (" + info.sku + ")" : "");
          b.addEventListener("click", function () { picks.hidden = true; addRow(info); input.focus(); });
          li.appendChild(b); picks.appendChild(li);
        });
        picks.hidden = false;
      })
      .catch(function () { say("ارتباط با سرور برقرار نشد.", false); });
  }
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); lookup(input.value); input.value = ""; }
  });
  input.addEventListener("scan", function (e) { lookup(e.detail); input.value = ""; input.focus(); });
  whSelect.addEventListener("change", render);
  form.addEventListener("submit", function (e) {
    if (!rows.length) { e.preventDefault(); say("هنوز کالایی اضافه نشده است.", false); return; }
    var missing = rows.filter(function (r) { return r.info.auth && !(r.auth_code && r.auth_ok); })[0];
    if (missing) { e.preventDefault(); say("برای «" + missing.info.name + "» کد اصالت را وارد و تیک تأیید اصالت را بزنید.", false); return; }
    form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; b.textContent = "در حال ثبت…"; });
    form.querySelector("[name=items]").value = JSON.stringify(rows.map(function (r) {
      return { product: r.product, batch: r.batch || "", expiry: r.expiry || "", quantity: num(r.quantity), unit_cost: num(r.unit_cost),
               location: r.location || null, auth_code: r.auth_code || "", auth_ok: !!(r.info.auth && r.auth_ok) };
    }));
  });
  JSON.parse(document.getElementById("receive-rows").textContent).forEach(function (r) {
    rows.push({ info: r.info, product: r.product, batch: r.batch || "", expiry: r.expiry || "", quantity: r.quantity || "",
                unit_cost: r.unit_cost == null ? r.info.last_cost : r.unit_cost, location: r.location || null,
                auth_code: r.auth_code || "", auth_ok: !!r.auth_ok });
  });
  render();
})();
