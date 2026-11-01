"""Everything the assistant is allowed to DO, in one audited place.

READ tools run immediately.
WRITE tools never write: they validate, build a human-readable summary and *queue a proposal*.
The proposal is executed (inside one database transaction) only after the owner presses Confirm,
by `run_group`. The AI model never touches the database directly.
"""
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone
from rapidfuzz import fuzz, process

from store import services
from store.models import (
    Category, Customer, Item, Payment, Purchase, PurchaseItem, Sale, SaleItem, Supplier, ZERO,
)

from store import reports

from . import vector
from .helpers import HelperError, convert_qty, fmt_date, normalize_unit, parse_date
from .models import CustomField, CustomRecord, CustomTable

MAX_ACTIONS = 8
MAX_TABLES = 20
MAX_FIELDS = 15
CORE_NAMES = {
    "item", "items", "product", "products", "stock", "sale", "sales", "purchase", "purchases",
    "customer", "customers", "supplier", "suppliers", "payment", "payments", "category", "categories",
    "user", "users", "chat", "session",
}
RESERVED_CODES = {"CAT", "CUS", "SUP", "ITM", "MOV", "PUR", "PLI", "SAL", "SLI", "PAY", "SRT", "PRT", "TBL", "FLD", "CHT", "MSG", "ACT", "REC"}


class ToolFail(Exception):
    """A problem the owner should hear about (not a bug). The agent relays it in plain words."""


# ------------------------------------------------------------------ small helpers
def D(x):
    try:
        return Decimal(str(x))
    except InvalidOperation:
        raise ToolFail(f"'{x}' is not a valid number.")


def inr(v):
    s = f"{Decimal(v):,.2f}".rstrip("0").rstrip(".")
    return "₹" + s


def qtys(v):
    return format(Decimal(v).normalize(), "f")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _clip(s, n):
    return str(s or "").strip()[:n]


