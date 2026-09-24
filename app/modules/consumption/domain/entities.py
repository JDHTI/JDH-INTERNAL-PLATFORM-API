"""Pure domain entities for the consumption module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Client:
    """Represent one client row exposed by the consumption data source."""

    client_key: str
    nit: str | None
    razon_social: str | None
    sucursal: str | None
    nombre_establecimiento: str | None
    zona_logistica: str | None
    departamento: str | None
    ciudad: str | None
    nombre_vendedor: str | None
    telefono: str | None
    celular: str | None
    direccion: str | None
    correo: str | None
    unidad_negocio: str | None
    canal: str | None


@dataclass(frozen=True, slots=True)
class ClientPage:
    """Represent one cursor-paginated slice of clients."""

    items: tuple[Client, ...]
    next_cursor: str | None
    has_more: bool
