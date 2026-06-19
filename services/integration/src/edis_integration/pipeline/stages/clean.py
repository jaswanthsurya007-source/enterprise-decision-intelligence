"""Stage 4 -- clean: normalize canonical entities (trim/case, currency, ISO).

Operates on the mapped :class:`MapperResult` and returns a normalized copy:

* **Currency -> USD base.** ``currency_src`` is upper-cased to an ISO-4217 code;
  ``amount_base = amount_src * fx_rate`` where ``fx_rate`` is the source->USD
  rate (``1.0`` for USD, per the MVP base). Line ``*_base`` amounts are scaled by
  the same rate so the order and its lines stay consistent.
* **Region** is upper-cased and trimmed (``"  emea " -> "EMEA"``); blanks become
  ``None``.
* **Country** is normalized to an ISO-3166-1 alpha-2 code (upper-cased, 2 chars).
* String fields are trimmed.

Pure: it builds new pydantic models via ``model_copy(update=...)`` rather than
mutating in place. The FX table is a static MVP stub (the demo is USD-only);
unknown currencies default to a ``1.0`` rate and are flagged by the DQ stage.
"""

from __future__ import annotations

from decimal import Decimal

from edis_contracts.canonical import CanonicalCustomer, CanonicalOrder, CanonicalOrderLine

from edis_integration.pipeline.stages import StageContext

#: Static source-currency -> USD rates (MVP stub; demo is USD-only). A live FX
#: provider drops in behind this map without changing the clean contract.
_FX_TO_USD: dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("1.08"),
    "GBP": Decimal("1.27"),
    "JPY": Decimal("0.0064"),
    "INR": Decimal("0.012"),
}

_REGIONS = {"NA", "EMEA", "APAC", "LATAM"}


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


def _norm_region(region: str | None) -> str | None:
    cleaned = _clean_str(region)
    if cleaned is None:
        return None
    upper = cleaned.upper()
    return upper  # kept even if not in _REGIONS; DQ stage scores unknown regions


def _norm_country(country: str | None) -> str | None:
    cleaned = _clean_str(country)
    if cleaned is None:
        return None
    code = cleaned.upper()
    return code[:2] if len(code) >= 2 else None


def _fx_rate(currency: str) -> Decimal:
    return _FX_TO_USD.get(currency.upper(), Decimal("1.0"))


def _clean_order(order: CanonicalOrder) -> CanonicalOrder:
    currency_src = order.currency_src.strip().upper()
    rate = _fx_rate(currency_src)
    amount_base = (order.amount_src * rate).quantize(Decimal("0.0001"))

    lines: list[CanonicalOrderLine] = [
        line.model_copy(
            update={
                "sku": line.sku.strip(),
                "unit_price_base": (line.unit_price_base * rate).quantize(Decimal("0.0001")),
                "line_amount_base": (line.line_amount_base * rate).quantize(Decimal("0.0001")),
            }
        )
        for line in order.line_items
    ]

    return order.model_copy(
        update={
            "currency_src": currency_src,
            "currency_base": "USD",
            "fx_rate": rate,
            "amount_base": amount_base,
            "region": _norm_region(order.region),
            "line_items": lines,
        }
    )


def _clean_customer(customer: CanonicalCustomer) -> CanonicalCustomer:
    return customer.model_copy(
        update={
            "legal_name": (_clean_str(customer.legal_name) or customer.legal_name),
            "display_name": (_clean_str(customer.display_name) or customer.display_name),
            "region": _norm_region(customer.region),
            "country_iso2": _norm_country(customer.country_iso2),
            "industry": _clean_str(customer.industry),
        }
    )


class CleanStage:
    name = "clean"

    def __call__(self, ctx: StageContext) -> StageContext:
        from edis_integration.mappers.registry import MapperResult

        assert ctx.mapped is not None  # map ran first
        mapped = ctx.mapped

        order = _clean_order(mapped.order) if mapped.order is not None else None
        customer = _clean_customer(mapped.customer) if mapped.customer is not None else None
        # Ops events trim service/region.
        ops_events = [
            ev.model_copy(
                update={
                    "service": ev.service.strip(),
                    "region": _norm_region(ev.region),
                }
            )
            for ev in mapped.ops_events
        ]

        ctx.cleaned = MapperResult(order=order, customer=customer, ops_events=ops_events)
        return ctx


clean = CleanStage()