def _json(obj, limit=4500):
    text = json.dumps(obj, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + '..."(truncated)'


def _aliases(it):
    return [a.strip() for a in it.aliases.split(",") if a.strip()]


def _item_names(it):
    return [it.name] + _aliases(it)


def item_dict(it):
    return {
        "id": it.id, "name": it.name, "unit": it.unit,
        "selling_price": f"{inr(it.selling_price)} per {it.unit}",
        "buying_price": f"{inr(it.cost_price)} per {it.unit}",
        "stock": f"{qtys(it.current_stock)} {it.unit}", "low_stock": it.is_low_stock, "aliases": it.aliases,
        "category": it.category.name if it.category else "", "active": it.is_active,
    }


# ------------------------------------------------------------------ per-request context
class Ctx:
    """Lives for exactly one chat turn. Collects the proposals the agent makes."""

    def __init__(self, user, session, files=None):
        self.user = user
        self.session = session
        self.files = files or []
        self.proposals = []
        self.new_items = {}     # norm name -> dict   (products proposed in this same turn)
        self.new_parties = {}   # (kind, norm name) -> dict
        self.stock_delta = {}   # "id:ITM-1" / "new:name" -> Decimal change inside this turn
        self.virtual = {}       # bill id -> {"returned": Decimal, "paid": Decimal} proposed in this turn (so a return + refund work together)
        self.line_returned = {} # bill line id -> quantity already proposed for return in this turn

    def add(self, tool, args, summary, danger=False):
        if len(self.proposals) >= MAX_ACTIONS:
            raise ToolFail(f"Too many changes at once (max {MAX_ACTIONS}). Finish these first, then ask for the rest.")
        self.proposals.append({"tool": tool, "args": args, "summary": summary, "danger": danger})


# ------------------------------------------------------------------ resolving names -> records
def _rank_items(query, items):
    """Fuzzy rank. Returns [(item, score)] best first, one row per item."""
    r = _norm(query)
    pairs = [(_norm(n), it) for it in items for n in _item_names(it)]
    if not pairs or not r:
        return []
    hits = process.extract(r, [p[0] for p in pairs], scorer=fuzz.WRatio, limit=8)
    best = {}
    for _name, score, idx in hits:
        it = pairs[idx][1]
        if score > best.get(it.id, (None, -1))[1]:
            best[it.id] = (it, score)
    return sorted(best.values(), key=lambda x: -x[1])


def resolve_item(ctx, ref, *, include_inactive=False, allow_new=True):
    """-> ("id", Item) or ("new", dict). Raises ToolFail with helpful candidates."""
    r = _norm(ref)
    if not r:
        raise ToolFail("Product name is missing.")
    it = Item.objects.filter(pk=str(ref).strip().upper()).first()
    if it:
        return "id", it
    if allow_new and r in ctx.new_items:
        return "new", ctx.new_items[r]
    items = list(Item.objects.select_related("category") if include_inactive
                 else Item.objects.select_related("category").filter(is_active=True))
    for it in items:
        if r in {_norm(n) for n in _item_names(it)}:
            return "id", it
    ranked = _rank_items(ref, items)
    if ranked:
        (best, s1) = ranked[0]
        s2 = ranked[1][1] if len(ranked) > 1 else 0
        if s1 >= 90 and s1 - s2 >= 8:
            return "id", best
    close = ", ".join(f"{it.name} ({it.id})" for it, s in ranked[:3] if s >= 60)
    raise ToolFail(
        f"No product matches '{ref}'." + (f" Closest: {close}. Ask the owner which one." if close else "")
        + " If it is a NEW product, ask for its selling price (and buying price / stock), then use create_item first."
    )


def resolve_party(ctx, kind, ref, *, allow_new=True):
    Model = Customer if kind == "customer" else Supplier
    r = _norm(ref)
    if not r:
        raise ToolFail(f"{kind.title()} name is missing.")
    p = Model.objects.filter(pk=str(ref).strip().upper()).first()
    if p:
        return "id", p
    if allow_new and (kind, r) in ctx.new_parties:
        return "new", ctx.new_parties[(kind, r)]
    people = list(Model.objects.all())
    for p in people:
        if _norm(p.name) == r:
            return "id", p
    good = []
    if people:
        hits = process.extract(r, [_norm(p.name) for p in people], scorer=fuzz.WRatio, limit=4)
        good = [(people[i], s) for _n, s, i in hits if s >= 60]
        if good and good[0][1] >= 88 and (len(good) == 1 or good[0][1] - good[1][1] >= 8):
            return "id", good[0][0]
    close = ", ".join(f"{p.name} ({p.id})" for p, _s in good[:3])
    raise ToolFail(
        f"No {kind} named '{ref}'." + (f" Closest: {close}. Ask which one." if close else "")
        + f" To add a new {kind}, use create_party."
    )


def _ref(kind, obj):
    return f"id:{obj.id}" if kind == "id" else f"new:{obj['name']}"


def _load_item(ref):
    kind, _, val = ref.partition(":")
    try:
        return Item.objects.get(pk=val) if kind == "id" else Item.objects.get(name__iexact=val)
    except Item.DoesNotExist:
        raise ToolFail(f"Product '{val}' no longer exists.")


def _load_party(kind, ref):
    Model = Customer if kind == "customer" else Supplier
    how, _, val = ref.partition(":")
    try:
        return Model.objects.get(pk=val) if how == "id" else Model.objects.get(name__iexact=val)
    except Model.DoesNotExist:
        raise ToolFail(f"{kind.title()} '{val}' no longer exists.")


def _available(ctx, kind, obj):
    if kind == "id":
        return obj.current_stock + ctx.stock_delta.get("id:" + obj.id, ZERO)
    return ctx.stock_delta.get("new:" + _norm(obj["name"]), ZERO)


def _bump_stock(ctx, kind, obj, change):
    key = ("id:" + obj.id) if kind == "id" else ("new:" + _norm(obj["name"]))
    ctx.stock_delta[key] = ctx.stock_delta.get(key, ZERO) + change


# ================================================================== READ TOOLS
def r_search_items(ctx, a):
    q = a.query.strip()
    if not q:
        raise ToolFail("Tell me what product to search for.")
    items = list(Item.objects.select_related("category").all())
    out, seen = [], set()
    for it, score in _rank_items(q, items):
        if score >= 60 and it.id not in seen:
            out.append(it); seen.add(it.id)
    for it in items:   # plain substring match as well
        if it.id not in seen and (_norm(q) in _norm(it.name) or _norm(q) in _norm(it.aliases)):
            out.append(it); seen.add(it.id)
    semantic = []
    for item_id in vector.search_items_semantic(q, k=3):   # meaning-based match (e.g. 'cooking oil' -> Sunflower Oil)
        it = next((x for x in items if x.id == item_id), None)
        if it and it.id not in seen:
            semantic.append(it); seen.add(it.id)
    return {"matches": [item_dict(i) for i in out[:6]], "similar_by_meaning": [item_dict(i) for i in semantic]}


def r_list_low_stock(ctx, a):
    return [item_dict(i) for i in Item.objects.select_related("category").filter(is_active=True) if i.is_low_stock]


def r_find_party(ctx, a):
    Model = Customer if a.kind == "customer" else Supplier
    people = list(Model.objects.all())
    q = _norm(a.query)
    if q:
        hits = process.extract(q, [_norm(p.name) for p in people], scorer=fuzz.WRatio, limit=5) if people else []
        people = [people[i] for _n, s, i in hits if s >= 60]
    rows = []
    for p in people[:10]:
        row = {"id": p.id, "name": p.name, "phone": p.phone}
        if a.kind == "customer":
            row.update(owes_shop=p.pending_from_customer, shop_owes_customer=p.refund_due_to_customer)
        else:
            row.update(shop_owes_supplier=p.pending_to_supplier, supplier_owes_shop=p.refund_due_from_supplier)
        rows.append(row)
    return rows


def r_list_pending(ctx, a):
    spec = {
        "customers_owe_shop": (Sale, "customer", "pending_from_customer"),
        "shop_owes_customers": (Sale, "customer", "refund_due_to_customer"),
        "shop_owes_suppliers": (Purchase, "supplier", "pending_to_supplier"),
        "suppliers_owe_shop": (Purchase, "supplier", "refund_due_from_supplier"),
    }[a.kind]
    Model, party_field, field = spec
    rows = []
    for b in Model.objects.select_related(party_field).filter(**{f"{field}__gt": 0}).order_by("date")[:30]:
        p = getattr(b, party_field)
        rows.append({"bill_id": b.id, "party": p.name if p else "?", "party_id": p.id if p else None,
                     "bill_total": b.total_amount, "outstanding": getattr(b, field), "date": b.date})
    return {"rows": rows, "total_outstanding": sum((r["outstanding"] for r in rows), ZERO)}


def r_business_summary(ctx, a):
    today = timezone.localdate()

    def tot(qs, f):
        return qs.aggregate(t=Sum(f))["t"] or ZERO

    value = Item.objects.aggregate(t=Sum(ExpressionWrapper(
        F("current_stock") * F("cost_price"), output_field=DecimalField(max_digits=18, decimal_places=3))))["t"] or ZERO
    return {
        "today_sales": tot(Sale.objects.filter(date=today), "total_amount"),
        "today_purchases": tot(Purchase.objects.filter(date=today), "total_amount"),
        "customers_owe_shop": tot(Sale.objects, "pending_from_customer"),
        "shop_owes_customers": tot(Sale.objects, "refund_due_to_customer"),
        "shop_owes_suppliers": tot(Purchase.objects, "pending_to_supplier"),
        "suppliers_owe_shop": tot(Purchase.objects, "refund_due_from_supplier"),
        "stock_value_at_cost": value, "products": Item.objects.count(),
        "low_stock_count": sum(1 for i in Item.objects.filter(is_active=True) if i.is_low_stock),
    }


def r_recent(ctx, a):
    rows = []
    for s in Sale.objects.select_related("customer").prefetch_related("lines__item")[: a.limit]:
        rows.append({"type": "sale", "id": s.id, "party": s.customer.name if s.customer else "Walk-in", "total": s.total_amount,
                     "pending": s.pending_from_customer, "date": s.date,
                     "items": ", ".join(f"{l.item.name} x{qtys(l.quantity)}" for l in s.lines.all()), "_t": s.created_at})
    for p in Purchase.objects.select_related("supplier").prefetch_related("lines__item")[: a.limit]:
        rows.append({"type": "purchase", "id": p.id, "party": p.supplier.name if p.supplier else "-", "total": p.total_amount,
                     "pending": p.pending_to_supplier, "date": p.date,
                     "items": ", ".join(f"{l.item.name} x{qtys(l.quantity)}" for l in p.lines.all()), "_t": p.created_at})
    rows.sort(key=lambda r: r["_t"], reverse=True)
    for r in rows:
        r.pop("_t")
    return rows[: a.limit]


def r_sales_summary(ctx, a):
    today = timezone.localdate()
    start = today - timedelta(days=a.days - 1)
    sales = Sale.objects.filter(date__gte=start)
    lines = SaleItem.objects.filter(sale__date__gte=start)
    p = reports.profit_between(start, today)   # same formula as the Home tab
    top = lines.values("item__name").annotate(q=Sum("quantity"), r=Sum("line_total")).order_by("-r")[:5]
    return {
        "from": start, "bills": sales.count(), "sales_total": p["total_sales"], "sales_returns": p["sales_returns"],
        "received": sales.aggregate(t=Sum("amount_received"))["t"] or ZERO,
        "buying_cost_of_sold_items": p["buying_cost"],
        "estimated_profit": p["total_profit"],
        "top_items": [{"item": t["item__name"], "quantity": t["q"], "revenue": t["r"]} for t in top],
    }


def r_search_documents(ctx, a):
    hits = vector.search_docs(ctx.session.id, a.query, k=4)
    if not hits:
        return {"matches": [], "note": "Nothing found (or no file was uploaded in this chat)."}
    return {"matches": hits}


def _table(ref):
    r = str(ref).strip()
    t = CustomTable.objects.filter(pk=r.upper()).first() or CustomTable.objects.filter(code=r.upper()).first() \
        or CustomTable.objects.filter(name__iexact=r).first()
    if t:
        return t
    names = list(CustomTable.objects.values_list("name", flat=True))
    hit = process.extractOne(_norm(r), [_norm(n) for n in names], scorer=fuzz.WRatio) if names else None
    if hit and hit[1] >= 88:
        return CustomTable.objects.get(name=names[hit[2]])
    raise ToolFail(f"No register named '{ref}'." + (f" Existing registers: {', '.join(names)}." if names else " There are no extra registers yet."))


def r_list_custom_tables(ctx, a):
    return [{"id": t.id, "name": t.name, "code": t.code, "description": t.description, "records": t.records.count(),
             "fields": [{"name": f.name, "type": f.field_type, "required": f.required, "choices": f.choices} for f in t.fields.all()]}
            for t in CustomTable.objects.prefetch_related("fields")]


def r_list_custom_records(ctx, a):
    t = _table(a.table)
    q = _norm(a.query)
    rows = [{"id": r.id, **r.data} for r in t.records.all()[:500] if not q or q in _norm(json.dumps(r.data, ensure_ascii=False))]
    return {"register": t.name, "rows": rows[: a.limit], "total_matching": len(rows)}


# ------------------------------------------------------------------ prices, orders, dates
def _today():
    return timezone.localdate()


def _conv(q, from_unit, to_unit):
    try:
        return convert_qty(q, from_unit, to_unit)
    except HelperError as e:
        raise ToolFail(str(e))


def _date(raw, *, not_before=None, what="date", for_entry=True):
    try:
        return parse_date(raw, _today(), not_before=not_before, what=what, for_entry=for_entry)
    except HelperError as e:
        raise ToolFail(str(e))


def _to_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").date() if iso else None


def _bill_balance(ctx, bill, is_sale):
    """(still to pay, extra to give back) for a bill, INCLUDING changes already proposed in this same turn."""
    v = ctx.virtual.get(bill.id, {"returned": ZERO, "paid": ZERO})
    paid = (bill.amount_received if is_sale else bill.amount_paid) + v["paid"]
    diff = (bill.total_amount - bill.returned_amount - v["returned"]) - paid
    return max(diff, ZERO), max(-diff, ZERO)


def r_price_quote(ctx, a):
    """Exact price for any quantity, in any unit. The arithmetic is done here, never by the AI."""
    k, it = resolve_item(ctx, a.item, include_inactive=True, allow_new=False)
    given = D(a.quantity) if a.quantity > 0 else Decimal("1")
    q = _conv(given, a.unit, it.unit) if a.unit.strip() else given
    sell = (q * it.selling_price).quantize(Decimal("0.01"))
    buy = (q * it.cost_price).quantize(Decimal("0.01"))
    return {
        "product": it.name, "id": it.id, "unit": it.unit,
        "selling_price": f"{inr(it.selling_price)} per {it.unit}",
        "buying_price": f"{inr(it.cost_price)} per {it.unit}",
        "asked_for": f"{qtys(given)} {a.unit.strip() or it.unit}", "same_as": f"{qtys(q)} {it.unit}",
        "selling_total": sell, "buying_total": buy, "profit_on_this_quantity": sell - buy,
        "in_stock": f"{qtys(it.current_stock)} {it.unit}",
    }


def r_find_bills(ctx, a):
    """Look up real orders (sales or purchases) so the owner can be asked which one."""
    is_sale = a.kind == "sale"
    Model, party_kind = (Sale, "customer") if is_sale else (Purchase, "supplier")
    qs = Model.objects.select_related(party_kind).prefetch_related("lines__item")
    if a.party.strip():
        _k, p = resolve_party(ctx, party_kind, a.party, allow_new=False)
        qs = qs.filter(**{party_kind: p})
    if a.item.strip():
        _k, it = resolve_item(ctx, a.item, include_inactive=True, allow_new=False)
        qs = qs.filter(lines__item=it).distinct()
    if a.date.strip():
        qs = qs.filter(date=_date(a.date, what="order date", for_entry=False))
    if a.date_from.strip():
        qs = qs.filter(date__gte=_date(a.date_from, what="from date", for_entry=False))
    if a.date_to.strip():
        qs = qs.filter(date__lte=_date(a.date_to, what="to date", for_entry=False))

    orders = []
    for b in qs.order_by("-date", "-created_at")[:40]:
        items = [{
            "line_id": l.id, "item": l.item.name, "quantity": l.quantity, "unit": l.item.unit,
            "price": l.price if is_sale else l.cost_price, "already_returned": l.returned_quantity,
            "can_return": l.quantity - l.returned_quantity,
        } for l in b.lines.all()]
        if a.only_returnable and not any(i["can_return"] > 0 for i in items):
            continue
        party = getattr(b, party_kind)
        orders.append({
            "bill_id": b.id, "date": b.date, "party": party.name if party else ("Walk-in" if is_sale else "No supplier"),
            "total": b.total_amount, "returned_so_far": b.returned_amount,
            "paid": b.amount_received if is_sale else b.amount_paid,
            "still_pending": b.pending_from_customer if is_sale else b.pending_to_supplier,
            "refund_due": b.refund_due_to_customer if is_sale else b.refund_due_from_supplier,
            "items": items,
        })
        if len(orders) >= a.limit:
            break
    return {"orders": orders, "count": len(orders),
            "note": "If more than one order fits, list each (id, date, items, total) for the owner and ask which. Never guess."}


# ================================================================== WRITE: prepare (validate + summarise + queue)
# ---------------------------------------------------------------- products
def prep_create_item(ctx, a):
    name = _clip(a.name, 120)
    if not name:
        raise ToolFail("Product name is missing.")
    key = _norm(name)
    if key in ctx.new_items or Item.objects.filter(name__iexact=name).exists():
        raise ToolFail(f"'{name}' already exists. Use update_item, or sell/buy it directly.")
    if min(a.selling_price, a.cost_price, a.opening_stock, a.low_stock_threshold) < 0:
        raise ToolFail("Prices and stock cannot be negative.")
    sp, cp, st = D(a.selling_price), D(a.cost_price), D(a.opening_stock)
    ctx.new_items[key] = {"name": name, "unit": a.unit, "selling_price": sp, "cost_price": cp}
    ctx.stock_delta["new:" + key] = st
    args = {"name": name, "unit": a.unit, "selling_price": str(sp), "cost_price": str(cp), "opening_stock": str(st),
            "aliases": _clip(a.aliases, 250), "category": _clip(a.category, 60),
            "low_stock_threshold": str(D(a.low_stock_threshold)), "emoji": _clip(a.emoji, 4)}
    warn = "  ⚠ selling price is below buying price" if sp < cp else ""
    ctx.add("create_item", args, f"➕ New product: {name} ({a.unit}) · sell {inr(sp)} · buy {inr(cp)} · opening stock {qtys(st)} {a.unit}{warn}")


def run_create_item(args, user):
    if Item.objects.filter(name__iexact=args["name"]).exists():
        raise ToolFail(f"'{args['name']}' already exists.")
    cat = Category.objects.get_or_create(name=args["category"])[0] if args["category"] else None
    it = Item.objects.create(
        name=args["name"], unit=args["unit"], selling_price=D(args["selling_price"]), cost_price=D(args["cost_price"]),
        aliases=args["aliases"], category=cat, low_stock_threshold=D(args["low_stock_threshold"]),
        **({"emoji": args["emoji"]} if args["emoji"] else {}))
    if D(args["opening_stock"]) > 0:
        services.adjust_stock(item=it, new_quantity=D(args["opening_stock"]), note="Opening stock (assistant)")
    return f"Product created: {it.name} ({it.id})"


def prep_update_item(ctx, a):
    kind, it = resolve_item(ctx, a.item, include_inactive=True, allow_new=False)
    changes, diff = {}, []
    if a.name.strip() and a.name.strip() != it.name:
        n = _clip(a.name, 120)
        if Item.objects.filter(name__iexact=n).exclude(pk=it.pk).exists():
            raise ToolFail(f"Another product is already called '{n}'.")
        changes["name"] = n; diff.append(f"name → {n}")
    if a.unit.strip() and a.unit.strip() != it.unit:
        if a.unit not in {u for u, _ in Item.Unit.choices}:
            raise ToolFail("Unit must be one of: kg, g, litre, packet, piece, dozen.")
        if it.current_stock != 0 or it.sale_lines.exists() or it.purchase_lines.exists():
            raise ToolFail("Changing the unit would make old stock and bills wrong. Alternative: create a new product with the new unit.")
        changes["unit"] = a.unit; diff.append(f"unit → {a.unit}")
    for field, label, val in (("selling_price", "selling price", a.selling_price), ("cost_price", "buying price", a.cost_price)):
        if val >= 0 and D(val) != getattr(it, field):
            changes[field] = str(D(val)); diff.append(f"{label} {inr(getattr(it, field))} → {inr(D(val))}")
        elif val < -1:
            raise ToolFail("Price cannot be negative.")
    if a.low_stock_threshold >= 0 and D(a.low_stock_threshold) != it.low_stock_threshold:
        changes["low_stock_threshold"] = str(D(a.low_stock_threshold)); diff.append(f"low-stock alert → {qtys(D(a.low_stock_threshold))}")
    if a.aliases.strip():
        changes["aliases"] = _clip(a.aliases, 250); diff.append(f"nicknames → {changes['aliases']}")
    if a.category.strip():
        changes["category"] = _clip(a.category, 60); diff.append(f"category → {changes['category']}")
    if a.emoji.strip():
        changes["emoji"] = _clip(a.emoji, 4); diff.append(f"sticker → {changes['emoji']}")
    if a.active.strip().lower() in ("yes", "no"):
        want = a.active.strip().lower() == "yes"
        if want != it.is_active:
            changes["is_active"] = want; diff.append("shown in lists again" if want else "hidden from lists")
    if not changes:
        raise ToolFail(f"Nothing to change for {it.name}.")
    ctx.add("update_item", {"item": f"id:{it.id}", "changes": changes}, f"✏️ Update {it.name}: " + "; ".join(diff))


def run_update_item(args, user):
    it = _load_item(args["item"])
    ch = args["changes"]
    for f in ("name", "unit", "aliases", "emoji", "is_active"):
        if f in ch:
            setattr(it, f, ch[f])
    for f in ("selling_price", "cost_price", "low_stock_threshold"):
        if f in ch:
            setattr(it, f, D(ch[f]))
    if "category" in ch:
        it.category = Category.objects.get_or_create(name=ch["category"])[0]
    it.save()
    return f"Updated {it.name} ({it.id})"


def prep_delete_item(ctx, a):
    kind, it = resolve_item(ctx, a.item, include_inactive=True, allow_new=False)
    if it.sale_lines.exists() or it.purchase_lines.exists():
        raise ToolFail(f"{it.name} appears in past bills, so deleting it would break your records. "
                       "Alternative: hide it with update_item (active='no').")
    ctx.add("delete_item", {"item": f"id:{it.id}"}, f"🗑️ Delete product {it.name} ({it.id}) — it has no bills, only {qtys(it.current_stock)} {it.unit} stock", danger=True)


def run_delete_item(args, user):
    it = _load_item(args["item"])
    if it.sale_lines.exists() or it.purchase_lines.exists():
        raise ToolFail(f"{it.name} now has bills and cannot be deleted.")
    name = it.name
    it.delete()
    return f"Deleted product {name}"


# ---------------------------------------------------------------- customers / suppliers
def _check_phone(phone):
    phone = _clip(phone, 20)
    if phone and not re.fullmatch(r"[0-9+\-\s]{6,20}", phone):
        raise ToolFail("Phone number looks wrong (digits, +, - and spaces only).")
    return phone


def prep_create_party(ctx, a):
    Model = Customer if a.kind == "customer" else Supplier
    name = _clip(a.name, 120)
    if not name:
        raise ToolFail("Name is missing.")
    key = (a.kind, _norm(name))
    if key in ctx.new_parties or Model.objects.filter(name__iexact=name).exists():
        raise ToolFail(f"A {a.kind} called '{name}' already exists. Use them directly.")
    phone = _check_phone(a.phone)
    ctx.new_parties[key] = {"name": name}
    ctx.add("create_party", {"kind": a.kind, "name": name, "phone": phone, "address": _clip(a.address, 200)},
            f"👤 New {a.kind}: {name}" + (f" · {phone}" if phone else ""))


def run_create_party(args, user):
    Model = Customer if args["kind"] == "customer" else Supplier
    if Model.objects.filter(name__iexact=args["name"]).exists():
        raise ToolFail(f"'{args['name']}' already exists.")
    p = Model.objects.create(name=args["name"], phone=args["phone"], address=args["address"])
    return f"{args['kind'].title()} added: {p.name} ({p.id})"


def prep_update_party(ctx, a):
    kind, p = resolve_party(ctx, a.kind, a.party, allow_new=False)
    ch, diff = {}, []
    if a.new_name.strip() and a.new_name.strip() != p.name:
        ch["name"] = _clip(a.new_name, 120); diff.append(f"name → {ch['name']}")
    if a.phone.strip():
        ch["phone"] = _check_phone(a.phone); diff.append(f"phone → {ch['phone']}")
    if a.address.strip():
        ch["address"] = _clip(a.address, 200); diff.append(f"address → {ch['address']}")
    if not ch:
        raise ToolFail("Nothing to change.")
    ctx.add("update_party", {"kind": a.kind, "party": f"id:{p.id}", "changes": ch}, f"✏️ Update {a.kind} {p.name}: " + "; ".join(diff))


def run_update_party(args, user):
    p = _load_party(args["kind"], args["party"])
    for f, v in args["changes"].items():
        setattr(p, f, v)
    p.save()
    return f"Updated {args['kind']} {p.name} ({p.id})"


def prep_delete_party(ctx, a):
    kind, p = resolve_party(ctx, a.kind, a.party, allow_new=False)
    has = p.sales.exists() if a.kind == "customer" else p.purchases.exists()
    if has:
        raise ToolFail(f"{p.name} has bills (and maybe pending money), so deleting would erase that history. "
                       "Alternative: just keep them, or rename with update_party.")
    ctx.add("delete_party", {"kind": a.kind, "party": f"id:{p.id}"}, f"🗑️ Delete {a.kind} {p.name} ({p.id}) — no bills yet", danger=True)


def run_delete_party(args, user):
    p = _load_party(args["kind"], args["party"])
    if (p.sales if args["kind"] == "customer" else p.purchases).exists():
        raise ToolFail(f"{p.name} now has bills and cannot be deleted.")
    name = p.name
    p.delete()
    return f"Deleted {args['kind']} {name}"


# ---------------------------------------------------------------- sales / purchases
def _prep_lines(ctx, lines, *, selling):
    if not lines:
        raise ToolFail("Add at least one product.")
    out, text, total = [], [], ZERO
    for ln in lines:
        kind, it = resolve_item(ctx, ln.item)
        name = it.name if kind == "id" else it["name"]
        unit = it.unit if kind == "id" else it["unit"]
        given = D(ln.quantity)
        if given <= 0:
            raise ToolFail("Quantity must be more than 0.")
        if ln.price < 0 or ln.total < 0:
            raise ToolFail("Price cannot be negative.")
        if ln.unit.strip() and normalize_unit(ln.unit) != unit:       # e.g. 500 g of a kg product
            q = _conv(given, ln.unit, unit)
            shown = f"{qtys(q)} {unit} (= {qtys(given)} {ln.unit.strip()})"
        else:
            q = given.quantize(Decimal("0.001"))
            if q <= 0:
                raise ToolFail("That quantity is too small to record (smallest is 0.001).")
            shown = f"{qtys(q)} {unit}"
        default = (it.selling_price if selling else it.cost_price) if kind == "id" else (it["selling_price"] if selling else it["cost_price"])
        if ln.total > 0:                       # the owner gave the money for the whole line: "500 g for ₹60"
            price = (D(ln.total) / q).quantize(Decimal("0.01"))
        elif ln.price > 0:
            price = D(ln.price)
        else:
            price = default
        if selling:
            avail = _available(ctx, kind, it)
            if q > avail:
                raise ToolFail(f"Only {qtys(avail)} {unit} of {name} available, cannot sell {qtys(q)}. "
                               "Offer to record a purchase or fix the stock count first.")
            _bump_stock(ctx, kind, it, -q)
        else:
            _bump_stock(ctx, kind, it, q)
        line_total = (q * price).quantize(Decimal("0.01"))
        total += line_total
        out.append({"item": _ref(kind, it), "quantity": str(q), "price": str(price)})
        text.append(f"{shown} {name} × {inr(price)}/{unit} = {inr(line_total)}")
    return out, text, total


def prep_record_sale(ctx, a):
    cust_ref, cust_name = None, "Walk-in customer"
    if a.customer.strip():
        k, c = resolve_party(ctx, "customer", a.customer)
        cust_ref, cust_name = _ref(k, c), (c.name if k == "id" else c["name"])
    d = _date(a.date, what="sale date")
    lines, text, total = _prep_lines(ctx, a.lines, selling=True)
    received = total if a.amount_received < 0 else D(a.amount_received)
    pending, refund = max(total - received, ZERO), max(received - total, ZERO)
    if (pending or refund) and not cust_ref:
        raise ToolFail("Udhaar or change owed needs a customer. Ask the owner who the customer is.")
    mode = a.payment_mode.strip().lower()
    if mode not in ("cash", "upi", "udhaar"):
        mode = "udhaar" if received == 0 else "cash"
    tail = f" · received {inr(received)}" + (f" · PENDING {inr(pending)}" if pending else "") + (f" · RETURN {inr(refund)} to customer" if refund else "")
    ctx.add("record_sale", {"customer": cust_ref, "lines": lines, "amount_received": str(received),
                            "payment_mode": mode, "date": d.isoformat()},
            f"🧾 Sale to {cust_name} · 📅 {fmt_date(d, _today())}:\n  • " + "\n  • ".join(text) + f"\n  Total {inr(total)}{tail}")


def run_record_sale(args, user):
    customer = _load_party("customer", args["customer"]) if args.get("customer") else None
    lines = [{"item": _load_item(l["item"]), "quantity": l["quantity"], "price": l["price"]} for l in args["lines"]]
    s = services.create_sale(customer=customer, lines=lines, amount_received=D(args["amount_received"]),
                             payment_mode=args["payment_mode"], notes="via assistant", date=_to_date(args.get("date")))
    extra = f", pending {inr(s.pending_from_customer)}" if s.pending_from_customer else ""
    return f"Sale {s.id} saved ({s.date:%d %b %Y}) · total {inr(s.total_amount)}{extra}"


def prep_record_purchase(ctx, a):
    sup_ref, sup_name = None, "No supplier"
    if a.supplier.strip():
        k, s = resolve_party(ctx, "supplier", a.supplier)
        sup_ref, sup_name = _ref(k, s), (s.name if k == "id" else s["name"])
    d = _date(a.date, what="purchase date")
    lines, text, total = _prep_lines(ctx, a.lines, selling=False)
    paid = total if a.amount_paid < 0 else D(a.amount_paid)
    pending, refund = max(total - paid, ZERO), max(paid - total, ZERO)
    if (pending or refund) and not sup_ref:
        raise ToolFail("Unpaid or extra-paid amounts need a supplier. Ask the owner who the supplier is.")
    tail = f" · paid {inr(paid)}" + (f" · STILL TO PAY {inr(pending)}" if pending else "") + (f" · supplier must RETURN {inr(refund)}" if refund else "")
    ctx.add("record_purchase", {"supplier": sup_ref, "lines": [{"item": l["item"], "quantity": l["quantity"], "cost_price": l["price"]} for l in lines],
                                "amount_paid": str(paid), "date": d.isoformat()},
            f"🚚 Purchase from {sup_name} · 📅 {fmt_date(d, _today())}:\n  • " + "\n  • ".join(text) + f"\n  Total {inr(total)}{tail}")


def run_record_purchase(args, user):
    supplier = _load_party("supplier", args["supplier"]) if args.get("supplier") else None
    lines = [{"item": _load_item(l["item"]), "quantity": l["quantity"], "cost_price": l["cost_price"]} for l in args["lines"]]
    p = services.create_purchase(supplier=supplier, lines=lines, amount_paid=D(args["amount_paid"]),
                                 notes="via assistant", date=_to_date(args.get("date")))
    extra = f", still to pay {inr(p.pending_to_supplier)}" if p.pending_to_supplier else ""
    return f"Purchase {p.id} saved ({p.date:%d %b %Y}) · total {inr(p.total_amount)}{extra}"


SETTLE = {  # action: (party kind, bill model, outstanding field, payment direction, verb)
    "receive_payment": ("customer", Sale, "pending_from_customer", "in", "Receive {a} from {p}"),
    "give_refund": ("customer", Sale, "refund_due_to_customer", "out", "Return {a} to {p}"),
    "pay_supplier": ("supplier", Purchase, "pending_to_supplier", "out", "Pay {a} to {p}"),
    "receive_refund": ("supplier", Purchase, "refund_due_from_supplier", "in", "Get back {a} from {p}"),
}
_PENDING_ACTIONS = {"receive_payment", "pay_supplier"}   # these reduce a *pending* balance; the other two a *refund* balance


def _open_bills(action, party, bill_id=""):
    kind, Model, field, _d, _v = SETTLE[action]
    qs = Model.objects.filter(**{kind: party, f"{field}__gt": 0}).order_by("date", "created_at")
    return qs.filter(pk=bill_id) if bill_id else qs


def _outstanding(ctx, action, bill):
    pending, refund = _bill_balance(ctx, bill, SETTLE[action][0] == "customer")
    return pending if action in _PENDING_ACTIONS else refund


def prep_settle_dues(ctx, a):
    kind, Model, field, direction, verb = SETTLE[a.action]
    _k, p = resolve_party(ctx, kind, a.party, allow_new=False)
    amount = D(a.amount)
    if amount <= 0:
        raise ToolFail("Amount must be more than 0.")
    bill_id = a.bill.strip().upper()
    bills = list(Model.objects.filter(**{kind: p}).order_by("date", "created_at"))
    if bill_id:
        bills = [b for b in bills if b.id == bill_id]
        if not bills:
            raise ToolFail(f"Bill {bill_id} does not belong to {p.name}.")
    # balances include a return proposed earlier in this same turn (so "return + money received" works together)
    rows = [(b, _outstanding(ctx, a.action, b)) for b in bills]
    rows = [(b, o) for b, o in rows if o > 0]
    outstanding = sum((o for _b, o in rows), ZERO)
    if outstanding <= 0:
        raise ToolFail(f"{p.name} has nothing pending for this" + (f" on {bill_id}" if bill_id else "")
                       + ". (If the bill still had unpaid money, a return only reduces that unpaid amount, so no cash moves.)")
    if amount > outstanding:
        raise ToolFail(f"{p.name} only has {inr(outstanding)} pending for this, not {inr(amount)}.")
    left, grows = amount, a.action in _PENDING_ACTIONS
    for b, o in rows:                                   # remember it, so a second settlement in this turn sees the new balance
        if left <= 0:
            break
        part = min(left, o)
        ctx.virtual.setdefault(b.id, {"returned": ZERO, "paid": ZERO})["paid"] += part if grows else -part
        left -= part
    ctx.add("settle_dues", {"action": a.action, "party": f"id:{p.id}", "amount": str(amount), "bill": bill_id},
            f"💰 {verb.format(a=inr(amount), p=p.name)}" + (f" (bill {bill_id})" if bill_id else "")
            + f"  · pending {inr(outstanding)} → {inr(outstanding - amount)}")


def run_settle_dues(args, user):
    kind, Model, field, direction, verb = SETTLE[args["action"]]
    p = _load_party(kind, args["party"])
    left = D(args["amount"])
    for bill in list(_open_bills(args["action"], p, args.get("bill", ""))):
        if left <= 0:
            break
        part = min(left, getattr(bill, field))
        if kind == "customer":
            services.add_payment(sale=bill, direction=direction, amount=part, note="via assistant")
        else:
            services.add_payment(purchase=bill, direction=direction, amount=part, note="via assistant")
        left -= part
    if left > 0:
        raise ToolFail("The pending amount changed while you were confirming. Please ask again.")
    return verb.format(a=inr(D(args["amount"])), p=p.name) + " ✔"


# ---------------------------------------------------------------- returns (purchase -> supplier, sale -> customer)
def _pick_line(ctx, bill, item_ref):
    _k, it = resolve_item(ctx, item_ref, include_inactive=True, allow_new=False)
    lines = [l for l in bill.lines.select_related("item") if l.item_id == it.id]
    if not lines:
        on_bill = ", ".join(f"{l.item.name} {qtys(l.quantity)} {l.item.unit}" for l in bill.lines.select_related("item"))
        raise ToolFail(f"{it.name} is not on bill {bill.id}. That bill has: {on_bill}.")
    for l in lines:
        left = l.quantity - l.returned_quantity - ctx.line_returned.get(l.id, ZERO)
        if left > 0:
            return l, it, left
    raise ToolFail(f"All the {it.name} on bill {bill.id} has already been returned.")


def _return_common(ctx, a, *, is_sale):
    Model = Sale if is_sale else Purchase
    bill = (Model.objects.select_related("customer" if is_sale else "supplier")
            .filter(pk=str(a.bill).strip().upper()).first())
    if bill is None:
        raise ToolFail(f"There is no bill with id '{a.bill}'. Use find_bills to find the order and confirm it with the owner.")
    line, it, left = _pick_line(ctx, bill, a.item)
    given = D(a.quantity)
    if given <= 0:
        raise ToolFail("Return quantity must be more than 0.")
    if a.unit.strip() and normalize_unit(a.unit) != it.unit:
        q = _conv(given, a.unit, it.unit)
    else:
        q = given.quantize(Decimal("0.001"))
        if q <= 0:
            raise ToolFail("That quantity is too small to record (smallest is 0.001).")
    if q > left:
        raise ToolFail(f"Only {qtys(left)} {it.unit} of {it.name} can still be returned from {bill.id} (the bill had {qtys(line.quantity)} {it.unit}).")
    d = _date(a.date, not_before=bill.date, what="return date")
    return bill, line, it, q, given, d


def _after_text(pending, refund, is_sale):
    parts = []
    if pending:
        parts.append(f"{'customer' if is_sale else 'shop'} still owes {inr(pending)}")
    if refund:
        parts.append(f"{'shop must give back' if is_sale else 'supplier must give back'} {inr(refund)}")
    return " · ".join(parts) if parts else "bill fully settled"


def prep_record_purchase_return(ctx, a):
    reason = _clip(a.reason, 200)
    if len(reason) < 3:
        raise ToolFail("A purchase return needs a reason (for example: damaged, expired, wrong item). Ask the owner.")
    bill, line, it, q, given, d = _return_common(ctx, a, is_sale=False)
    stock = it.current_stock + ctx.stock_delta.get("id:" + it.id, ZERO)
    if q > stock:
        raise ToolFail(f"Only {qtys(stock)} {it.unit} of {it.name} is in stock now, so {qtys(q)} cannot be sent back. Check the stock count first.")
    amount = (q * line.cost_price).quantize(Decimal("0.01"))
    _bump_stock(ctx, "id", it, -q)
    ctx.line_returned[line.id] = ctx.line_returned.get(line.id, ZERO) + q
    ctx.virtual.setdefault(bill.id, {"returned": ZERO, "paid": ZERO})["returned"] += amount
    pending, refund = _bill_balance(ctx, bill, False)
    who = bill.supplier.name if bill.supplier else "no supplier"
    shown = f"{qtys(q)} {it.unit}" + (f" (= {qtys(given)} {a.unit.strip()})" if a.unit.strip() and normalize_unit(a.unit) != it.unit else "")
    ctx.add("record_purchase_return",
            {"purchase_item": line.id, "quantity": str(q), "reason": reason, "date": d.isoformat()},
            f"↩️ Purchase return to {who} · bill {bill.id} ({bill.date:%d %b %Y}, total {inr(bill.total_amount)}):\n"
            f"  • {shown} {it.name} × {inr(line.cost_price)}/{it.unit} = {inr(amount)} · reason: {reason}\n"
            f"  📅 Return date: {fmt_date(d, _today())} · stock goes down by {qtys(q)} {it.unit}\n"
            f"  After this: {_after_text(pending, refund, False)}")


def run_record_purchase_return(args, user):
    try:
        line = PurchaseItem.objects.get(pk=args["purchase_item"])
    except PurchaseItem.DoesNotExist:
        raise ToolFail("That purchase line no longer exists.")
    r = services.create_purchase_return(purchase_item=line, quantity=D(args["quantity"]), reason=args["reason"],
                                        date=_to_date(args.get("date")))
    return f"Purchase return {r.id} saved: {qtys(r.quantity)} {r.item.unit} {r.item.name} sent back ({inr(r.amount)})"


def prep_record_sale_return(ctx, a):
    bill, line, it, q, given, d = _return_common(ctx, a, is_sale=True)
    amount = (q * line.price).quantize(Decimal("0.01"))
    _bump_stock(ctx, "id", it, q)
    ctx.line_returned[line.id] = ctx.line_returned.get(line.id, ZERO) + q
    ctx.virtual.setdefault(bill.id, {"returned": ZERO, "paid": ZERO})["returned"] += amount
    pending, refund = _bill_balance(ctx, bill, True)
    who = bill.customer.name if bill.customer else "Walk-in customer"
    shown = f"{qtys(q)} {it.unit}" + (f" (= {qtys(given)} {a.unit.strip()})" if a.unit.strip() and normalize_unit(a.unit) != it.unit else "")
    ctx.add("record_sale_return",
            {"sale_item": line.id, "quantity": str(q), "date": d.isoformat()},
            f"↩️ Sales return from {who} · bill {bill.id} ({bill.date:%d %b %Y}, total {inr(bill.total_amount)}):\n"
            f"  • {shown} {it.name} × {inr(line.price)}/{it.unit} = {inr(amount)}\n"
            f"  📅 Return date: {fmt_date(d, _today())} · stock goes up by {qtys(q)} {it.unit}\n"
            f"  After this: {_after_text(pending, refund, True)}")


def run_record_sale_return(args, user):
    try:
        line = SaleItem.objects.get(pk=args["sale_item"])
    except SaleItem.DoesNotExist:
        raise ToolFail("That sale line no longer exists.")
    r = services.create_sale_return(sale_item=line, quantity=D(args["quantity"]), date=_to_date(args.get("date")))
    return f"Sales return {r.id} saved: {qtys(r.quantity)} {r.item.unit} {r.item.name} taken back ({inr(r.amount)})"


def prep_adjust_stock(ctx, a):
    kind, it = resolve_item(ctx, a.item, allow_new=False)
    if a.new_quantity < 0:
        raise ToolFail("Stock cannot be negative.")
    new = D(a.new_quantity)
    ctx.add("adjust_stock", {"item": f"id:{it.id}", "new_quantity": str(new), "note": _clip(a.note, 150) or "Recount (assistant)"},
            f"📦 Recount {it.name}: {qtys(it.current_stock)} {it.unit} → {qtys(new)} {it.unit}")


def run_adjust_stock(args, user):
    it = _load_item(args["item"])
    services.adjust_stock(item=it, new_quantity=D(args["new_quantity"]), note=args["note"])
    return f"Stock of {it.name} set to {args['new_quantity']}"


# ---------------------------------------------------------------- extra registers (custom tables)
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _&\-]{1,39}$")
_FIELD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _&\-]{0,39}$")


