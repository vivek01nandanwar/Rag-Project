"""Profit reporting. One place, used by the Home tab and by the AI assistant.

    Total Profit = Total Sales - Total Sales Returns - Buying price of the products that were sold

* Total Sales          : value of every bill made in the period.
* Total Sales Returns  : value of goods customers brought back in the period.
* Buying price         : what the shop paid for the products it actually sold (the buying price saved on each
                         sale line when it was made), minus the buying price of the goods that came back.
Purchase returns do not change profit: they only change how much money the shop owes its suppliers.
"""
from datetime import date

from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from .models import Sale, SaleItem, SaleReturn, ZERO

_DEC = DecimalField(max_digits=18, decimal_places=3)


def profit_between(start, end):
    sales = Sale.objects.filter(date__gte=start, date__lte=end).aggregate(t=Sum("total_amount"))["t"] or ZERO
    returns = SaleReturn.objects.filter(date__gte=start, date__lte=end).aggregate(t=Sum("amount"))["t"] or ZERO
    cost_sold = SaleItem.objects.filter(sale__date__gte=start, sale__date__lte=end).aggregate(
        t=Sum(ExpressionWrapper(F("quantity") * F("cost_price"), output_field=_DEC)))["t"] or ZERO
    cost_back = SaleReturn.objects.filter(date__gte=start, date__lte=end).aggregate(
        t=Sum(ExpressionWrapper(F("quantity") * F("sale_item__cost_price"), output_field=_DEC)))["t"] or ZERO
    buying_cost = cost_sold - cost_back
    return {
        "from": start, "to": end,
        "total_sales": sales,
        "sales_returns": returns,
        "buying_cost": buying_cost,
        "total_profit": sales - returns - buying_cost,
    }


def profit_for_year(year):
    out = profit_between(date(year, 1, 1), date(year, 12, 31))
    out["year"] = year
    return out
