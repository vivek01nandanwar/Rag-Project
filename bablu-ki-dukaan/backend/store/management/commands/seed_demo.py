from decimal import Decimal as D

from django.core.management.base import BaseCommand

from store import services
from store.models import Category, Customer, Item, Supplier


ITEMS = [
    # name, aliases, category, emoji, unit, sell, cost, stock, low
    ("Sugar", "cheeni, shakkar", "Grocery", "🍬", "kg", 46, 41, 40, 10),
    ("Basmati Rice", "chawal, rice", "Grocery", "🍚", "kg", 95, 82, 60, 15),
    ("Toor Dal", "dal, arhar", "Grocery", "🫘", "kg", 150, 132, 25, 8),
    ("Atta", "wheat flour, gehu atta", "Grocery", "🌾", "kg", 42, 36, 80, 20),
    ("Ghee", "ghi, desi ghee", "Dairy", "🧈", "kg", 600, 520, 12, 5),
    ("Milk Packet", "doodh", "Dairy", "🥛", "packet", 28, 25, 30, 10),
    ("Sunflower Oil", "tel, oil", "Grocery", "🛢️", "litre", 135, 120, 35, 10),
    ("Tea Powder", "chai patti", "Grocery", "🍵", "packet", 120, 100, 18, 6),
    ("Biscuit Packet", "biscuit", "Snacks", "🍪", "packet", 10, 8, 100, 25),
    ("Bath Soap", "sabun", "Household", "🧼", "piece", 38, 31, 45, 12),
    ("Detergent Powder", "surf, washing powder", "Household", "🧺", "packet", 85, 72, 4, 8),
    ("Notebook", "copy", "Stationery", "📒", "piece", 40, 30, 3, 10),
]
CATEGORY_EMOJI = {"Grocery": "🛒", "Dairy": "🥛", "Snacks": "🍿", "Household": "🧹", "Stationery": "✏️"}


class Command(BaseCommand):
    help = "Fill the shop with demo products, a few customers and suppliers."

    def handle(self, *args, **options):
        if Item.objects.exists():
            self.stdout.write("Data already exists, skipping.")
            return
        for n, e in CATEGORY_EMOJI.items():
            Category.objects.get_or_create(name=n, defaults={"emoji": e})
        for name, al, cat, emoji, unit, sell, cost, _stock, low in ITEMS:
            Item.objects.create(
                name=name, aliases=al, category=Category.objects.get(name=cat), emoji=emoji,
                unit=unit, selling_price=sell, cost_price=cost, low_stock_threshold=low,
            )
        sup = Supplier.objects.create(name="Sharma Wholesale", phone="9876500001", address="Mandi Road")
        Supplier.objects.create(name="Patel Dairy Agency", phone="9876500002", address="Station Road")
        Customer.objects.create(name="Sharma ji", phone="9876511111")
        Customer.objects.create(name="Meena ben", phone="9876522222")

        # opening stock goes through the same purchase rules, so stock history is clean
        lines = [{"item": Item.objects.get(name=r[0]), "quantity": r[7], "cost_price": r[6]} for r in ITEMS]
        total = sum(D(str(r[7])) * D(str(r[6])) for r in ITEMS)
        services.create_purchase(supplier=sup, lines=lines, amount_paid=total - D("2000"), notes="Opening stock")
        self.stdout.write(self.style.SUCCESS("Demo data ready for Bablu ki dukaan 🎉"))
