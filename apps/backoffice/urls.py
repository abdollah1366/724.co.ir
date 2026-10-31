from django.urls import path

from . import cashboxes, staff, views

app_name = "backoffice"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("alerts/", views.alerts_view, name="alerts"),
    path("stock/", views.stock_report, name="stock"),
    path("products/<int:pk>/kardex/", views.kardex, name="kardex"),
    path("reports/profit-loss/", views.profit_loss, name="pnl"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
    path("reports/parties/", views.parties, name="parties"),
    path("reports/parties/<int:pk>/", views.party_statement, name="party_statement"),
    path("pos/", views.pos, name="pos"),
    path("pos/add/", views.pos_add, name="pos_add"),
    path("pos/lines/<int:pk>/remove/", views.pos_remove, name="pos_remove"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("pos/close/", views.pos_close, name="pos_close"),
    path("invoices/<int:pk>/", views.pos_invoice, name="pos_invoice"),
    path("api/products/lookup/", views.api_product_lookup, name="api_lookup"),
    path("api/products/<int:pk>/stock/", views.api_product_stock, name="api_stock"),
    path("cashboxes/", cashboxes.cashboxes, name="cashboxes"),
    path("cashbox/", cashboxes.my_cashbox, name="my_cashbox"),
    path("cashbox/close/", cashboxes.my_cashbox_close, name="my_cashbox_close"),
    path("staff/", staff.staff_list, name="staff"),
    path("staff/new/", staff.staff_edit, name="staff_new"),
    path("staff/<int:pk>/", staff.staff_edit, name="staff_edit"),
    path("api/dashboard/", views.api_dashboard, name="api_dashboard"),
]
