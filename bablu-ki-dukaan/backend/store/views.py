from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Prefetch, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reports, services, timeline
from .models import (
    Category, Customer, Item, Payment, Purchase, PurchaseReturn, Sale, SaleReturn, StockMovement, Supplier, ZERO,
)
from .serializers import (
    CategorySerializer, CustomerSerializer, ItemSerializer, PaymentSerializer,
    PurchaseReturnSerializer, PurchaseSerializer, SaleReturnSerializer, SaleSerializer,
    StockMovementSerializer, SupplierSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.select_related("category").all()
    serializer_class = ItemSerializer

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """Fix the stock after counting the shelf: {"new_quantity": 12, "note": "..."}"""
        item = self.get_object()
        try:
            services.adjust_stock(
                item=item,
                new_quantity=request.data.get("new_quantity"),
                note=request.data.get("note") or "Manual correction",
            )
        except (services.BusinessError, ArithmeticError, ValueError) as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        item.refresh_from_db()
        return Response(ItemSerializer(item).data)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related("item").all()
    serializer_class = StockMovementSerializer


class _CreateListRetrieve(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    pass


class SaleViewSet(_CreateListRetrieve):
    queryset = Sale.objects.select_related("customer").prefetch_related("lines__item", "payments")
    serializer_class = SaleSerializer


class PurchaseViewSet(_CreateListRetrieve):
    queryset = Purchase.objects.select_related("supplier").prefetch_related("lines__item", "payments")
    serializer_class = PurchaseSerializer


class SaleReturnViewSet(_CreateListRetrieve):
    queryset = SaleReturn.objects.select_related("item", "sale__customer")
    serializer_class = SaleReturnSerializer


class PurchaseReturnViewSet(_CreateListRetrieve):
    queryset = PurchaseReturn.objects.select_related("item", "purchase__supplier")
    serializer_class = PurchaseReturnSerializer


class PaymentViewSet(_CreateListRetrieve):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer


class DuesView(APIView):
    """The four 'remaining transaction' lists in one call."""

    def get(self, request):
        return Response({
            "customers_owe_shop": SaleSerializer(
                Sale.objects.filter(pending_from_customer__gt=0), many=True).data,
            "shop_owes_customers": SaleSerializer(
                Sale.objects.filter(refund_due_to_customer__gt=0), many=True).data,
            "shop_owes_suppliers": PurchaseSerializer(
                Purchase.objects.filter(pending_to_supplier__gt=0), many=True).data,
            "suppliers_owe_shop": PurchaseSerializer(
                Purchase.objects.filter(refund_due_from_supplier__gt=0), many=True).data,
        })


class DashboardView(APIView):
    def get(self, request):
        today = timezone.localdate()

        def total(qs, field):
            return qs.aggregate(t=Sum(field))["t"] or ZERO

        stock_value = Item.objects.aggregate(
            t=Sum(ExpressionWrapper(F("current_stock") * F("cost_price"),
                                    output_field=DecimalField(max_digits=16, decimal_places=2)))
        )["t"] or ZERO

        low = [i for i in Item.objects.filter(is_active=True) if i.is_low_stock]

        # yearly profit: ?year=2026 (defaults to this year)
        try:
            year = int(request.query_params.get("year", today.year))
        except ValueError:
            year = today.year
        year = min(max(year, 2000), today.year + 1)
        years = sorted({d.year for d in Sale.objects.dates("date", "year")} | {today.year}, reverse=True)

        return Response({
            "shop_name": "Bablu ki dukaan",
            "today_sales": total(Sale.objects.filter(date=today), "total_amount"),
            "today_purchases": total(Purchase.objects.filter(date=today), "total_amount"),
            "pending_from_customers": total(Sale.objects, "pending_from_customer"),
            "refund_due_to_customers": total(Sale.objects, "refund_due_to_customer"),
            "pending_to_suppliers": total(Purchase.objects, "pending_to_supplier"),
            "refund_due_from_suppliers": total(Purchase.objects, "refund_due_from_supplier"),
            "stock_value": stock_value,
            "item_count": Item.objects.count(),
            "profit": reports.profit_for_year(year),
            "years": years,
            "low_stock": ItemSerializer(low, many=True).data,
            "recent_sales": SaleSerializer(Sale.objects.all()[:5], many=True).data,
        })


def _q(value):
    """10.000 -> '10', 0.500 -> '0.5'"""
    return f"{value:f}".rstrip("0").rstrip(".") or "0"


class RecentTransactionsView(APIView):
    """Latest 50 events, newest first: sales, purchases, returns and later payments / refunds.

    Each bill shows its FIRST entry as it was on the day (money paid then, and what was left), and every later
    payment, refund or return is its own entry with the balance left right after it. See timeline.py.
    """

    LIMIT = 50

    def get(self, request):
        L = self.LIMIT
        sale_ids = set(Sale.objects.order_by("-created_at").values_list("pk", flat=True)[:L])
        purchase_ids = set(Purchase.objects.order_by("-created_at").values_list("pk", flat=True)[:L])
        for p in Payment.objects.order_by("-created_at")[:L]:
            (sale_ids if p.sale_id else purchase_ids).add(p.sale_id or p.purchase_id)
        sale_ids |= {r.sale_id for r in SaleReturn.objects.order_by("-created_at")[:L]}
        purchase_ids |= {r.purchase_id for r in PurchaseReturn.objects.order_by("-created_at")[:L]}

        rows = []

        for s in (Sale.objects.filter(pk__in=sale_ids).select_related("customer")
                  .prefetch_related("lines__item", "payments",
                                    Prefetch("sale_returns", queryset=SaleReturn.objects.select_related("item")))):
            party = s.customer.name if s.customer else "Walk-in customer"
            pays, rets = list(s.payments.all()), list(s.sale_returns.all())
            first, after = timeline.replay(s.total_amount, s.amount_received, pays, rets, "in")
            rows.append({
                "id": s.id, "type": "sale", "title": "Sale", "party": party,
                "summary": ", ".join(f"{l.item.name} ×{_q(l.quantity)}" for l in s.lines.all()),
                "amount": s.total_amount, "settled": first["paid"],
                "pending": first["pending"], "refund": first["refund"],
                "flow": "in", "date": s.date, "created_at": s.created_at,
            })
            for pay in pays:
                rows.append({
                    "id": pay.id, "type": "payment", "party": party, "summary": f"Against {s.id}",
                    "title": "Payment received" if pay.direction == "in" else "Refund given to customer",
                    "amount": pay.amount, "settled": pay.amount,
                    "pending": after[pay.id]["pending"], "refund": after[pay.id]["refund"],
                    "flow": pay.direction, "date": pay.created_at.date(), "created_at": pay.created_at,
                })
            for r in rets:
                rows.append({
                    "id": r.id, "type": "return", "title": "Sales return", "party": party,
                    "summary": f"{r.item.name} ×{_q(r.quantity)} (from {s.id})",
                    "amount": r.amount, "settled": 0,
                    "pending": after[r.id]["pending"], "refund": after[r.id]["refund"],
                    "flow": "out", "date": r.date, "created_at": r.created_at,
                })

        for p in (Purchase.objects.filter(pk__in=purchase_ids).select_related("supplier")
                  .prefetch_related("lines__item", "payments",
                                    Prefetch("purchase_returns", queryset=PurchaseReturn.objects.select_related("item")))):
            party = p.supplier.name if p.supplier else "No supplier"
            pays, rets = list(p.payments.all()), list(p.purchase_returns.all())
            first, after = timeline.replay(p.total_amount, p.amount_paid, pays, rets, "out")
            rows.append({
                "id": p.id, "type": "purchase", "title": "Purchase", "party": party,
                "summary": ", ".join(f"{l.item.name} ×{_q(l.quantity)}" for l in p.lines.all()),
                "amount": p.total_amount, "settled": first["paid"],
                "pending": first["pending"], "refund": first["refund"],
                "flow": "out", "date": p.date, "created_at": p.created_at,
            })
            for pay in pays:
                rows.append({
                    "id": pay.id, "type": "payment", "party": party, "summary": f"Against {p.id}",
                    "title": "Paid to supplier" if pay.direction == "out" else "Refund received from supplier",
                    "amount": pay.amount, "settled": pay.amount,
                    "pending": after[pay.id]["pending"], "refund": after[pay.id]["refund"],
                    "flow": pay.direction, "date": pay.created_at.date(), "created_at": pay.created_at,
                })
            for r in rets:
                rows.append({
                    "id": r.id, "type": "return", "title": "Purchase return", "party": party,
                    "summary": f"{r.item.name} ×{_q(r.quantity)} (from {p.id}) · Reason: {r.reason}",
                    "amount": r.amount, "settled": 0,
                    "pending": after[r.id]["pending"], "refund": after[r.id]["refund"],
                    "flow": "in", "date": r.date, "created_at": r.created_at,
                })

        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return Response(rows[:L])
