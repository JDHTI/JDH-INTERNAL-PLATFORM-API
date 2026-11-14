"""Cleaning the contact cells and reading the monthly prospects workbook."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.errors import InvalidInputError
from app.modules.territory.domain.contacts import (
    clean_website,
    coordinates_from_link,
    parse_emails,
    parse_phones,
)
from app.modules.territory.infrastructure.prospect_file import SpreadsheetProspectReader
from tests.modules.territory.conftest import HEADER, row, workbook

reader = SpreadsheetProspectReader()


def test_phones_keep_their_note_and_area_code() -> None:
    phones = parse_phones("(604) 361 6061 (fijo · central)\n310 496 9204 (celular)")

    assert [(p.number, p.display, p.label, p.is_mobile) for p in phones] == [
        ("6043616061", "(604) 361 6061", "fijo · central", False),
        ("3104969204", "310 496 9204", "celular", True),
    ]


def test_phones_split_runs_drop_the_country_code_and_repeats() -> None:
    phones = parse_phones("+57 313 776 8650 - 300 123 4567; 313 776 8650")

    assert [p.number for p in phones] == ["3137768650", "3001234567"]
    assert parse_phones("sin teléfono") == [] and parse_phones(None) == []
    assert parse_phones("235 2699")[0].display == "235 2699"


def test_emails_drop_scraper_junk_links_and_repeats() -> None:
    raw = "u003einfo@Lupita.com\nventas@lupita.com ; info@lupita.com"

    assert parse_emails(raw) == ["info@lupita.com", "ventas@lupita.com"]
    assert parse_emails("https://www.google.com/maps/place/Bar/@6.12,-75.6") == []


def test_websites_and_map_links() -> None:
    assert clean_website("www.mikaela.com.co") == "https://www.mikaela.com.co"
    assert clean_website("https://x.co/a?b=1") == "https://x.co/a?b=1"
    assert clean_website("no tiene") is None
    assert coordinates_from_link("https://www.google.com/maps?q=6.281776,-75.560911") == (
        6.281776,
        -75.560911,
    )
    assert coordinates_from_link("https://maps.google.com/?q=6.28%2C-75.56") == (6.28, -75.56)
    assert coordinates_from_link("https://www.google.com/maps/place/X/@6.1,-75.5,17z") == (
        6.1,
        -75.5,
    )
    assert coordinates_from_link("Ver mapa") is None


def test_the_workbook_brings_every_zone_sheet_and_skips_the_summary_ones() -> None:
    content = workbook(
        {
            "Belén": [
                row(
                    "BEL-1",
                    "Deli",
                    **{
                        "Cliente antiguo": "Sí",
                        "Última compra": datetime(2015, 2, 1),
                        "Ubicación": "Dentro del área",
                        "Teléfono / WhatsApp": "313 776 8650 (WhatsApp · central)",
                        "Correo": "u003edeli@x.com",
                    },
                ),
                row("BEL-2", "Sin punto", latitude=None, **{"Ubicación": "Por ubicar"}),
            ],
            "Itagüí": [row(f"ITG-{i}", f"Local {i}", 6.17, -75.61) for i in range(3)],
        }
    )

    parsed = reader.read(content)

    assert [s.name for s in parsed.sheets] == ["Belén", "Itagüí"]
    assert parsed.rows_read == 5 and not parsed.errors
    first = parsed.rows[0]
    assert first.source_zone == "Belén" and first.is_former_customer
    assert first.last_purchase_month.isoformat() == "2015-02-01"
    assert first.area_status == "inside" and first.emails == ("deli@x.com",)
    assert first.phones[0].is_mobile and first.location.latitude == 6.25
    assert first.location_quality == "exact"
    assert parsed.rows[1].location is None and parsed.rows[1].location_quality == "missing"
    assert parsed.rows[1].area_status == "unlocated"
    # Three rows on one point were geocoded to a centroid.
    assert {r.location_quality for r in parsed.rows[2:]} == {"approximate"}
    belen = parsed.sheets[0]
    assert (belen.rows, belen.with_location, belen.without_location) == (2, 1, 1)


def test_no_data_markers_are_empty_and_real_notes_stay() -> None:
    content = workbook(
        {
            "Itagüí": [
                row("ITG-1", "Uno", **{"Observación": "no_determinado", "Correo": "-"}),
                row("ITG-2", "Dos", **{"Observación": "No determinado", "Sector": "N/A"}),
                row("ITG-3", "Tres", **{"Observación": "posible_cierre_no_confirmado"}),
            ]
        }
    )

    rows = reader.read(content).rows

    assert [r.observation for r in rows] == [None, None, "posible_cierre_no_confirmado"]
    assert rows[0].email_raw is None and rows[1].sector is None


def test_latitude_and_longitude_columns_win_over_the_link() -> None:
    header = [*HEADER, "Latitud", "Longitud"]
    content = workbook(
        {"Centro": [{**row("C-1", "Bar", 6.0, -75.0), "Latitud": "6,2512", "Longitud": -75.5}]},
        header,
    )

    location = reader.read(content).rows[0].location

    assert (location.latitude, location.longitude) == (6.2512, -75.5)


def test_row_problems_are_reported_with_sheet_and_row() -> None:
    content = workbook(
        {
            "Belén": [row("BEL-1", "Uno"), row("", "Sin id"), row("BEL-3", None)],
            "Centro": [row("bel-1", "Repetido"), row("C-2", "Lejos", 40.4, -3.7)],
        }
    )

    parsed = reader.read(content)

    messages = {(e.sheet, e.row_number, e.field) for e in parsed.errors}
    assert messages == {
        ("Belén", 3, "ID"),
        ("Belén", 4, "Establecimiento"),
        ("Centro", 3, "Mapa"),
        ("Centro", 2, "ID"),
    }
    assert parsed.rows_read == 5


def test_a_file_without_prospect_sheets_or_not_a_workbook_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="no tiene hojas de prospectos"):
        reader.read(workbook({}, HEADER))
    with pytest.raises(InvalidInputError, match="Formato no soportado"):
        reader.read(b"plain text")
