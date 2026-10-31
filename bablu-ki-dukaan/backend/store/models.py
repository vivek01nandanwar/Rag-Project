from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

ZERO = Decimal("0")


def money():
    return models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)


def qty():
    return models.DecimalField(max_digits=10, decimal_places=3, default=ZERO)


class IdCounter(models.Model):
    """Keeps the running number for each prefix (ITM -> 12 means next is ITM-0013)."""

    prefix = models.CharField(max_length=6, primary_key=True)
    last_number = models.PositiveIntegerField(default=0)


def next_code(prefix):
    with transaction.atomic():
        counter, _ = IdCounter.objects.select_for_update().get_or_create(prefix=prefix)
        counter.last_number += 1
        counter.save(update_fields=["last_number"])
    return f"{prefix}-{counter.last_number:04d}"


class CodedModel(models.Model):
    """Every table inherits this: its primary key IS the unique ID (e.g. CUS-0001).

    Other tables connect to it through foreign keys, so the unique ID is the
    only thing that links Sale -> Customer, SaleItem -> Item, Payment -> Sale, etc.
    """

    ID_PREFIX = "ID"
    id = models.CharField(primary_key=True, max_length=20, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = next_code(self.ID_PREFIX)
        super().save(*args, **kwargs)


class Category(CodedModel):
    ID_PREFIX = "CAT"

    name = models.CharField(max_length=60, unique=True)
    emoji = models.CharField(max_length=8, blank=True, default="🛒")

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Customer(CodedModel):
    ID_PREFIX = "CUS"

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def pending_from_customer(self):
        """Total udhaar: money this customer still has to pay the shop."""
        return self.sales.aggregate(t=models.Sum("pending_from_customer"))["t"] or ZERO

    @property
    def refund_due_to_customer(self):
        """Total money the shop still has to return to this customer."""
        return self.sales.aggregate(t=models.Sum("refund_due_to_customer"))["t"] or ZERO


class Supplier(CodedModel):
    ID_PREFIX = "SUP"

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def pending_to_supplier(self):
        return self.purchases.aggregate(t=models.Sum("pending_to_supplier"))["t"] or ZERO

    @property
    def refund_due_from_supplier(self):
        return self.purchases.aggregate(t=models.Sum("refund_due_from_supplier"))["t"] or ZERO


class Item(CodedModel):
    ID_PREFIX = "ITM"

    """Product details + the AVAILABLE STOCK for that product."""

    class Unit(models.TextChoices):
        KG = "kg", "kg"
        GRAM = "g", "gram"
        LITRE = "litre", "litre"
        PACKET = "packet", "packet"
        PIECE = "piece", "piece"
        DOZEN = "dozen", "dozen"

    name = models.CharField(max_length=120, unique=True)
    aliases = models.CharField(
        max_length=250,
        blank=True,
        help_text="Comma separated other names, e.g. 'ghi, desi ghee' (used by the AI agent later)",
    )
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="items")
    emoji = models.CharField(max_length=8, blank=True, default="📦")
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.PIECE)
    selling_price = money()
    cost_price = money()
    current_stock = qty()
    low_stock_threshold = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal("5"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    @property
    def is_low_stock(self):
        return self.current_stock <= self.low_stock_threshold


class StockMovement(CodedModel):
    ID_PREFIX = "MOV"

    """Every change in stock is logged here, so stock can always be explained."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase", "Purchase (stock in)"
        SALE = "sale", "Sale (stock out)"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        SALE_RETURN = "sale_return", "Sale return (stock in)"
        PURCHASE_RETURN = "purchase_return", "Purchase return (stock out)"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, help_text="+ in, - out")
    stock_after = models.DecimalField(max_digits=10, decimal_places=3)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Purchase(CodedModel):
    ID_PREFIX = "PUR"

    """Stock bought from a supplier."""

    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="purchases")
    date = models.DateField(default=timezone.localdate)
    total_amount = money()
    amount_paid = money()  # what the shop has paid to the supplier so far
    # --- the "remaining" columns for purchase side ---
    pending_to_supplier = money()      # shop still has to pay the supplier
    refund_due_from_supplier = money()  # supplier has to return extra money to shop
    returned_amount = money()           # value of goods already sent BACK to the supplier
    notes = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    @property
    def net_amount(self):
        """Bill value after purchase returns."""
        return self.total_amount - self.returned_amount

    def recalc(self):
        diff = self.net_amount - self.amount_paid
        self.pending_to_supplier = max(diff, ZERO)
        self.refund_due_from_supplier = max(-diff, ZERO)

    def __str__(self):
        return f"{self.pk} - {self.total_amount}"


class PurchaseItem(CodedModel):
    ID_PREFIX = "PLI"

    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_lines")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def returned_quantity(self):
        return self.returns.aggregate(t=models.Sum("quantity"))["t"] or ZERO


class Sale(CodedModel):
    ID_PREFIX = "SAL"

    """Goods sold to a customer (customer can be empty = walk-in)."""

    class Mode(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        UDHAAR = "udhaar", "Udhaar (credit)"

    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales")
    date = models.DateField(default=timezone.localdate)
    total_amount = money()
    amount_received = money()  # what the customer has actually paid so far
    # --- the two "remaining transaction" columns ---
    pending_from_customer = money()    # customer still has to pay the shop
    refund_due_to_customer = money()   # shop still has to return money to the customer
    returned_amount = money()          # value of goods the customer already brought BACK
    payment_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.CASH)
    notes = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    @property
    def net_amount(self):
        """Bill value after sales returns."""
        return self.total_amount - self.returned_amount

    def recalc(self):
        diff = self.net_amount - self.amount_received
        self.pending_from_customer = max(diff, ZERO)
        self.refund_due_to_customer = max(-diff, ZERO)

    def __str__(self):
        return f"{self.pk} - {self.total_amount}"


class SaleItem(CodedModel):
    ID_PREFIX = "SLI"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sale_lines")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Actual price sold at (can differ from item price)")
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        help_text="What the shop paid for ONE unit when this was sold (used for profit)")

    @property
    def returned_quantity(self):
        return self.returns.aggregate(t=models.Sum("quantity"))["t"] or ZERO


class Payment(CodedModel):
    ID_PREFIX = "PAY"

    """A settlement made later against one Sale or one Purchase.

    Sale:      IN  = customer paid pending money      | OUT = shop returned money to customer
    Purchase:  OUT = shop paid supplier pending money | IN  = supplier returned money to shop
    """

    class Direction(models.TextChoices):
        IN = "in", "Money in"
        OUT = "out", "Money out"

    sale = models.ForeignKey(Sale, null=True, blank=True, on_delete=models.CASCADE, related_name="payments")
    purchase = models.ForeignKey(Purchase, null=True, blank=True, on_delete=models.CASCADE, related_name="payments")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    mode = models.CharField(max_length=10, default="cash")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if bool(self.sale) == bool(self.purchase):
            raise ValidationError("A payment must belong to exactly one sale or one purchase.")


class SaleReturn(CodedModel):
    """A customer brings a product back. Stock goes UP, and the bill value goes DOWN
    (so pending udhaar shrinks, or money has to be returned to the customer).
    No 'reason' column here on purpose: reasons are recorded only for purchase returns."""

    ID_PREFIX = "SRT"

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="sale_returns")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="returns")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sale_returns")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)  # price the customer paid per unit
    amount = models.DecimalField(max_digits=12, decimal_places=2)      # quantity x unit_price
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.pk} - {self.item_id} x {self.quantity}"


class PurchaseReturn(CodedModel):
    """The shop sends a product back to the supplier. Stock goes DOWN, and the bill value goes DOWN
    (so the shop owes less, or the supplier has to return money). A reason is required."""

    ID_PREFIX = "PRT"

    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="purchase_returns")
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT, related_name="returns")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_returns")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)   # buying price per unit on that bill
    amount = models.DecimalField(max_digits=12, decimal_places=2)      # quantity x unit_cost
    reason = models.CharField(max_length=200)                          # why it was returned
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.pk} - {self.item_id} x {self.quantity}"
