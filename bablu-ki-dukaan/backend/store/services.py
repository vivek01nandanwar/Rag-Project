"""All money + stock rules live here.

The REST API calls these functions today; the Groq AI agent will call the
very same functions later as "tools". One place = no duplicated logic.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from .models import (
    Item, Payment, Purchase, PurchaseItem, PurchaseReturn, Sale, SaleItem, SaleReturn, StockMovement, ZERO,
)


class BusinessError(Exception):
    """Raised for rule violations (e.g. not enough stock). Becomes HTTP 400."""


def _to_dec(value):
    return Decimal(str(value))


def _move_stock(item, quantity, kind, note=""):
    Item.objects.filter(pk=item.pk).update(current_stock=F("current_stock") + quantity)
    item.refresh_from_db()
    StockMovement.objects.create(
        item=item, kind=kind, quantity=quantity, stock_after=item.current_stock, note=note
    )


@transaction.atomic
def create_sale(*, customer=None, lines, amount_received=ZERO, payment_mode="cash", notes="", date=None):
    """lines = [{"item": Item, "quantity": x, "price": optional}]"""
    if not lines:
        raise BusinessError("Add at least one item to the sale.")

    sale = Sale(customer=customer, payment_mode=payment_mode, notes=notes)
    if date:
        sale.date = date
    sale.save()

    total = ZERO
    for ln in lines:
        item = Item.objects.select_for_update().get(pk=ln["item"].pk)
        q = _to_dec(ln["quantity"])
        if q <= 0:
            raise BusinessError(f"Quantity for {item.name} must be more than 0.")
        if item.current_stock < q:
            raise BusinessError(
                f"Only {item.current_stock} {item.unit} of {item.name} in stock, cannot sell {q}."
            )
        price = _to_dec(ln["price"]) if ln.get("price") not in (None, "") else item.selling_price
        line_total = (q * price).quantize(Decimal("0.01"))
        SaleItem.objects.create(sale=sale, item=item, quantity=q, price=price, line_total=line_total,
                                cost_price=item.cost_price)
        _move_stock(item, -q, StockMovement.Kind.SALE, f"Sale {sale.pk}")
        total += line_total

    sale.total_amount = total
    sale.amount_received = _to_dec(amount_received)
    if payment_mode == "udhaar" and sale.amount_received == ZERO:
        pass  # whole bill is pending, that is expected for udhaar
    sale.recalc()
    if (sale.pending_from_customer > 0 or sale.refund_due_to_customer > 0) and customer is None:
        raise BusinessError("Choose a customer when money is pending or has to be returned.")
    sale.save()
    return sale


@transaction.atomic
def create_purchase(*, supplier=None, lines, amount_paid=ZERO, notes="", date=None):
    """lines = [{"item": Item, "quantity": x, "cost_price": x}]"""
    if not lines:
        raise BusinessError("Add at least one item to the purchase.")

    purchase = Purchase(supplier=supplier, notes=notes)
    if date:
        purchase.date = date
    purchase.save()

    total = ZERO
    for ln in lines:
        item = Item.objects.select_for_update().get(pk=ln["item"].pk)
        q = _to_dec(ln["quantity"])
        if q <= 0:
            raise BusinessError(f"Quantity for {item.name} must be more than 0.")
        cost = _to_dec(ln["cost_price"]) if ln.get("cost_price") not in (None, "") else item.cost_price
        line_total = (q * cost).quantize(Decimal("0.01"))
        PurchaseItem.objects.create(purchase=purchase, item=item, quantity=q, cost_price=cost, line_total=line_total)
        _move_stock(item, q, StockMovement.Kind.PURCHASE, f"Purchase {purchase.pk}")
        Item.objects.filter(pk=item.pk).update(cost_price=cost)  # remember latest buying price
        total += line_total

    purchase.total_amount = total
    purchase.amount_paid = _to_dec(amount_paid)
    purchase.recalc()
    if (purchase.pending_to_supplier > 0 or purchase.refund_due_from_supplier > 0) and supplier is None:
        raise BusinessError("Choose a supplier when money is pending or has to be returned.")
    purchase.save()
    return purchase


@transaction.atomic
def add_payment(*, sale=None, purchase=None, direction, amount, mode="cash", note=""):
    amount = _to_dec(amount)
    if amount <= 0:
        raise BusinessError("Amount must be more than 0.")
    if bool(sale) == bool(purchase):
        raise BusinessError("Payment must belong to exactly one sale or one purchase.")

    if sale:
        sale = Sale.objects.select_for_update().get(pk=sale.pk)
        if direction == Payment.Direction.IN:       # customer pays pending money
            if amount > sale.pending_from_customer:
                raise BusinessError(f"Customer only owes ₹{sale.pending_from_customer}.")
            sale.amount_received += amount
        else:                                       # shop returns money to customer
            if amount > sale.refund_due_to_customer:
                raise BusinessError(f"Shop only has to return ₹{sale.refund_due_to_customer}.")
            sale.amount_received -= amount
        sale.recalc()
        sale.save()
    else:
        purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
        if direction == Payment.Direction.OUT:      # shop pays supplier
            if amount > purchase.pending_to_supplier:
                raise BusinessError(f"Shop only owes ₹{purchase.pending_to_supplier} to supplier.")
            purchase.amount_paid += amount
        else:                                       # supplier returns money
            if amount > purchase.refund_due_from_supplier:
                raise BusinessError(f"Supplier only has to return ₹{purchase.refund_due_from_supplier}.")
            purchase.amount_paid -= amount
        purchase.recalc()
        purchase.save()

    return Payment.objects.create(
        sale=sale, purchase=purchase, direction=direction, amount=amount, mode=mode, note=note
    )


@transaction.atomic
def adjust_stock(*, item, new_quantity, note="Manual correction"):
    new_quantity = _to_dec(new_quantity)
    if new_quantity < 0:
        raise BusinessError("Stock cannot be negative.")
    diff = new_quantity - item.current_stock
    if diff != 0:
        _move_stock(item, diff, StockMovement.Kind.ADJUSTMENT, note)
    return item


@transaction.atomic
def create_sale_return(*, sale_item, quantity, date=None):
    """Customer brings back some (or all) of one line of a sale."""
    line = SaleItem.objects.select_for_update().select_related("item").get(pk=sale_item.pk)
    q = _to_dec(quantity)
    if q <= 0:
        raise BusinessError("Return quantity must be more than 0.")
    left = line.quantity - line.returned_quantity
    if q > left:
        raise BusinessError(
            f"Only {left.normalize():f} {line.item.unit} of {line.item.name} can still be returned from this bill "
            f"(sold {line.quantity.normalize():f}, already returned {line.returned_quantity.normalize():f})."
        )
    amount = (q * line.price).quantize(Decimal("0.01"))

    ret = SaleReturn(sale_id=line.sale_id, sale_item=line, item=line.item, quantity=q, unit_price=line.price, amount=amount)
    if date:
        ret.date = date
    ret.save()

    _move_stock(line.item, q, StockMovement.Kind.SALE_RETURN, f"Sale return {ret.pk} (bill {line.sale_id})")

    sale = Sale.objects.select_for_update().get(pk=line.sale_id)
    sale.returned_amount = min(sale.returned_amount + amount, sale.total_amount)
    sale.recalc()          # pending shrinks, or a refund becomes due to the customer
    sale.save()
    return ret


@transaction.atomic
def create_purchase_return(*, purchase_item, quantity, reason, date=None):
    """The shop sends back some (or all) of one line of a purchase to the supplier."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise BusinessError("Please write the reason for returning (at least 3 letters).")
    if len(reason) > 200:
        raise BusinessError("Reason is too long (max 200 characters).")

    line = PurchaseItem.objects.select_for_update().select_related("item").get(pk=purchase_item.pk)
    q = _to_dec(quantity)
    if q <= 0:
        raise BusinessError("Return quantity must be more than 0.")
    left = line.quantity - line.returned_quantity
    if q > left:
        raise BusinessError(
            f"Only {left.normalize():f} {line.item.unit} of {line.item.name} can still be sent back from this bill "
            f"(bought {line.quantity.normalize():f}, already returned {line.returned_quantity.normalize():f})."
        )
    item = Item.objects.select_for_update().get(pk=line.item_id)
    if item.current_stock < q:
        raise BusinessError(
            f"Only {item.current_stock.normalize():f} {item.unit} of {item.name} is in stock now, "
            f"so {q.normalize():f} cannot be sent back."
        )
    amount = (q * line.cost_price).quantize(Decimal("0.01"))

    ret = PurchaseReturn(purchase_id=line.purchase_id, purchase_item=line, item=item, quantity=q,
                         unit_cost=line.cost_price, amount=amount, reason=reason)
    if date:
        ret.date = date
    ret.save()

    _move_stock(item, -q, StockMovement.Kind.PURCHASE_RETURN, f"Purchase return {ret.pk} (bill {line.purchase_id})")

    purchase = Purchase.objects.select_for_update().get(pk=line.purchase_id)
    purchase.returned_amount = min(purchase.returned_amount + amount, purchase.total_amount)
    purchase.recalc()      # shop owes less, or the supplier has to give money back
    purchase.save()
    return ret
