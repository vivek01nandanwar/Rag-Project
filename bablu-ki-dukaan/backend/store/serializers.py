from rest_framework import serializers

from . import services
from .models import (
    Category, Customer, Item, Payment, Purchase, PurchaseItem, PurchaseReturn, Sale, SaleItem,
    SaleReturn, StockMovement, Supplier,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class CustomerSerializer(serializers.ModelSerializer):
    pending_from_customer = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    refund_due_to_customer = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "address", "pending_from_customer", "refund_due_to_customer"]


class SupplierSerializer(serializers.ModelSerializer):
    pending_to_supplier = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    refund_due_from_supplier = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Supplier
        fields = ["id", "name", "phone", "address", "pending_to_supplier", "refund_due_from_supplier"]


class ItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Item
        fields = [
            "id", "name", "aliases", "category", "category_name", "emoji", "unit",
            "selling_price", "cost_price", "current_stock", "low_stock_threshold",
            "is_low_stock", "is_active",
        ]
        # stock only changes via purchase / sale / adjust, never by editing the item
        read_only_fields = ["current_stock"]
        extra_kwargs = {
            "selling_price": {"min_value": 0},
            "cost_price": {"min_value": 0},
            "low_stock_threshold": {"min_value": 0},
        }

    def validate(self, attrs):
        item = self.instance
        if item and "unit" in attrs and attrs["unit"] != item.unit:
            if item.current_stock != 0 or item.sale_lines.exists() or item.purchase_lines.exists():
                raise serializers.ValidationError(
                    {"unit": "The unit cannot be changed once a product has stock or bills, because old records would become wrong. Create a new product instead."})
        return attrs


class StockMovementSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "item", "item_name", "kind", "quantity", "stock_after", "note", "created_at"]


# ---------- Sales ----------
class SaleItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    returned_quantity = serializers.DecimalField(max_digits=10, decimal_places=3, read_only=True)

    class Meta:
        model = SaleItem
        fields = ["id", "item", "item_name", "unit", "quantity", "price", "line_total", "returned_quantity"]
        read_only_fields = ["line_total"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "sale", "purchase", "direction", "amount", "mode", "note", "created_at"]
        read_only_fields = ["created_at"]

    def create(self, validated):
        try:
            return services.add_payment(**validated)
        except services.BusinessError as e:
            raise serializers.ValidationError({"detail": str(e)})


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleItemSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="Walk-in")
    payments = PaymentSerializer(many=True, read_only=True)
    net_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "customer", "customer_name", "date", "lines", "total_amount", "amount_received",
            "returned_amount", "net_amount", "pending_from_customer", "refund_due_to_customer", "payment_mode", "notes", "payments",
        ]
        read_only_fields = ["total_amount", "returned_amount", "net_amount", "pending_from_customer", "refund_due_to_customer"]

    def create(self, validated):
        lines = validated.pop("lines")
        try:
            return services.create_sale(
                customer=validated.get("customer"),
                lines=lines,
                amount_received=validated.get("amount_received", 0),
                payment_mode=validated.get("payment_mode", "cash"),
                notes=validated.get("notes", ""),
                date=validated.get("date"),
            )
        except services.BusinessError as e:
            raise serializers.ValidationError({"detail": str(e)})


# ---------- Purchases ----------
class PurchaseItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    cost_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    returned_quantity = serializers.DecimalField(max_digits=10, decimal_places=3, read_only=True)

    class Meta:
        model = PurchaseItem
        fields = ["id", "item", "item_name", "unit", "quantity", "cost_price", "line_total", "returned_quantity"]
        read_only_fields = ["line_total"]


class PurchaseSerializer(serializers.ModelSerializer):
    lines = PurchaseItemSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="—")
    payments = PaymentSerializer(many=True, read_only=True)
    net_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Purchase
        fields = [
            "id", "supplier", "supplier_name", "date", "lines", "total_amount", "amount_paid",
            "returned_amount", "net_amount", "pending_to_supplier", "refund_due_from_supplier", "notes", "payments",
        ]
        read_only_fields = ["total_amount", "returned_amount", "net_amount", "pending_to_supplier", "refund_due_from_supplier"]

    def create(self, validated):
        lines = validated.pop("lines")
        try:
            return services.create_purchase(
                supplier=validated.get("supplier"),
                lines=lines,
                amount_paid=validated.get("amount_paid", 0),
                notes=validated.get("notes", ""),
                date=validated.get("date"),
            )
        except services.BusinessError as e:
            raise serializers.ValidationError({"detail": str(e)})


# ---------- Returns ----------
class SaleReturnSerializer(serializers.ModelSerializer):
    """A sales return has NO reason field (reasons are only kept for purchase returns)."""

    item_name = serializers.CharField(source="item.name", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    customer_name = serializers.CharField(source="sale.customer.name", read_only=True, default="Walk-in")

    class Meta:
        model = SaleReturn
        fields = ["id", "sale", "sale_item", "item", "item_name", "unit", "customer_name",
                  "quantity", "unit_price", "amount", "date"]
        read_only_fields = ["sale", "item", "unit_price", "amount"]

    def create(self, validated):
        try:
            return services.create_sale_return(
                sale_item=validated["sale_item"], quantity=validated["quantity"], date=validated.get("date"))
        except services.BusinessError as e:
            raise serializers.ValidationError({"detail": str(e)})


class PurchaseReturnSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    supplier_name = serializers.CharField(source="purchase.supplier.name", read_only=True, default="—")
    reason = serializers.CharField(min_length=3, max_length=200)

    class Meta:
        model = PurchaseReturn
        fields = ["id", "purchase", "purchase_item", "item", "item_name", "unit", "supplier_name",
                  "quantity", "unit_cost", "amount", "reason", "date"]
        read_only_fields = ["purchase", "item", "unit_cost", "amount"]

    def create(self, validated):
        try:
            return services.create_purchase_return(
                purchase_item=validated["purchase_item"], quantity=validated["quantity"],
                reason=validated["reason"], date=validated.get("date"))
        except services.BusinessError as e:
            raise serializers.ValidationError({"detail": str(e)})
