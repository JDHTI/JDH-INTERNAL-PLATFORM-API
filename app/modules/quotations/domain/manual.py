"""The quoter's operating manual for Comercial (the «Manual» page).

The calculation rules (costs, warehouses, factors, formulas) are BI's and live in
:mod:`catalog`; Comercial sees how to use the quoter: steps, what each column means, how the
supports show up, how to ask for one and what happens after confirming.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualStep:
    """One instruction of a manual section."""

    title: str
    text: str


@dataclass(frozen=True)
class ManualSection:
    """A section of the operating manual."""

    code: str
    title: str
    steps: tuple[ManualStep, ...]


MANUAL: tuple[ManualSection, ...] = (
    ManualSection(
        "crear",
        "Crear una cotización",
        (
            ManualStep(
                "Elige el cliente",
                "Busca por NIT, razón social o grupo empresarial. La cotización trae las "
                "referencias que el cliente compró en la ventana elegida (6 o 12 meses) con su "
                "último precio.",
            ),
            ManualStep(
                "Agrega referencias",
                "Busca por SKU o descripción para cotizar algo que el cliente aún no compra. "
                "Un producto que no está en SIESA se puede cotizar con su descripción y precio, "
                "pero no sale en el plano.",
            ),
            ManualStep(
                "Actualiza",
                "Si cambian el inventario o los apoyos durante el día, usa Actualizar para "
                "recalcular los precios sin perder lo que escribiste.",
            ),
        ),
    ),
    ManualSection(
        "lineas",
        "Qué ves en cada línea",
        (
            ManualStep(
                "Precio sugerido",
                "El precio con el que la referencia queda en el margen esperado para la lista "
                "del cliente. Si la referencia tiene un apoyo del proveedor, es el precio "
                "apoyado sugerido: el cliente recibe el valor del apoyo.",
            ),
            ManualStep(
                "Precio mínimo",
                "El precio más bajo permitido. Si el apoyo trae un margen mínimo autorizado por "
                "JDH, el mínimo sale de ese margen. Por debajo, la línea queda marcada para "
                "que BI la revise.",
            ),
            ManualStep(
                "Inventario",
                "Existencias y disponible de la referencia en las bodegas del cliente. "
                "Cotiza con cuidado lo que no tiene disponible.",
            ),
            ManualStep(
                "Marcas de la línea",
                "Referencia inactiva, sin margen, apoyo en revisión o apoyo de otra bodega: "
                "cada marca explica qué revisar antes de confirmar.",
            ),
        ),
    ),
    ManualSection(
        "apoyos",
        "Apoyos",
        (
            ManualStep(
                "Apoyo aplicado",
                "Cuando el proveedor apoya la referencia para ese cliente, la línea muestra el "
                "apoyo, su tipo y su vigencia. Solo se aplica uno: el que más beneficia.",
            ),
            ManualStep(
                "Pedir un apoyo interno",
                "Desde la cotización puedes pedir a BI un margen apoyado para una o varias "
                "referencias de un proveedor: escribe el margen, con quién se acordó y el "
                "motivo. BI lo aprueba por única vez o de forma persistente, o lo rechaza; la "
                "línea muestra el estado.",
            ),
            ManualStep(
                "Solicitudes de margen JDH",
                "Sobre un apoyo vigente puedes pedir un margen mínimo autorizado por JDH desde "
                "el maestro de apoyos (Solicitudes). BI decide y queda en el historial.",
            ),
        ),
    ),
    ManualSection(
        "confirmar",
        "Confirmar y descargar",
        (
            ManualStep(
                "Confirmar",
                "Confirma cuando los precios estén listos. La cotización queda pendiente de "
                "validación y ya no se edita.",
            ),
            ManualStep(
                "Validación",
                "BI la aprueba (se liberan los archivos) o la devuelve a borrador con una nota "
                "para que corrijas.",
            ),
            ManualStep(
                "Archivos",
                "Con la cotización aprobada descargas el Excel para el cliente y el plano para "
                "SIESA. Asignar una lista nueva al cliente se hace en SIESA.",
            ),
        ),
    ),
)


__all__ = ["MANUAL", "ManualSection", "ManualStep"]
