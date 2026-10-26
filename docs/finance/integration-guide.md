# راهنمای اتصال ماژول‌ها به مالی

## گذرگاه رویداد (`apps/core/events.py`)

الگو: transactional outbox. تولیدکننده داخل تراکنش خودش `events.publish(...)` را صدا می‌زند؛ رویداد در جدول `core_outboxevent` همراه داده ماژول ذخیره می‌شود. اگر تراکنش برگردد، رویداد هم نیست.

هر رویداد دارد:

| فیلد | توضیح |
|---|---|
| `event_id` | UUID یکتا |
| `event_type` | مثل `ehr.encounter.signed` |
| `schema_version` | نسخه طرح payload (فعلاً ۱) |
| `occurred_at` | زمان رخداد |
| `source_type`، `source_id` | مرجع سند منبع |
| `dedupe_key` | برای چند رویداد از یک منبع (مثلاً شناسه پرداخت) |
| `correlation_id` | شناسه درخواست وب (`X-Request-ID`) برای ردیابی در لاگ |
| `status`، `attempts`، `last_error`، `result_ref` | وضعیت پردازش |

یکتایی (`event_type`، `source_type`، `source_id`، `dedupe_key`) انتشار تکراری را بی‌اثر می‌کند. اگر هیچ مصرف‌کننده‌ای ثبت نشده باشد، رویدادی ذخیره نمی‌شود.

**پردازش:** بلافاصله پس از commit (اگر `EVENTS_DISPATCH_INLINE` روشن باشد؛ خطا بلعیده می‌شود تا درخواست کاربر ماژول درمانی خراب نشود) و هر ۵ دقیقه با `python manage.py dispatch_events` (Cron یا systemd timer). هر handler داخل savepoint اجرا می‌شود.

**خطا:** تلاش مجدد با فاصله نمایی (۲، ۴، ۸، ... دقیقه تا سقف `EVENTS_MAX_BACKOFF_MINUTES`). پس از `EVENTS_MAX_ATTEMPTS` (۸) تلاش، وضعیت «متوقف» (dead letter). رویدادهای ناموفق و متوقف در صفحه «وضعیت اتصال‌ها» با متن خطا دیده می‌شوند و با دکمه «تلاش مجدد» (مجوز `core.retry_outboxevent`) به صف برمی‌گردند. در لاگ فقط شناسه، نوع و نوع خطا نوشته می‌شود، نه payload.

**قابلیت خاموش:** اگر قابلیت handler خاموش باشد، رویداد «در انتظار» می‌ماند (بدون شمارش تلاش) و با روشن شدن قابلیت پردازش می‌شود. پس روشن کردن دوباره idempotent است و چیزی گم نمی‌شود.

## رویدادهای موجود

| رویداد | تولیدکننده | مصرف مالی | قابلیت |
|---|---|---|---|
| `ehr.encounter.signed` | `apps/ehr/clinical.sign` | صورتحساب ویزیت (پیش‌نویس، یا نهایی با `auto_post_encounter_invoices`) | `finance.integration.clinic` |
| `ehr.therapysession.signed` | `apps/ehr/rehab.sign_session` | صورتحساب جلسه حاضر با کد خدمت | `finance.integration.rehab` |
| `telemed.payment.paid` | پرداخت درگاه یا ثبت دستی | صورتحساب نهایی + دریافت | `finance.integration.telemed` |
| `telemed.payment.refund_due` | انقضا، لغو، پرداخت دیرهنگام | ابطال صورتحساب | همان |
| `telemed.payment.refunded` | ثبت استرداد در پنل مشاوره | سند استرداد | همان |
| `telemed.payment.failed` | پرداخت ناموفق | فقط ثبت؛ بدون اثر مالی | همان |
| `sales.salesinvoice.posted/reversed`، `sales.salesreturn.*`، `purchasing.goodsreceipt.*`، `purchasing.purchaseinvoice.*`، `inventory.inventorydocument.*` | `PostedDocument.post/reverse` | پیوند به سند حسابداری موجود | `finance.integration.pharmacy` |
| `lab.laborder.*`، `lab.labpayment.*`، `lab.claimsettlement.*` | همان | همان | `finance.integration.lab` |

### payload نمونه

