"""The rules of the quoter, written for the advisors (public «Reglas» page)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.order_validation.domain.catalog import HOLDS, INFORMS, WARNS, Rule, Section
from app.modules.quotations.domain.entities import QuotationPolicy


def _points(value: Decimal) -> str:
    return f"{value * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def catalog(policy: QuotationPolicy) -> tuple[Section, ...]:
    """Every rule the quoter applies, with the values in force."""

    points = _points(policy.max_points_below_target)
    return (
        Section(
            "precio",
            "De dónde sale cada dato",
            (
                Rule(
                    "ultimo_precio",
                    "Último precio",
                    "La última venta de cada referencia al cliente (todas sus sucursales) en la "
                    "ventana elegida, 6 o 12 meses. Es el precio neto de descuentos, antes de IVA "
                    "y por unidad de inventario: una caja de 45 vendida a 149.551 es 3.323,36 por "
                    "unidad. No cuentan devoluciones ni obsequios.",
                    INFORMS,
                ),
                Rule(
                    "costo_actual",
                    "Costo actual",
                    "Costo promedio del último corte de inventario en el centro de operación que "
                    "factura al cliente. Si ese centro no tiene existencia, el promedio de todas "
                    "las bodegas.",
                    INFORMS,
                ),
                Rule(
                    "impuestos",
                    "Impuestos",
                    "IVA de venta del producto e impuestos en valor (impoconsumo, licores) de la "
                    "lista del cliente. Los precios del formato van antes de IVA.",
                    INFORMS,
                ),
            ),
        ),
        Section(
            "margen",
            "Margen y apoyos",
            (
                Rule(
                    "formulas",
                    "Fórmulas",
                    "Sin apoyo: (precio − costo) / precio. Con apoyo: (precio − (costo − apoyo)) "
                    "/ precio, donde el apoyo es el dinero del proveedor por unidad.",
                    INFORMS,
                ),
                Rule(
                    "margen_objetivo",
                    "Margen objetivo por canal",
                    "Mayoristas y Distribuidores y Subdistribuidor usan el margen mayoristas; "
                    "Salsamentarias, el suyo; cualquier otro canal, el margen Lista 1 del maestro "
                    "de márgenes. Si el apoyo aplicado trae su propio objetivo, manda el del "
                    "apoyo.",
                    INFORMS,
                ),
                Rule(
                    "apoyos",
                    "Qué apoyo se aplica",
                    "Los apoyos activos y vigentes hoy que cubren al cliente y la referencia, con "
                    "el mismo alcance del validador de pedidos. Se aplica el que trae más dinero. "
                    "Un margen mínimo autorizado solo aplica si no hay apoyo con dinero. Los "
                    "apoyos en revisión o de una bodega puntual no se aplican y se muestran.",
                    INFORMS,
                ),
            ),
        ),
        Section(
            "regla",
            "Regla general",
            (
                Rule(
                    "puntos_maximos",
                    f"Hasta {points} punto(s) bajo el objetivo",
                    f"Un precio nuevo puede dejar el margen con apoyo como máximo {points} "
                    f"punto(s) por debajo del margen objetivo (objetivo 20 %: mínimo "
                    f"{_points(Decimal('0.2') - policy.max_points_below_target)} %). Guardar un "
                    "precio por debajo no se bloquea: la línea queda marcada.",
                    WARNS,
                    {"puntos_maximos": points},
                ),
                Rule(
                    "generar",
                    "Generar el formato",
                    "Solo se genera si todas las líneas con precio nuevo cumplen la regla y tienen "
                    "costo y margen objetivo.",
                    HOLDS,
                ),
            ),
        ),
        Section(
            "formato",
            "Formato de actualización",
            (
                Rule(
                    "lista_destino",
                    "Lista de precios",
                    "Por defecto la lista SIESA del cliente. Si la lista es compartida, el cambio "
                    "llega a todos sus clientes: al generar se puede elegir otra.",
                    WARNS,
                ),
                Rule(
                    "archivos",
                    "Excel y plano",
                    "El Excel ACTUALIZACION LISTAS PRECIOS (hojas Precios e Impuestos en valor) y "
                    "el plano de importación de SIESA, con solo las líneas con precio nuevo, por "
                    "unidad de inventario y antes de IVA.",
                    INFORMS,
                ),
            ),
        ),
    )


__all__ = ["Rule", "Section", "catalog"]