def _make_code(name):
    letters = re.sub(r"[^A-Za-z]", "", name).upper() or "REG"
    taken = set(CustomTable.objects.values_list("code", flat=True)) | RESERVED_CODES
    for n in (3, 4, 5):
        if letters[:n] not in taken and len(letters[:n]) == n:
            return letters[:n]
    base = (letters + "XXX")[:3]
    for i in range(1, 100):
        c = f"{base}{i}"
        if c not in taken:
            return c
    raise ToolFail("Could not create a unique id prefix for this register.")


def _clean_field(f, existing_names):
    name = _clip(f.name, 40)
    if not _FIELD_RE.match(name):
        raise ToolFail(f"Field name '{f.name}' is not allowed (letters, numbers, spaces, - _ & only).")
    if _norm(name) in existing_names or _norm(name) == "id":
        raise ToolFail(f"Field '{name}' appears twice.")
    choices = []
    if f.type == "choice":
        choices = [c.strip()[:40] for c in f.choices.split(",") if c.strip()][:10]
        if len(choices) < 2:
            raise ToolFail(f"Field '{name}' is a choice field: give 2 or more options.")
    return {"name": name, "type": f.type, "required": bool(f.required), "choices": choices}


def prep_create_custom_table(ctx, a):
    name = _clip(a.name, 40)
    if not _NAME_RE.match(name):
        raise ToolFail("Register name must be 2–40 characters: letters, numbers, spaces, - _ & only.")
    if _norm(name) in CORE_NAMES or _norm(name).rstrip("s") in CORE_NAMES:
        raise ToolFail(f"'{name}' already exists in the shop system and cannot be re-created. Use a different name, e.g. 'Staff {name}'.")
    if CustomTable.objects.filter(name__iexact=name).exists():
        raise ToolFail(f"A register called '{name}' already exists. Add records to it, or add a field.")
    if CustomTable.objects.count() >= MAX_TABLES:
        raise ToolFail(f"Maximum {MAX_TABLES} extra registers reached.")
    if not 1 <= len(a.fields) <= MAX_FIELDS:
        raise ToolFail(f"A register needs 1 to {MAX_FIELDS} fields.")
    seen, fields = set(), []
    for f in a.fields:
        cf = _clean_field(f, seen)
        seen.add(_norm(cf["name"]))
        fields.append(cf)
    code = _make_code(name)
    desc = ", ".join(f"{f['name']} ({f['type'].replace('_', '/')}{', required' if f['required'] else ''})" for f in fields)
    ctx.add("create_custom_table", {"name": name, "description": _clip(a.description, 200), "fields": fields},
            f"🗂️ New register “{name}” (ids like {code}-0001) with fields: {desc}")


