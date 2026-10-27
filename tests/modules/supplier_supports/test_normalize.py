"""Cleaning of the workbook's messy cells; every guess must leave an issue behind."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.supplier_supports.application import normalize as n

D = Decimal


def codes(issues) -> list[str]:
    return [issue.code for issue in issues]


@pytest.mark.parametrize(
    ("raw", "expected", "issue"),
    [
        (datetime(2026, 12, 31), date(2026, 12, 31), []),
        (date(2026, 1, 5), date(2026, 1, 5), []),
        ("30/10/2026", date(2026, 10, 30), []),
        ("30/10(2026", date(2026, 10, 30), ["fecha_texto"]),
        ("19/05&2026", date(2026, 5, 19), ["fecha_texto"]),
        ("31/09/2026", date(2026, 9, 30), ["fecha_invalida"]),  # clamped to the month end
        ("31/02/2026", date(2026, 2, 28), ["fecha_invalida"]),
        ("3106/2026", None, ["fecha_invalida"]),
        ("02/19/2024", None, ["fecha_invalida"]),  # US order: month 19 does not exist
        (datetime(1900, 1, 30), None, ["fecha_invalida"]),  # empty formula placeholder
        (datetime(1900, 3, 31), None, ["fecha_invalida"]),
        ("INDEFINIDO", None, []),
        ("INDEFINIDO ", None, []),
        ("No definida", None, []),
        ("UFN", None, []),
        ("", None, []),
        (None, None, []),
    ],
)
def test_parse_date(raw, expected, issue) -> None:
    value, found = n.parse_date(raw, "valid_to")

    assert value == expected
    assert codes(found) == issue


def test_a_repaired_date_keeps_the_original_text_in_the_issue() -> None:
    _, found = n.parse_date("31/09/2026", "valid_to")

    assert found[0].detail == "31/09/2026 (ajustada)" and found[0].field == "valid_to"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$ 131.974", D("131974")),  # Kimberly: Colombian thousands separator
        ("$ 1.234.567,50", D("1234567.50")),
        ("12,5", D("12.5")),
        (131974, D("131974")),
        (0.17999999999999994, D("0.18")),  # Excel float noise
        (None, None),
        ("#REF!", None),
    ],
)
def test_parse_number(raw, expected) -> None:
    value, found = n.parse_number(raw, "supplier_value")

    assert value == expected
    assert found == []


def test_unreadable_text_is_reported_not_guessed() -> None:
    value, found = n.parse_number("sin dato", "supplier_value")

    assert value is None and codes(found) == ["valor_texto"]


@pytest.mark.parametrize(
    ("raw", "expected", "issue"),
    [
        (0.045, D("0.045"), []),
        ("4,5%", D("0.045"), []),
        ("13% (Recompuesto)", D("0.13"), ["valor_texto"]),
        ("5,98% (Precio 90.336)", D("0.0598"), ["valor_texto"]),
        (201265, None, ["valor_texto"]),  # a price typed in a margin column
        (-0.0984, D("-0.0984"), []),  # negative margins are legitimate
        (None, None, []),
    ],
)
def test_parse_fraction(raw, expected, issue) -> None:
    value, found = n.parse_fraction(raw, "min_margin_pct")

    assert value == expected
    assert codes(found) == issue


@pytest.mark.parametrize(
    ("raw", "kwargs", "expected"),
    [
        (800116299, {}, "800116299"),
        ("901505610\xa0", {}, "901505610"),
        ("800.116.299", {}, "800116299"),
        ("8001162991", {"check_digit": True}, "800116299"),
        ("TODOS", {}, None),
        ("#REF!", {}, None),
        (None, {}, None),
    ],
)
def test_parse_nit(raw, kwargs, expected) -> None:
    assert n.parse_nit(raw, **kwargs) == expected


def test_free_text_extractors() -> None:
    assert n.nits_in_text("800116299 - SUMMAR PROCESOS\n35518947 GUTIERREZ") == [
        "800116299",
        "35518947",
    ]
    assert n.nits_in_text("TODOS") == []
    assert n.skus_in_text("8749Arroz Diana x 1Kg\n22226 Arroz Castellano\nSAL") == ["8749", "22226"]
    assert n.percent_in_text("TODO EL PORTAFOLIO (Unilever devuelve 6,38% de lista 1)") == D(
        "0.0638"
    )
    assert n.percent_in_text("sin porcentaje") is None
    assert n.authorizer_in_text("AUTORIZA ISABEL QUIROGA") == "ISABEL QUIROGA"
    assert n.authorizer_in_text("Gloria Autoriza Ingresar") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ANTIQOUIA", ("ANTIOQUIA",)),  # typo in the workbook
        ("Antioquia", ("ANTIOQUIA",)),
        ("ANTIOQUIA -CENTRO- COSTA - OCCIDENTE", ("ANTIOQUIA", "CENTRO", "COSTA", "OCCIDENTE")),
        ("ANTIOQUIA/CENTRO", ("ANTIOQUIA", "CENTRO")),
        ("TODOS", ()),
        ("Todas", ()),
        ("NACIONAL", ()),
        ("BARRANQUILLA", ("BARRANQUILLA",)),  # kept as written until the business decides
        (None, ()),
    ],
)
def test_parse_regions(raw, expected) -> None:
    assert n.parse_regions(raw) == expected


@pytest.mark.parametrize(
    ("raw", "units", "channels"),
    [
        ("FOOD", ("FOOD SERVICE",), ()),
        ("FOOD SERVICE", ("FOOD SERVICE",), ()),
        ("MAYORISTAS ", (), ("MAYORISTAS Y DISTRIBUIDORES",)),
        ("MAYORISTAS Y DISTRIBUIDORES", (), ("MAYORISTAS Y DISTRIBUIDORES",)),
        ("RETAIL - MASIVO", ("RETAIL", "MASIVO"), ()),
        ("MASIVO", ("MASIVO",), ()),
        ("TODOS", (), ()),
        ("E-COMMERCE", (), ("E-COMMERCE",)),
    ],
)
def test_parse_units_and_channels(raw, units, channels) -> None:
    assert n.parse_units_and_channels(raw) == (units, channels)


def test_parse_channels_of_a_canal_column() -> None:
    assert n.parse_channels("CAJAS COMPESANCION") == ("CAJAS DE COMPENSACION",)
    assert n.parse_channels("HOTELES") == ("HOTELES Y CLUBES",)


def test_sku_cleaning() -> None:
    assert n.clean_sku(8749.0) == "8749"
    assert n.clean_sku("22226\xa0") == "22226"
    assert n.clean_sku("NO ESTA") is None
    assert n.clean_sku("#REF!") is None
    assert n.clean_sku("P000083") == "P000083"


def test_sequential_nits_under_one_name_are_flagged_from_the_second_row() -> None:
    rows = [
        ("111", "OTRO"),
        ("860504410", "NUTRIX"),
        ("860504411", "NUTRIX"),
        ("860504412", "NUTRIX"),
        ("860504413", "NUTRIX"),
        ("222", "OTRO"),
        ("223", "OTRO"),  # a pair is a coincidence, not a drag-fill
        ("500", "CUALQUIERA"),
    ]

    assert n.flag_sequential_nits(rows) == {2, 3, 4}


def test_fold_matches_headers_regardless_of_accents_and_spacing() -> None:
    assert n.fold("RAZÓN\nSOCIAL ") == n.fold("razon social")
    assert n.fold("% DCTO TEAM (En Costo)") == "% dcto team en costo"
    assert n.fold("\xa0Costo Unitario") == "costo unitario"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5pp", D("0.05")),
        ("5 PP", D("0.05")),
        (" 2,5pp ", D("0.025")),
        ("12pp", D("0.12")),
        ("5%", None),
        ("pp", None),
        ("5ppm", None),
        ("KCP WIPERS 24X1X64 PP", None),
        (5, None),
        (None, None),
    ],
)
def test_parse_pp_is_a_percentage_over_cost(raw, expected) -> None:
    assert n.parse_pp(raw) == expected
