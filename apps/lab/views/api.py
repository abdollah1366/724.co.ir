"""API ورود جواب از دستگاه یا نرم‌افزار واسط (middleware) با کلید اختصاصی هر دستگاه.

POST /lab/api/instrument/results/
Header: X-Instrument-Key: <کلید>
Body (JSON): {"barcode": "14050001201", "results": [{"code": "FBS", "value": "95"}]}
پاسخ: {"saved": ["FBS"], "errors": []}
جواب‌ها فقط «ثبت‌شده» می‌شوند و همیشه نیاز به تأیید مسئول فنی دارند.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit import log
from apps.audit.models import AuditLog

from ..models import Instrument
from ..services import lis


@csrf_exempt
@require_POST
def instrument_results(request):
    key = request.headers.get("X-Instrument-Key", "")
    instrument = Instrument.objects.filter(api_key_hash=Instrument.hash_key(key), is_active=True).first() if key else None
    if instrument is None:
        return JsonResponse({"error": "کلید دستگاه نامعتبر است."}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        barcode = str(payload["barcode"])
        rows = list(payload["results"])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "ساختار JSON نامعتبر است."}, status=400)
    saved, errors = lis.import_results(instrument, barcode, rows)
    log.record(AuditLog.Action.EVENT, instrument, message=f"ورود جواب از دستگاه برای {barcode}: {len(saved)} ثبت، {len(errors)} خطا")
    return JsonResponse({"saved": saved, "errors": errors}, status=200 if saved or not errors else 422)