def run_create_custom_table(args, user):
    if CustomTable.objects.filter(name__iexact=args["name"]).exists():
        raise ToolFail(f"Register '{args['name']}' already exists.")
    t = CustomTable.objects.create(name=args["name"], code=_make_code(args["name"]), description=args["description"], created_by=user)
    for i, f in enumerate(args["fields"]):
        CustomField.objects.create(table=t, name=f["name"], field_type=f["type"], required=f["required"], choices=f["choices"], position=i)
    return f"Register created: {t.name} ({t.id}) — records will be numbered {t.code}-0001, {t.code}-0002…"


def prep_add_custom_field(ctx, a):
    t = _table(a.table)
    if t.fields.count() >= MAX_FIELDS:
        raise ToolFail(f"{t.name} already has {MAX_FIELDS} fields.")
    cf = _clean_field(a.field, {_norm(f.name) for f in t.fields.all()})
    if cf["required"] and t.records.exists():
        raise ToolFail("A new field cannot be 'required' while the register already has records. Add it as optional.")
    ctx.add("add_custom_field", {"table": f"id:{t.id}", "field": cf}, f"➕ Add field “{cf['name']}” ({cf['type']}) to register {t.name}")


def run_add_custom_field(args, user):
    t = _table(args["table"].split(":", 1)[1])
    f = args["field"]
    CustomField.objects.create(table=t, name=f["name"], field_type=f["type"], required=f["required"], choices=f["choices"], position=t.fields.count())
    return f"Field {f['name']} added to {t.name}"


