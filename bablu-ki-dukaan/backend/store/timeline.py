"""Replays the life of ONE bill (a sale or a purchase) so Recent 50 can show history as it really happened.

A bill is created with some money paid. Later, payments / refunds and returns change what is left to pay.
The bill row itself keeps changing (amount_received / amount_paid grows), so to show the *first* entry the way
it was on the day, we rebuild it:   money at creation = money now  -  later payments that added money
                                                                  +  later payments that took money back
Then we walk through the later events in time order and record the balance after each one.
"""
from decimal import Decimal

ZERO = Decimal("0")


def _balance(total, returned, paid):
    diff = (total - returned) - paid
    return max(diff, ZERO), max(-diff, ZERO)       # (still to pay, extra to give back)


def replay(total, current_paid, payments, returns, adds_money_direction):
    """
    total                : gross bill value (never changes)
    current_paid         : amount_received (sale) / amount_paid (purchase) right now
    payments             : objects with .id .direction .amount .created_at
    returns              : objects with .id .amount .created_at
    adds_money_direction : the payment direction that INCREASES current_paid ("in" for sales, "out" for purchases)

    -> (first, after)
       first = {"paid": money at creation, "pending": ..., "refund": ...}
       after = {event_id: {"pending": ..., "refund": ...}}   balance right after each payment / return
    """
    added = sum((p.amount for p in payments if p.direction == adds_money_direction), ZERO)
    taken = sum((p.amount for p in payments if p.direction != adds_money_direction), ZERO)
    paid = current_paid - added + taken
    pending, refund = _balance(total, ZERO, paid)
    first = {"paid": paid, "pending": pending, "refund": refund}

    events = [(p.created_at, 1, "payment", p) for p in payments] + [(r.created_at, 0, "return", r) for r in returns]
    events.sort(key=lambda e: (e[0], e[1]))
    returned, after = ZERO, {}
    for _ts, _order, kind, obj in events:
        if kind == "return":
            returned += obj.amount
        else:
            paid += obj.amount if obj.direction == adds_money_direction else -obj.amount
        pending, refund = _balance(total, returned, paid)
        after[obj.id] = {"pending": pending, "refund": refund}
    return first, after