```json
// ehr.encounter.signed (نسخه ۱)
{"encounter_id": 12, "patient_id": 5, "patient_name": "...", "provider_id": 3, "provider_name": "...",
 "appointment_id": 40, "date": "2026-10-26", "visit_fee": 2000000,
 "services": [{"line_ref": 81, "code": "901900", "title": "...", "quantity": 1, "unit_tariff": 1500000, "sector": "private"}]}

// telemed.payment.paid (نسخه ۱)
{"payment_id": 9, "consultation_id": 31, "code": "TM-...", "kind": "text", "kind_label": "...", "channel": "doctor",
 "doctor_id": 3, "doctor_name": "...", "patient_user_id": 77, "patient_name": "...", "amount": 1500000,
 "method": "online", "gateway": "sandbox", "reference": "...", "refund_reference": "", "at": "..."}

// <سند>.posted (نسخه ۱)
{"number": "SI-1405-00012", "date": "2026-10-26", "model": "sales.salesinvoice", "id": 120}
```

**حداقل داده پزشکی:** payloadها فقط شناسه، نام، کد و عنوان خدمت و مبلغ دارند. شرح حال، معاینه، تشخیص، یادداشت درمانگر و موضوع یا متن گفتگوی مشاوره هرگز در رویداد نیست (با تست کنترل می‌شود).

## مالکیت داده

- مالکیت خدمت، نوبت، ویزیت، نسخه، موجودی و جواب آزمایش در ماژول منبع می‌ماند. مالی هیچ‌کدام را تغییر نمی‌دهد و خدمت انجام‌نشده را صورتحساب نمی‌کند.
- داروخانه و آزمایشگاه سند حسابداری خودشان را هم‌زمان ثبت می‌کنند (کاردکس = دفتر کل). مالی فقط پیوند و کنترل وجود سند را انجام می‌دهد.
- صورتحساب تکراری برای یک رویداد ممکن نیست: یکتایی «منبع فعال» صورتحساب در پایگاه داده (`unique_when`)، و اگر برای نوبت همان ویزیت در پذیرش صورتحساب صادر شده باشد، ویزیت دوباره صورتحساب نمی‌شود.

## افزودن رویداد تازه

1. در ماژول تولیدکننده، داخل همان `transaction.atomic` که داده را ذخیره می‌کند، `events.publish("app.thing.happened", obj, payload)` را صدا بزنید. payload فقط حداقل داده لازم.
2. در `apps/finance/integration.py` یک handler با `@subscribe("app.thing.happened", flag="finance.integration.<x>")` بنویسید. handler باید idempotent باشد (پیش از ساخت، وجود سند را با مرجع منبع یا کلید idempotency بررسی کند) و متن کوتاه نتیجه برگرداند.
3. اگر قابلیت تازه لازم است، در `apps/finance/flags.py` ثبت کنید.
4. تست: انتشار، پردازش، پردازش دوباره بدون سند تکراری، رفتار با قابلیت خاموش.

## اتصال‌های واقعی و موقت

| اتصال | وضعیت |
|---|---|
| مشاوره آنلاین ← مالی | واقعی (رویداد از کد پرداخت و استرداد) |
| ویزیت درمانگاه ← مالی | واقعی |
| جلسه توانبخشی ← مالی | واقعی |
| داروخانه و آزمایشگاه ← مالی | واقعی (پیوند و کنترل؛ ثبت حسابداری همان ثبت قبلی) |
| درگاه پرداخت | فقط درگاه آزمایشی موجود مشاوره آنلاین؛ درگاه واقعی وصل نشده |
| بیمه‌ها (استعلام، ارسال الکترونیکی) | وصل نشده؛ سهم بیمه دستی در صورتحساب وارد می‌شود |
| سامانه مؤدیان و صورتحساب الکترونیکی | وصل نشده و هیچ ارسال واقعی انجام نمی‌شود |
| حضور و غیاب | وجود ندارد؛ کارکرد دستی وارد می‌شود |

## پایش

- `GET /finance/health/` و `/finance/api/v1/health/`: برای عموم فقط `status` و `database`؛ برای کاربر دارای `core.view_outboxevent` تعداد رویدادهای در صف، ناموفق، متوقف و در انتظار و وضعیت قابلیت‌ها. اگر رویداد متوقف یا معوق بیش از ۶۰ دقیقه باشد، `status` برابر `degraded` است.
- `GET /finance/api/v1/integration/`: فهرست رویدادها با صفحه‌بندی.
- هر پاسخ وب سرآیند `X-Request-ID` دارد و همان شناسه در رویدادهای ساخته‌شده در آن درخواست ثبت می‌شود.
