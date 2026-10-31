from django.core.management.base import BaseCommand

from store.models import SaleItem


class Command(BaseCommand):
    help = "One-time: give OLD sale lines a buying price (the product's current buying price) so profit can be calculated."

    def handle(self, *args, **options):
        n = 0
        for line in SaleItem.objects.select_related("item").filter(cost_price=0):
            line.cost_price = line.item.cost_price
            line.save(update_fields=["cost_price"])
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {n} old sale line(s)."))