def prep_delete_custom_table(ctx, a):
    t = _table(a.table)
    ctx.add("delete_custom_table", {"table": f"id:{t.id}"}, f"🗑️ Delete register “{t.name}” and its {t.records.count()} record(s). Shop sales/stock are NOT affected.", danger=True)


def run_delete_custom_table(args, user):
    t = _table(args["table"].split(":", 1)[1])
    name = t.name
    t.delete()
    return f"Register {name} deleted"


def _coerce(field, raw):
    v = str(raw).strip()
    if v == "":
        return ""
    if field.field_type == "number":
        try:
            return float(Decimal(v.replace(",", "")))
        except InvalidOperation:
            raise ToolFail(f"'{field.name}' must be a number (got '{raw}').")
    if field.field_type == "date":
        from datetime import datetime
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(v, fmt).date().isoformat()
            except ValueError:
                pass
        raise ToolFail(f"'{field.name}' must be a date like 2026-10-29 (got '{raw}').")
    if field.field_type == "yes_no":
        if v.lower() in ("yes", "y", "true", "1", "haan", "ha", "han"):
            return "yes"
        if v.lower() in ("no", "n", "false", "0", "nahi", "na"):
            return "no"
        raise ToolFail(f"'{field.name}' must be yes or no.")
    if field.field_type == "choice":
        for c in field.choices:
            if c.lower() == v.lower():
                return c
        raise ToolFail(f"'{field.name}' must be one of: {', '.join(field.choices)}.")
    return v[:500]


