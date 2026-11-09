"""The rules of the quoter, written for the advisors (public «Reglas» page)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.order_validation.domain.catalog import HOLDS, INFORMS, WARNS, Rule, Section
from app.modules.quotations.domain import pricing
from app.modules.quotations.domain.entities import QuotationPolicy


def _points(value: Decimal) -> str:
    return f"{value * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def catalog(policy: QuotationPolicy) -> tuple[Section, ...]:
    """Every rule the quoter applies, with the values in force."""

    points = _points(policy.max_points_below_target)
    factor = f"{pricing.COST_FACTOR[pricing.GENERAL]}".replace(".", ",")
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
                    "unidad. No cuentan devoluciones ni obsequios. Las referencias son las que el "
                    "cliente compró y quedan en la lista que tiene asignada.",
                    INFORMS,
                ),
                Rule(
                    "costo_actual",
                    "Costo: el mayor entre cotización e inventario",
                    "Se toma el mayor entre la cotización vigente del proveedor (en unidad o kilo, "
                    "con ICUI e IBUA) y el costo promedio de inventario de la bodega de la lista "
                    "(bodega 2 para congelados y refrigerados; la lista 1 toma el mayor de sus 17 "
                    "bodegas; una lista sin bodega definida usa la del centro de operación que "
                    "factura). Se redondea hacia arriba y se multiplica por "
                    f"{factor} (las listas mayoristas no llevan ese factor). Lactalis cotiza por "
                    "sucursal: 003 para Centro y 001 para el resto.",
                    INFORMS,
                    {"factor_costo": factor},
                ),
                Rule(
                    "cotizacion_descartada",
                    "Cotización fuera de rango",
                    "Una cotización que pasa de 3 veces el último costo del producto es un error "
                    "de captura en SIESA (por ejemplo, el precio de la caja cargado como unidad): "
                    "no se usa y la línea avisa para revisarla. Un costo de $1 o menos no cuenta.",
                    WARNS,
                ),
                Rule(
                    "impuestos",
                    "Impuesto de licores",
                    "Solo el impuesto al consumo de licores, por unidad: el de la lista del "
                    "cliente en SIESA o, si no lo tiene, el del maestro de márgenes según la "
                    "regional de facturación (Costa para el centro de operación 004, Nacional "
                    "para el resto). El IVA no se muestra.",
                    INFORMS,
                ),
                Rule(
                    "inactivas",
                    "Referencias inactivas",
                    "Una referencia inactiva en SIESA se muestra marcada para reemplazarla. No "
                    "sale en el Excel ni en el plano.",
                    WARNS,
                ),
                Rule(
                    "productos_locales",
                    "Productos solo para cotización",
                    "Un producto que no existe en SIESA se puede cotizar con su descripción, "
                    "proveedor, precio antes de IVA e IVA. No tiene costo, así que la regla no "
                    "lo evalúa: no cuenta entre los precios cambiados, no impide confirmar y no "
                    "sale en el Excel, en el plano ni en la plantilla.",
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
                    "precio_optimo",
                    "Precio óptimo y precio mínimo",
                    "Precio óptimo: el que da exactamente el margen objetivo, (costo − apoyo) / "
                    "(1 − objetivo), en pesos enteros (hacia arriba en Salsamentarias y Market). "
                    f"Precio mínimo: el más bajo que cumple la regla (objetivo − {points} punto), "
                    "redondeado hacia arriba.",
                    INFORMS,
                ),
                Rule(
                    "bajo_objetivo",
                    "Margen actual bajo el objetivo",
                    "Se resalta la referencia cuyo último precio deja el margen, contando el "
                    f"apoyo, más de {points} punto bajo el objetivo: hay que revisarla y "
                    "actualizarla. Si un apoyo la cubre, no se resalta.",
                    WARNS,
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
                    "confirmar",
                    "Confirmar y validar",
                    "El asesor confirma la lista (queda pendiente de validación) solo si todas "
                    "las referencias activas con precio nuevo cumplen la regla y tienen costo, "
                    "margen objetivo y precio en pesos enteros. Mientras está pendiente no se "
                    "edita. Quien valida la aprueba (queda creada o actualizada y se liberan los "
                    "archivos) o la devuelve a borrador con una nota.",
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
                    "Actualización o creación de lista",
                    "Actualización: los precios se escriben en la lista SIESA que el cliente "
                    "tiene asignada; si es compartida, el cambio llega a todos sus clientes. "
                    "Creación: se crea una lista nueva con su código y descripción. Un cliente en "
                    "la lista 1 siempre va por creación: la lista 1 no se actualiza por este "
                    "medio. Asignar la lista nueva al cliente se hace en SIESA.",
                    WARNS,
                ),
                Rule(
                    "archivos",
                    "Excel y plano",
                    "El Excel ACTUALIZACION LISTAS PRECIOS (hojas Precios e Impuestos en valor) y "
                    "el plano de importación de SIESA, con las referencias activas con precio "
                    "nuevo, por unidad de inventario, antes de IVA y en pesos enteros. Para una "
                    "lista nueva, el plano trae primero su cabecera.",
                    INFORMS,
                ),
                Rule(
                    "plantilla",
                    "Plantilla para cambios masivos",
                    "La cotización se descarga en Excel con sus datos; se cambia el precio nuevo o "
                    "el margen nuevo de las referencias que se quiera y se sube completa. Manda la "
                    "celda que se cambió; las dos vacías quitan el precio nuevo.",
                    INFORMS,
                ),
            ),
        ),
    )


__all__ = ["Rule", "Section", "catalog"]
