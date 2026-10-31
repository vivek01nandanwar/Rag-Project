from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import auth, views

router = DefaultRouter()
router.register("categories", views.CategoryViewSet)
router.register("customers", views.CustomerViewSet)
router.register("suppliers", views.SupplierViewSet)
router.register("items", views.ItemViewSet)
router.register("stock-movements", views.StockMovementViewSet)
router.register("sales", views.SaleViewSet)
router.register("purchases", views.PurchaseViewSet)
router.register("payments", views.PaymentViewSet)
router.register("sale-returns", views.SaleReturnViewSet)
router.register("purchase-returns", views.PurchaseReturnViewSet)

urlpatterns = [
    path("auth/login/", auth.LoginView.as_view()),
    path("auth/logout/", auth.LogoutView.as_view()),
    path("auth/me/", auth.MeView.as_view()),
    path("dashboard/", views.DashboardView.as_view()),
    path("dues/", views.DuesView.as_view()),
    path("transactions/", views.RecentTransactionsView.as_view()),
    path("", include(router.urls)),
]