def _values_to_data(table, values, *, require_all):
    fields = {_norm(f.name): f for f in table.fields.all()}
    data = {}
    for fv in values:
        f = fields.get(_norm(fv.field))
        if not f:
            raise ToolFail(f"{table.name} has no field '{fv.field}'. Fields are: {', '.join(x.name for x in fields.values())}.")
        data[f.name] = _coerce(f, fv.value)
    if require_all:
        missing = [f.name for f in fields.values() if f.required and data.get(f.name, "") == ""]
        if missing:
            raise ToolFail(f"Missing required: {', '.join(missing)}. Ask the owner for them.")
    return data


def prep_add_custom_record(ctx, a):
    t = _table(a.table)
    data = _values_to_data(t, a.values, require_all=True)
    if not any(v != "" for v in data.values()):
        raise ToolFail("The record is empty.")
    ctx.add("add_custom_record", {"table": f"id:{t.id}", "data": data}, f"📝 Add to {t.name}: " + ", ".join(f"{k}: {v}" for k, v in data.items()))


def run_add_custom_record(args, user):
    t = _table(args["table"].split(":", 1)[1])
    r = CustomRecord.objects.create(table=t, data=args["data"], created_by=user)
    return f"Saved in {t.name}: {r.id}"


def _record(rid):
    r = CustomRecord.objects.select_related("table").filter(pk=str(rid).strip().upper()).first()
    if not r:
        raise ToolFail(f"No record with id '{rid}'. Use list_custom_records to find the id.")
    return r


