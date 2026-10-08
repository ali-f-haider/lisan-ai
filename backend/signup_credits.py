"""Bounded signup allowance shared by pricing validation and offline tests."""
from decimal import Decimal, InvalidOperation

MIN_SIGNUP_CREDITS = 1
MAX_SIGNUP_CREDITS = 1000
DEFAULT_SIGNUP_CREDITS = 100


def configured_amount(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        amount = Decimal(str(value))
        if not amount.is_finite() or not MIN_SIGNUP_CREDITS <= amount <= MAX_SIGNUP_CREDITS or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (ValueError, TypeError, InvalidOperation):
        return DEFAULT_SIGNUP_CREDITS


def validate_amount(value):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        amount = Decimal(str(value))
        if not amount.is_finite() or not MIN_SIGNUP_CREDITS <= amount <= MAX_SIGNUP_CREDITS or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError(f'Signup credits must be a whole number from {MIN_SIGNUP_CREDITS} to {MAX_SIGNUP_CREDITS}.') from None
