"""The workbook adapters, on small synthetic workbooks that mimic the real layouts."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.modules.supplier_supports.infrastructure import workbook as wb
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table

D = Decimal


def add_table(
    book: Workbook,
    sheet: str,
    name: str,
    header: list[str],
    rows: list[list],
    first_column: int = 1,
) -> None:
    """Add an Excel table; several tables of one sheet sit side by side, like the real ones."""

    worksheet = book[sheet] if sheet in book.sheetnames else book.create_sheet(sheet)
    first_row = 2  # the real sheets keep a merged title above the table
    for column, title in enumerate(header, first_column):
        worksheet.cell(first_row, column, title)
    for offset, row in enumerate(rows, 1):
        for column, value in enumerate(row, first_column):
            worksheet.cell(first_row + offset, column, value)
    first, last = get_column_letter(first_column), get_column_letter(first_column + len(header) - 1)
    worksheet.add_table(
        Table(displayName=name, ref=f"{first}{first_row}:{last}{first_row + len(rows)}")
    )


def familia_book(tmp_path: Path, rows: list[list]) -> wb.Workbook:
    """Familia has a second table (``Tabla40``); an empty one keeps the reader satisfied."""

    def build(book: Workbook) -> None:
        add_table(book, "Familia", "Apoyos_Familia", FAMILIA_HEADER, rows)
        wholesale = ["FECHA INICIAL", "FECHA FINAL", "CANAL", "REF JDH", "REF FAMILIA"]
        sheet = book["Familia"]
        for column, title in enumerate(wholesale, 54):
            sheet.cell(2, column, title)
        sheet.add_table(Table(displayName="Tabla40", ref="BB2:BF3"))

    return open_book(tmp_path, build)


def open_book(tmp_path: Path, build) -> wb.Workbook:
    book = Workbook()
    book.remove(book.active)
    build(book)
    path = tmp_path / "apoyos.xlsx"
    book.save(path)
    return wb.Workbook(path)


FAMILIA_HEADER = [
    "FECHA INICIAL", "FECHA FINAL", "NIT", "RAZON SOCIAL", "GRUPO EMPRESARIAL", "REF JDH",
    "REF FAMILIA", "PVP X CAJA SIN IVA", "COSTO CAJA", "COSTO APOYADO CAJA", "FACTOR U.E",
    "MARGEN OBJETIVO", "UN", "OBSERVACION",
]  # fmt: skip


def familia_row(nit, name, start=datetime(2026, 7, 1), end=datetime(2027, 1, 30), **extra):
    row = [
        start, end, nit, name, None, 1128, 80550, 163965.97, 122974.48, 115678.013, 8, 0.17,
        "FOOD", extra.get("note"),
    ]  # fmt: skip
    return row


def test_familia_rows_become_client_x_sku_cost_supports(tmp_path) -> None:
    book = familia_book(
        tmp_path,
        [familia_row(800116299, "LIMPIEZA Y MANTENIMIENTO", note="AUTORIZA ISABEL QUIROGA")],
    )

    (item,) = [
        item for item in wb.read_supports(book, ["Familia"]) if item.table == "Apoyos_Familia"
    ]

    data = item.data
    assert item.supplier == "233"
    assert (data.kind, data.calc_method) == ("tactico_cliente", "costo_neto")
    assert data.customer_nit == "800116299" and data.product_sku == "1128"
    assert data.supplier_sku == "80550" and data.business_units == ("FOOD SERVICE",)
    assert data.supplier_value == D("115678.013") and data.reference_cost == D("122974.48")
    assert data.target_margin_pct == D("0.17") and data.pack_factor == D("8")
    assert data.valid_from == date(2026, 7, 1) and data.valid_to == date(2027, 1, 30)
    assert data.authorized_by == "ISABEL QUIROGA"
    assert data.source == "Familia/Apoyos_Familia" and data.source_key == "Familia/Apoyos_Familia:3"
    assert data.issues == ()


def test_formula_only_rows_are_skipped_and_row_numbers_follow_the_sheet(tmp_path) -> None:
    empty = [None] * len(FAMILIA_HEADER)
    book = familia_book(tmp_path, [familia_row(111, "A"), empty, familia_row(222, "B")])

    keys = [
        item.data.source_key
        for item in wb.read_supports(book, ["Familia"])
        if item.table == "Apoyos_Familia"
    ]

    assert keys == ["Familia/Apoyos_Familia:3", "Familia/Apoyos_Familia:5"]


def test_drag_filled_nits_and_exact_duplicates_are_flagged(tmp_path) -> None:
    rows = [familia_row(860504410 + step, "NUTRIX") for step in range(4)]
    rows += [familia_row(111, "A"), familia_row(111, "A")]
    book = familia_book(tmp_path, rows)

    items = [item for item in wb.read_supports(book, ["Familia"]) if item.table == "Apoyos_Familia"]

    flags = [[issue.code for issue in item.data.issues] for item in items]
    assert flags == [
        [],
        ["nit_secuencial_sospechoso"],
        ["nit_secuencial_sospechoso"],
        ["nit_secuencial_sospechoso"],
        [],
        ["duplicado"],
    ]


def test_bad_dates_are_repaired_with_an_issue_and_open_ended_stays_open(tmp_path) -> None:
    rows = [
        familia_row(1, "A", end="31/09/2026"),
        familia_row(2, "B", end="INDEFINIDO"),
        familia_row(3, "C", start=datetime(1900, 1, 30)),
    ]
    book = familia_book(tmp_path, rows)

    first, second, third = [
        item.data for item in wb.read_supports(book, ["Familia"]) if item.table == "Apoyos_Familia"
    ]

    assert first.valid_to == date(2026, 9, 30)
    assert [issue.code for issue in first.issues] == ["fecha_invalida"]
    assert second.valid_to is None and second.issues == ()
    assert third.valid_from is None and [issue.code for issue in third.issues] == ["fecha_invalida"]


def test_kimberly_text_money_and_unknown_sku(tmp_path) -> None:
    header = [
        "N° ACUERDO DE PRECIOS", "NOMBRE DEL ACUERDO DE PRECIOS", "TIPO DE ACUERDO DE PRECIOS",
        "NIT SIN DIGITO VERIFICACION", "NOMBRE DE USUARIO FINAL", "CÓDIGO PRODUCTO",
        "COSTO APOYADO", "FECHA INICIO ACUERDO", "FECHA FIN ACUERDO", "VOLUMEN COMPROMETIDO",
        "U.E", "REF JDH", "COSTO CAJA KCP",
    ]  # fmt: skip
    rows = [
        [70038195, "Precios Agiles Juan", "Grupo", "1000354291", "KEVIN LOPEZ  CALLE 1  CO",
         30242860, "$ 131.974", datetime(2026, 6, 1), "31/12/2026", "1.000", 6, "NO ESTA",
         186705],
    ]  # fmt: skip
    book = open_book(tmp_path, lambda b: add_table(b, "Kimberly", "Apoyos_Kimberly", header, rows))

    (item,) = wb.read_supports(book, ["Kimberly"])

    data = item.data
    assert item.supplier == "1098"
    assert data.supplier_value == D("131974") and data.settlement_method == "costo_neto_facturado"
    assert data.external_ref == "70038195" and data.customer_group == "Precios Agiles Juan"
    assert data.customer_name == "KEVIN LOPEZ"
    assert data.valid_to == date(2026, 12, 31) and data.committed_volume == D("1000")
    assert data.supplier_sku == "30242860" and data.product_sku is None
    assert [issue.code for issue in data.issues] == ["sku_sin_cruce"]


def test_template_sheets_choose_the_calculation_from_the_applied_base(tmp_path) -> None:
    header = [
        "FECHA INICIAL", "FECHA FINAL", "REGIONAL", "UNIDAD NEGOCIO",
        "NIT CLIENTE (Sin Digito Verificacion)", "RAZÓN SOCIAL CLIENTE", "SKU PROVEEDOR", "SKU JDH",
        "COSTO JDH", "PRECIO LISTA AL PUBLICO SIN IVA", "% DESCUENTO PROVEEDOR",
        "DESCUENTO APLICA EN", "$ DESCUENTO POR UNIDAD",
    ]  # fmt: skip
    rows = [
        [datetime(2026, 5, 1), "31/09/2026", "NACIONAL", "FOOD SERVICE", "TODOS", "TODOS", 1,
         "22226\xa0", 4000, 5000, 0.065, "PRECIO LISTA", None],
        [datetime(2026, 5, 1), None, "CENTRO", None, 900993023, "COCOROLLO", 2, 16794, 16852,
         20551.2, None, "COSTO", 706],
    ]  # fmt: skip
    book = open_book(tmp_path, lambda b: add_table(b, "Dicorp", "Tabla211", header, rows))

    first, second = [item.data for item in wb.read_supports(book, ["Dicorp"])]

    assert (first.calc_method, first.supplier_value) == ("pct_precio_lista", D("0.065"))
    assert first.customer_nit is None and first.regionals == ()  # TODOS / NACIONAL
    assert first.product_sku == "22226" and first.settlement_method == "nota_credito"
    assert first.valid_to == date(2026, 9, 30)
    assert (second.calc_method, second.supplier_value) == ("valor_unidad", D("706"))
    assert second.customer_nit == "900993023" and second.regionals == ("CENTRO",)


def test_ohra_rows_carry_the_ambiguous_base_issue(tmp_path) -> None:
    header = [
        "REGIONAL", "VENDEDOR", "FECHA INICIO OFERTA", "FECHA VALIDEZ OFERTA",
        "NIT (Sin Digito Verificación)", "RAZON SOCIAL", "REF. JDH", "U.E", "COSTO CAJA",
        "PVP CAJA SIN IVA", "% APOYO DODEKA", "% DCTO JDH",
    ]  # fmt: skip
    rows = [
        [
            "ANTIOQUIA",
            "ANA",
            datetime(2026, 1, 1),
            None,
            900111222,
            "CLIENTE",
            5000,
            6,
            100,
            120,
            0.05,
            0.02,
        ]
    ]
    book = open_book(tmp_path, lambda b: add_table(b, "Dodeka (Ohra)", "Apoyos_Ohra", header, rows))

    (item,) = wb.read_supports(book, ["Dodeka (Ohra)"])

    assert item.supplier == "1109" and item.data.sales_rep == "ANA"
    assert [issue.code for issue in item.data.issues] == ["base_ambigua"]


def test_special_conditions_expand_by_supplier_sku_and_client(tmp_path) -> None:
    header = [
        "C.O", "U.N/CANAL", "PROVEEDOR", "REFERENCIAS", "CLIENTES", "MARGEN MINIMO", "AUTORIZA",
        "OBSERVACIONES", "VIGENCIA INICIAL", "VIGENCIA FINAL",
    ]  # fmt: skip
    rows = [
        ["ANTIQOUIA", "FOOD SERVICE", "FAMILIA", "8749Arroz Diana x 1Kg\n22226 Arroz Castellano",
         "800116299 - SUMMAR", 0.13, "ROBERTO MONTOYA", "APOYADO CON REBATE 8%",
         datetime(2025, 1, 1), "HASTA NIVELAR COSTO"],
        ["TODOS", "MAYORISTAS", "CORONA", "TODAS", "TODOS", "13% (Recompuesto)", "JOHAN MAZO",
         None, None, "INDEFINIDO"],
        ["COSTA", None, "ESTRA", "SAL", "MAYORISTAS", 201265, None, None, None, None],
    ]  # fmt: skip
    book = open_book(
        tmp_path,
        lambda b: add_table(b, "Condiciones Especiales", "Condiciones_Especiales", header, rows),
    )

    items = list(wb.read_supports(book, ["Condiciones Especiales"]))

    first_two, third, fourth = items[:2], items[2], items[3]
    assert [item.data.product_sku for item in first_two] == ["8749", "22226"]
    assert {item.supplier for item in first_two} == {"name:FAMILIA"}
    assert first_two[0].data.regionals == ("ANTIOQUIA",)
    assert first_two[0].data.customer_nit == "800116299"
    assert first_two[0].data.end_condition == "HASTA NIVELAR COSTO"
    assert first_two[0].data.authorized_by == "ROBERTO MONTOYA"
    assert first_two[0].data.min_margin_pct == D("0.13")
    assert [item.data.source_key for item in first_two] == [
        "Condiciones Especiales:3#1",
        "Condiciones Especiales:3#2",
    ]
    # TODAS / TODOS mean no restriction; the percentage inside text is read and flagged.
    assert third.data.product_sku is None and third.data.customer_nit is None
    assert third.data.channels == ("MAYORISTAS Y DISTRIBUIDORES",)
    assert third.data.min_margin_pct == D("0.13")
    assert [issue.code for issue in third.data.issues] == ["valor_texto"]
    # A price typed as margin is kept as a note, not as a margin.
    assert fourth.data.min_margin_pct is None
    assert "201265" in fourth.data.notes
    assert "Referencias: SAL" in fourth.data.conditions
    assert "Clientes: MAYORISTAS" in fourth.data.conditions


def test_estra_block_without_a_table_skips_the_totals_row(tmp_path) -> None:
    def build(book: Workbook) -> None:
        sheet = book.create_sheet("Estra")
        sheet.append(
            [
                "Referencia",
                "PROVEEDOR",
                "Desc. item",
                "Bodega",
                "\xa0Costo Unitario",
                "Dscto Estra",
                "Cantidad Actual Bodega",
                "Precio Especial Sin IVA",
                "Nc Estra",
                "Observaciones",
                "Liq",
            ]
        )
        sheet.append(
            [
                18024,
                "295 - ESTRA",
                "Caneca",
                "MDE1\xa0",
                708580,
                0.1,
                9,
                None,
                63772,
                "Se paga lo que se rota",
                "Sin venta en Junio",
            ]
        )
        sheet.append([None, None, None, None, None, None, None, None, 2081150.8])

    book = open_book(tmp_path, build)

    (item,) = wb.read_supports(book, ["Estra"])

    data = item.data
    assert (item.supplier, data.kind, data.calc_method) == (
        "295",
        "liquidacion_inventario",
        "pct_costo",
    )
    assert data.warehouse == "MDE1" and data.stock_quantity == D("9")
    assert data.supplier_value == D("0.1") and data.reference_cost == D("708580")


def test_list_margins_and_supplier_codes(tmp_path) -> None:
    def build(book: Workbook) -> None:
        add_table(
            book, "Margenes Lista1", "Margenes_Lista1",
            ["Cod. Proveedor", "Proveedor", "Margen Lista 1", "Observacion"],
            [[233, "FAMILIA SANCELA", 0.18, None], [999, "SIN MARGEN", None, "x"]],
        )  # fmt: skip
        add_table(
            book, "Familia", "Maestro_Sku_Familia",
            ["Ref. JDH", "Ref. Familia", "Desc. item", "Factor U.E"],
            [[1128, 80550, "Ph Jumbo", 8], [None, 1, "x", 1]],
        )  # fmt: skip

    book = open_book(tmp_path, build)

    margins = wb.read_list_margins(book)
    assert [(m.code, m.pct) for m in margins] == [("233", D("0.18"))]

    codes = [c for supplier, c in wb.read_supplier_codes(book, ["Familia"])]
    assert (codes[0].supplier_sku, codes[0].product_sku, codes[0].pack_factor) == (
        "80550",
        "1128",
        D("8"),
    )


def test_missing_sheets_fail_loudly(tmp_path) -> None:
    book = open_book(tmp_path, lambda b: b.create_sheet("Otra"))

    with pytest.raises(KeyError, match="Falta la hoja"):
        book.table("Familia", "Apoyos_Familia")


DIVERSEY_HEADER = [
    "Fecha Inicial",
    "Fecha Final",
    "NIT",
    "RAZÓN SOCIAL",
    "NOMBRE COMERCIAL",
    "REGIONAL",
    "SKU JDH",
    "DESCRIPCION REFERENCIA",
    "COSTO UND",
    "$ PRECIO MARGEN 18%",
    "$ PRECIO APOYADO SIN IVA",
    "$ APOYO DIVERSEY",
    "% APOYO DIVERSEY",
    "MARGEN REAL",
    "OBSERVACION",
]


def diversey_book(tmp_path, rows):
    def build(book):
        add_table(book, "Diversey", "Apoyos_Diversey", DIVERSEY_HEADER, rows)

    return open_book(tmp_path, build)


def test_diversey_uses_the_apoyo_diversey_percentage_over_cost(tmp_path) -> None:
    row = [
        datetime(2024, 1, 1),
        "INDEFINIDO",
        98557490,
        "Arango",
        None,
        "Antioquia",
        8293,
        "Limpiavidrios x 1Lt",
        11201,
        13659.75,
        12020,
        1344.6,
        0.12004285,
        0.18,
        None,
    ]
    book = diversey_book(tmp_path, [row])

    (item,) = wb.read_supports(book, ["Diversey"])

    data = item.data
    assert (data.calc_method, data.value_unit, data.unit_basis_source) == (
        "pct_costo",
        "unidad",
        "hoja",
    )
    assert data.supplier_value == D("0.120043") and data.reference_cost == D("11201")
    assert data.regionals == ("ANTIOQUIA",) and data.product_sku == "8293"
    assert data.valid_to is None


@pytest.mark.parametrize("cell", ["% APOYO DIVERSEY", "$ APOYO DIVERSEY", "SKU JDH"])
def test_npp_in_diversey_is_a_discount_over_the_whole_portfolio(tmp_path, cell) -> None:
    row = [datetime(2026, 1, 1), None, 98557490, "Arango", None, "Antioquia", 8293, "x", 11201,
           None, None, 1344.6, 0.12, 0.18, None]  # fmt: skip
    row[DIVERSEY_HEADER.index(cell)] = "5pp"
    book = diversey_book(tmp_path, [row])

    (item,) = wb.read_supports(book, ["Diversey"])

    data = item.data
    assert (data.calc_method, data.supplier_value) == ("pct_costo", D("0.05"))
    assert data.product_sku is None and data.supplier_sku is None  # no product: all the portfolio
    assert data.reference_cost is None and data.value_unit is None
    assert data.program == "DESCUENTO PORTAFOLIO DIVERSEY" and "5pp" in data.conditions
    assert [issue.field for issue in data.issues if issue.field == "product_sku"] == []
    assert data.customer_nit == "98557490"


RUBBERMAID_LIST_HEADER = [
    "FECHA INICIAL", "FECHA FINAL", "SKU JDH", "SKU RUBBERMAID", "DESC. PRODUCTO", "COSTO REGULAR",
    "COSTO APOYADO", "PVP SIN IVA", "PRECIO CON DSCTO SIN IVA", "$ APOYO EN COSTO RCP",
    "%MARGEN GARANTIZADO",
]  # fmt: skip


def rubbermaid_book(tmp_path):
    row = [datetime(2025, 4, 1), datetime(2026, 3, 30), 10603, "RCP-1", "Contenedor", 241357.5,
           199455.0, 321810.0, 284934.98, 41902.5, 0.3]  # fmt: skip

    def build(book):
        add_table(book, "Rubbermaid", "Lista_Summar_RCP", RUBBERMAID_LIST_HEADER, [row], 20)
        add_table(book, "Rubbermaid", "Lista_SodexoCedi_RCP", RUBBERMAID_LIST_HEADER, [row], 40)
        add_table(
            book, "Rubbermaid", "Apoyos_Rubbermaid",
            ["NIT", "RAZON SOCIAL", "GRUPOS EMPRESARIALES", "CANAL", "MARGEN LISTA",
             "% APOYO PROVEEDOR", "% APOYO JDH", "OBSERVACION"],
            [[900111222, "HOTEL X", None, "HOTELES", 0.25, 0.1, 0, None]],
        )  # fmt: skip

    return open_book(tmp_path, build)


def test_rubbermaid_lists_hold_the_new_supported_cost_for_one_client(tmp_path) -> None:
    summar, sodexo = [
        item
        for item in wb.read_supports(rubbermaid_book(tmp_path), ["Rubbermaid"])
        if item.table.startswith("Lista_")
    ]

    for item in (summar, sodexo):
        data = item.data
        assert (data.kind, data.calc_method) == ("lista_especial", "costo_neto")
        assert data.supplier_value == D("199455") and data.reference_cost == D("241357.5")
        assert data.reference_list_price == D("321810") and data.brand == "RUBBERMAID"
        assert data.value_unit is None  # the sheet does not say: inferred later
    assert summar.data.customer_nit == "800125313" and summar.data.issues == ()
    assert sodexo.data.customer_nit is None and sodexo.data.customer_name == "SODEXO CEDI"
    assert [issue.code for issue in sodexo.data.issues] == ["cliente_por_definir"]


def test_sheets_that_do_not_declare_their_unit_leave_it_to_the_inference(tmp_path) -> None:
    header = ["FECHA INICIO", "FECHA FIN", "NIT", "RAZON SOCIAL", "REF JDH", "SKU UNILEVER",
              "% DCTO APOYADO", "COSTO", "PRECIO LISTA SIN IVA"]  # fmt: skip
    row = [datetime(2026, 1, 1), datetime(2026, 12, 31), 5744769, "RODRIGUEZ", 3474, 67464006,
           0.1, 13783, 17000]  # fmt: skip
    book = open_book(tmp_path, lambda b: add_table(b, "Unilever", "Apoyos_Unilever", header, [row]))

    item = next(wb.read_supports(book, ["Unilever"]))  # lazy: later sources are not read

    assert item.data.value_unit is None and item.data.unit_basis_source == "desconocido"
