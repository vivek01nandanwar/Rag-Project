from django.contrib import admin

from . import models

for m in [
    models.Category, models.Customer, models.Supplier, models.Item, models.StockMovement,
    models.Purchase, models.PurchaseItem, models.Sale, models.SaleItem, models.Payment,
]:
    admin.site.register(m)