def prep_update_custom_record(ctx, a):
    r = _record(a.record_id)
    data = _values_to_data(r.table, a.values, require_all=False)
    if not data:
        raise ToolFail("Nothing to change.")
    ctx.add("update_custom_record", {"record": r.id, "data": data}, f"✏️ Update {r.id} in {r.table.name}: " + ", ".join(f"{k} → {v}" for k, v in data.items()))


def run_update_custom_record(args, user):
    r = _record(args["record"])
    r.data = {**r.data, **args["data"]}
    r.save()
    return f"Updated {r.id}"


def prep_delete_custom_record(ctx, a):
    r = _record(a.record_id)
    ctx.add("delete_custom_record", {"record": r.id}, f"🗑️ Delete {r.id} from {r.table.name}: " + ", ".join(f"{k}: {v}" for k, v in r.data.items()), danger=True)


def run_delete_custom_record(args, user):
    r = _record(args["record"])
    rid = r.id
    r.delete()
    return f"Deleted {rid}"


# ================================================================== registry + executor
WRITE_OPS = {
    "create_item": (prep_create_item, run_create_item),
    "update_item": (prep_update_item, run_update_item),
    "delete_item": (prep_delete_item, run_delete_item),
    "create_party": (prep_create_party, run_create_party),
    "update_party": (prep_update_party, run_update_party),
    "delete_party": (prep_delete_party, run_delete_party),
    "record_sale": (prep_record_sale, run_record_sale),
    "record_purchase": (prep_record_purchase, run_record_purchase),
    "settle_dues": (prep_settle_dues, run_settle_dues),
    "record_purchase_return": (prep_record_purchase_return, run_record_purchase_return),
    "record_sale_return": (prep_record_sale_return, run_record_sale_return),
    "adjust_stock": (prep_adjust_stock, run_adjust_stock),
    "create_custom_table": (prep_create_custom_table, run_create_custom_table),
    "add_custom_field": (prep_add_custom_field, run_add_custom_field),
    "delete_custom_table": (prep_delete_custom_table, run_delete_custom_table),
    "add_custom_record": (prep_add_custom_record, run_add_custom_record),
    "update_custom_record": (prep_update_custom_record, run_update_custom_record),
    "delete_custom_record": (prep_delete_custom_record, run_delete_custom_record),
}
ITEM_CHANGING = {"create_item", "update_item", "delete_item"}


def run_group(group, user):
    """Execute every action of a confirmed group, all-or-nothing. Returns a list of result lines."""
    results = []
    with transaction.atomic():
        for act in group.actions:
            op = WRITE_OPS.get(act["tool"])
            if not op:
                raise ToolFail("Unknown action in proposal.")
            try:
                results.append(op[1](act["args"], user))
            except services.BusinessError as e:
                raise ToolFail(str(e))
    return results
