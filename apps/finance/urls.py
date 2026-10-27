from django.urls import path

from . import api, views

app_name = "finance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cashier/", views.cashier, name="cashier"),
    path("cashier/receive/", views.cashier_receive, name="cashier_receive"),
    path("cashier/invoice/", views.cashier_invoice, name="cashier_invoice"),
    path("cashier/refund/", views.cashier_refund, name="cashier_refund"),
    path("refunds/<int:pk>/pay/", views.refund_pay, name="refund_pay"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
    path("receivables/", views.receivables, name="receivables"),
    path("reports/ledger/", views.ledger, name="ledger"),
    path("reports/journal/", views.journal, name="journal"),
    path("reports/centers/", views.center_pnl, name="center_pnl"),
    path("reports/cash-flow/", views.cash_flow, name="cash_flow"),
    path("reports/checks/", views.checks, name="checks"),
    path("bank/<int:pk>/", views.bank_reconciliation, name="bank_reconciliation"),
    path("integration/", views.integration, name="integration"),
    path("integration/retry/", views.integration_retry, name="integration_retry"),
    path("settings/", views.settings_view, name="settings"),
    path("health/", views.health, name="health"),
    path("my/earnings/", views.my_earnings, name="my_earnings"),
    path("my/settlements/<int:pk>/", views.my_settlement, name="my_settlement"),
    path("my/payslips/", views.my_payslips, name="my_payslips"),
    path("payslips/<int:pk>/", views.payslip, name="payslip"),
    path("api/v1/health/", api.health, name="api_health"),
    path("api/v1/invoices/", api.invoices, name="api_invoices"),
    path("api/v1/invoices/<str:number>/", api.invoice_detail, name="api_invoice"),
    path("api/v1/receipts/", api.receipts, name="api_receipts"),
    path("api/v1/accounts/", api.accounts, name="api_accounts"),
    path("api/v1/trial-balance/", api.trial_balance, name="api_trial_balance"),
    path("api/v1/integration/", api.integration_status, name="api_integration"),
    path("api/v1/my/settlements/", api.my_settlements, name="api_my_settlements"),
]
