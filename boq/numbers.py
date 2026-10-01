"""
Showing BOQ quantities and rates without padding.

They are stored with spare decimals (6 for quantity, 4 for rate) so real
contract figures like 2.1504 ha or a rate of 26.325 are kept exactly.
On screen, trailing zeros are dropped down to a minimum: 3 decimals for
quantities (Section 7.2) and 2 for rates, so 4300.800000 shows as
4300.800, 1.031250 as 1.03125, and a rate of 185.0000 as 185.00.
"""

from decimal import Decimal


def trim(value, min_places):
    if value is None or value == "":
        return ""
    value = Decimal(str(value))
    places = max(min_places, -value.normalize().as_tuple().exponent)
    return f"{value:.{places}f}"


def quantity(value):
    return trim(value, 3)


def rate(value):
    return trim(value, 2)
