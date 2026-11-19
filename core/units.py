from decimal import Decimal
from core.models import Unit


def convert(qty, from_unit, to_unit):
    """
    Convert quantity from one unit to another using Decimal arithmetic.
    Raises ValueError if unit kinds differ.
    Accepts Unit model instances or unit code strings (e.g. 'kg', 'g').
    """
    if isinstance(from_unit, str):
        from_unit = Unit.objects.get(code=from_unit)
    if isinstance(to_unit, str):
        to_unit = Unit.objects.get(code=to_unit)

    if from_unit.kind != to_unit.kind:
        raise ValueError(
            f"Cannot convert between different unit kinds: '{from_unit.kind}' ({from_unit.code}) "
            f"and '{to_unit.kind}' ({to_unit.code})"
        )

    if not isinstance(qty, Decimal):
        qty = Decimal(str(qty))

    factor_from = Decimal(str(from_unit.factor_to_base))
    factor_to = Decimal(str(to_unit.factor_to_base))

    result = (qty * factor_from) / factor_to

    if result == result.to_integral():
        return result.to_integral()
    else:
        res_str = f"{result:f}".rstrip('0').rstrip('.')
        return Decimal(res_str)
