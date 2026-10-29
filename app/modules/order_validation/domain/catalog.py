"""The rules of the retained orders validator, written for the people who use it.

One source for the public «Reglas» page: the values come from the policy in force, so the page
never says 18 % while the system applies another number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.modules.order_validation.domain.entities import OrderPolicy

# What a rule does to a line or an order.
RELEASES = "libera"
HOLDS = "retiene"
WARNS = "alerta"
INFORMS = "informa"


@dataclass(frozen=True)
class Rule:
    """One rule as people read it."""

    code: str
    title: str
    description: str
    effect: str
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Section:
    """A group of rules (lines, order, approvals...)."""

    code: str
    title: str
    rules: tuple[Rule, ...]


def _points(value: Decimal) -> str:
    return f"{value * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def catalog(policy: OrderPolicy) -> tuple[Section, ...]:
    """Every rule the validator applies, in the order they are applied."""

    below = _points(policy.max_points_below_target)
    minimum = _points(policy.tolerance_min_order_margin)
    lines = policy.tolerance_max_lines
    return (
        Section(
            "linea",
            "Cómo se decide cada línea",
            (
                Rule(
                    "margen_referencia",
                    "Margen de referencia por canal",
                    "Cada línea se compara con el margen del producto para su canal: Mayoristas "
                    "y Distribuidores y Subdistribuidor usan el margen mayoristas; Salsamentarias, "
                    "el suyo; cualquier otro canal, el margen lista 1. El canal es el del archivo "
                    "o, si no lo trae, el del cliente en el maestro.",
                    INFORMS,
                ),
                Rule(
                    "apoyos_aplicables",
                    "Qué apoyos aplican",
                    "Del mismo proveedor, activos (no anulados ni desactivados), vigentes en la "
                    "fecha del pedido y con el alcance de la línea: cliente, referencia, bodega, "
                    "canal, regional y unidad de negocio (vacío = todos). Las condiciones "
                    "especiales del libro no se aplican.",
                    INFORMS,
                ),
                Rule(
                    "margen_100",
                    "Producto con margen 100 %",
                    "Una línea con margen 100 % no trae costo: queda pendiente con alerta y el "
                    "pedido entero no se libera de ninguna forma, ni por su margen ni con "
                    "aprobación manual, hasta corregirlo en el ERP y volver a subir el archivo. "
                    "Inflaría el margen acumulado y dejaría pasar pedidos que no deben pasar.",
                    HOLDS,
                ),
                Rule(
                    "datos_incompletos",
                    "Datos incompletos",
                    "Una línea sin proveedor, precio o margen queda pendiente: no se puede "
                    "evaluar.",
                    HOLDS,
                ),
                Rule(
                    "falta_impuesto_licor",
                    "Productos de licor sin impuesto",
                    "Un producto con impuesto al consumo de licor debe traerlo en la línea "
                    "(columna Vlr. imp. IMPTOLIC). Si falta, la línea queda pendiente aunque "
                    "cumpla el margen o el apoyo, y el pedido no se libera por su margen; solo "
                    "una persona puede aprobarlo.",
                    HOLDS,
                ),
                Rule(
                    "cumple_con_apoyo",
                    "Cumple con el apoyo",
                    "La línea queda lista si un apoyo activo que la cubre lleva su margen al "
                    "objetivo pactado (o, si el apoyo no trae objetivo, al margen del producto "
                    "para el canal). Si hay varios, se muestra el de mayor holgura.",
                    RELEASES,
                ),
                Rule(
                    "cumple_margen_lista",
                    "Cumple el margen del producto",
                    "Sin apoyo que se cumpla, la línea queda lista si su margen ya alcanza el "
                    "margen del producto para el canal.",
                    RELEASES,
                ),
                Rule(
                    "pendientes",
                    "Pendientes de validación",
                    "Queda pendiente la línea con un apoyo en revisión, vencido en la fecha del "
                    "pedido o no evaluable por pedido (rebates sobre ventas), la que no cumple su "
                    "apoyo y la que no tiene apoyo y no alcanza el margen del producto.",
                    HOLDS,
                ),
            ),
        ),
        Section(
            "pedido",
            "Cómo se decide el pedido",
            (
                Rule(
                    "margen_pedido",
                    "Margen del pedido con apoyos",
                    "El margen del pedido es el promedio de los márgenes de sus líneas ponderado "
                    "por valor (precio × cantidad), y cada línea cuenta con su apoyo cuando un "
                    "apoyo activo con dinero la decidió: una línea al 1 % que el apoyo lleva al "
                    "22 % suma como 22 %. No cuentan las líneas sin precio, cantidad o margen ni "
                    "las de margen 100 %. El objetivo del pedido se pondera igual.",
                    INFORMS,
                ),
                Rule(
                    "lineas",
                    "Todas las líneas listas",
                    "Un pedido cuyas líneas están todas listas queda listo para liberación.",
                    RELEASES,
                ),
                Rule(
                    "margen",
                    "Margen del pedido y umbral",
                    "Un pedido pendiente solo por margen se libera si su margen más el umbral del "
                    "archivo alcanza su objetivo. El umbral (0 a 20 puntos, por defecto 0) se suma "
                    "al margen del pedido, nunca al de una línea.",
                    RELEASES,
                    {"umbral": "lo fija cada archivo"},
                ),
                Rule(
                    "tolerancia",
                    f"Tolerancia de {lines} líneas",
                    f"Un pedido con margen de {minimum} % o más se libera aunque tenga hasta "
                    f"{lines} líneas por debajo de su objetivo, si ninguna está a más de {below} "
                    f"puntos de él (ejemplo: objetivo 12 %, línea al 8 %, pedido al {minimum} % "
                    "o más: pasa).",
                    RELEASES,
                    {
                        "margen_minimo_pedido": f"{minimum} %",
                        "lineas_maximas": str(lines),
                        "puntos_maximos": below,
                    },
                ),
                Rule(
                    "bajo_objetivo",
                    f"Línea a más de {below} puntos de su objetivo",
                    f"Si una línea está a más de {below} puntos por debajo de su margen objetivo "
                    "(con su apoyo), queda en alerta y el pedido no se libera por su margen, ni "
                    "por el umbral ni por la tolerancia. Solo una persona puede aprobarlo.",
                    WARNS,
                    {"puntos_maximos": below},
                ),
                Rule(
                    "no_rescatables",
                    "Lo que el margen no rescata",
                    "El margen del pedido no libera un pedido con una línea pendiente por otra "
                    "razón (datos incompletos, apoyo en revisión, vencido o no evaluable), sin "
                    "margen de referencia, sin el impuesto al licor o con margen 100 %.",
                    HOLDS,
                ),
            ),
        ),
        Section(
            "aprobacion",
            "Aprobación manual",
            (
                Rule(
                    "manual",
                    "Aprobación manual",
                    "Quien gestiona puede liberar a mano pedidos pendientes, todos o ninguno, con "
                    "una nota opcional. La aprobación es del pedido: sus líneas conservan su "
                    "motivo, y volver a validar el archivo la conserva. No se pueden aprobar "
                    "pedidos con un producto en margen 100 %.",
                    RELEASES,
                ),
            ),
        ),
    )


__all__ = ["Rule", "Section", "catalog"]
