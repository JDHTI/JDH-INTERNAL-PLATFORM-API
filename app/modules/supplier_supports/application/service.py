"""Use cases of the supplier supports master."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.commercial_masters.domain.entities import (
    CustomerRef,
    ProductRef,
    clean_nit,
    nit_check_digit,
)
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain import fields, filters, overlap, vocabulary
from app.modules.supplier_supports.domain.entities import (
    Checked,
    Conflict,
    ImportOutcome,
    Issue,
    LoadPlan,
    LoadResult,
    Support,
    SupportCost,
    SupportData,
    SupportPage,
    SupportRow,
    SupportView,
    Trace,
)
from app.modules.supplier_supports.domain.overlap import Supersede
from app.modules.supplier_supports.domain.ports import (
    Calendar,
    ManagerPolicy,
    MasterLookup,
    SupportRepository,
)
from app.modules.supplier_supports.domain.pricing import derive
from app.modules.supplier_supports.domain.units import infer_value_unit
from app.modules.supplier_supports.domain.validity import validity_on

MAX_PAGE_SIZE = 200
MAX_BATCH = 500
# Supports voided or expired in one mass action.
MAX_RETIRE = 2000
IMPORT_ACTOR = "importacion"
# Fields whose change can make a support cross another (rules v2).
_OVERLAP_FIELDS = (
    "kind",
    "commercial_supplier_id",
    "customer_nit",
    "customer_branch_id",
    "customer_group",
    "product_sku",
    "supplier_sku",
    "warehouse",
    "regionals",
    "channels",
    "business_units",
    "valid_from",
    "valid_to",
    "is_enabled",
)

_PCT_METHODS = ("pct_costo", "pct_precio_lista", "puntos_margen")
_VALUE_REQUIRED = (
    "pct_costo",
    "pct_precio_lista",
    "valor_unidad",
    "valor_precio_lista",
    "costo_neto",
    # Not precio_neto: a guaranteed margin has no value, only target_margin_pct.
    "puntos_margen",
    "pct_ventas",
)
_TEXT_FIELDS = (
    "external_ref",
    "program",
    "settlement_frequency",
    "customer_branch_id",
    "customer_name",
    "customer_group",
    "supplier_sku",
    "brand",
    "warehouse",
    "conditions",
    "end_condition",
    "authorized_by",
    "supplier_contact",
    "sales_rep",
    "notes",
    "source",
    "source_key",
)
_ARRAY_FIELDS = ("regionals", "business_units", "channels")
# Decimal places of each numeric column of ``supplier_supports``.
_SCALES = {
    **dict.fromkeys(
        (
            "supplier_value",
            "jdh_pct",
            "jdh_net_cost",
            "target_margin_pct",
            "min_margin_pct",
            "list_margin_pct",
            "supported_margin_pct",
        ),
        Decimal("0.000001"),
    ),
    **dict.fromkeys(
        (
            "pack_factor",
            "reference_cost",
            "reference_list_price",
            "min_quantity",
            "committed_volume",
            "budget_units",
            "units_used",
            "stock_quantity",
        ),
        Decimal("0.0001"),
    ),
    "budget_amount": Decimal("0.01"),
}
# Fields a PATCH may change; identity and audit columns are not among them.
EDITABLE_FIELDS = frozenset(
    {
        "kind",
        "calc_method",
        "funding",
        "settlement_method",
        "value_unit",
        "customer_nit",
        "product_sku",
        "supplier_value",
        "jdh_pct",
        "jdh_net_cost",
        "target_margin_pct",
        "min_margin_pct",
        "list_margin_pct",
        "supported_margin_pct",
        "pack_factor",
        "reference_cost",
        "reference_list_price",
        "reference_date",
        "min_quantity",
        "committed_volume",
        "budget_amount",
        "budget_units",
        "units_used",
        "stock_quantity",
        "valid_from",
        "valid_to",
        "status",
        "is_enabled",
        "promo_warehouses",
        "tiers",
        *_TEXT_FIELDS,
        *_ARRAY_FIELDS,
    }
)


class FieldError(InvalidInputError):
    """An invalid input that belongs to one field of the support (the bulk report names it)."""

    def __init__(self, detail: str, field: str) -> None:
        # ``field`` also goes in the 422 body, so the form marks the input to fix.
        super().__init__(detail, extra={"field": field})
        self.field = field


def _clean(value: str | None) -> str | None:
    return " ".join(value.split()) or None if value else None


def _normalize_scope(values: Sequence[str]) -> tuple[str, ...]:
    """Upper-case, trimmed, de-duplicated scope values; ``TODOS`` means everything."""

    upper = list(dict.fromkeys(item.upper() for value in values if (item := _clean(value))))
    return () if any(item in ("TODOS", "TODAS") for item in upper) else tuple(upper)


def _with_base_filters(
    group: filters.Group,
    customer_nits: Sequence[str] | None,
    supplier_codes: Sequence[str] | None,
    product_skus: Sequence[str] | None,
) -> filters.Group:
    """AND the base search (client, supplier, reference) with the free conditions."""

    base: list[filters.Condition | filters.Group] = []
    for field_name, values in (
        ("customer_nit", [nit for value in customer_nits or () if (nit := clean_nit(value))]),
        ("supplier.code", [code for value in supplier_codes or () if (code := _clean(value))]),
        ("product_sku", [sku for value in product_skus or () if (sku := _clean(value))]),
    ):
        if values:
            if len(values) > filters.MAX_LIST_VALUES:
                raise InvalidInputError(f"Máximo {filters.MAX_LIST_VALUES} valores por filtro base")
            base.append(filters.Condition(field_name, "in", values))
    return filters.Group("all", (*base, group)) if base else group


def _label(name: str) -> str:
    """How the form and the template name a field (its code when the catalog has none)."""

    spec = fields.FIELDS.get(name)
    return spec.label if spec is not None else name


def _branch_in(branch: str, branches: Sequence[str]) -> str | None:
    """The master's spelling of a branch id: ``1`` and ``1.0`` (a number in Excel) are ``001``."""

    text = branch.strip()
    if text in branches:
        return text
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit():
        return None
    return next((item for item in branches if item.isdigit() and int(item) == int(text)), None)


class PrefetchedMasters:
    """The masters of a whole file read once, so checking N rows is 3 queries, not 3N.

    A key that was not prefetched falls through to the real lookup.
    """

    def __init__(self, masters: MasterLookup) -> None:
        self.masters = masters
        # Keys asked so far and what was found (a key asked and absent is unknown).
        self._asked: dict[str, set[str]] = {"suppliers": set(), "products": set(), "nits": set()}
        self._suppliers: dict[str, UUID] = {}
        self._products: dict[str, ProductRef] = {}
        self._customers: dict[str, CustomerRef] = {}

    async def load(self, codes: set[str], skus: set[str], nits: set[str]) -> None:
        await self.commercial_suppliers_by_code(sorted(codes))
        await self.products_by_sku(sorted(skus))
        await self.customers(sorted(nits))

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        missing = [code for code in codes if code not in self._asked["suppliers"]]
        if missing:
            self._suppliers.update(await self.masters.commercial_suppliers_by_code(missing))
            self._asked["suppliers"].update(missing)
        return {code: self._suppliers[code] for code in codes if code in self._suppliers}

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]:
        missing = [sku for sku in skus if sku not in self._asked["products"]]
        if missing:
            self._products.update(await self.masters.products_by_sku(missing))
            self._asked["products"].update(missing)
        return {sku: self._products[sku] for sku in skus if sku in self._products}

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerRef]:
        missing = [nit for nit in nits if nit not in self._asked["nits"]]
        if missing:
            self._customers.update(await self.masters.customers(missing))
            self._asked["nits"].update(missing)
        return {nit: self._customers[nit] for nit in nits if nit in self._customers}

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]:
        return {nit: item.name for nit, item in (await self.customers(nits)).items()}

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        return await self.masters.commercial_supplier_names(ids)

    async def products_by_supplier_sku(
        self, supplier_id: UUID, supplier_skus: Sequence[str]
    ) -> dict[str, list[ProductRef]]:
        return await self.masters.products_by_supplier_sku(supplier_id, supplier_skus)

    async def business_group_members(self, groups: Sequence[str]) -> dict[str, list[tuple]]:
        return await self.masters.business_group_members(groups)

    async def commercial_suppliers_by_name(self, query: str) -> Any:
        return await self.masters.commercial_suppliers_by_name(query)


class SupplierSupportService:
    """Create, edit, void and search supplier supports."""

    def __init__(
        self,
        repository: SupportRepository,
        masters: MasterLookup,
        managers: ManagerPolicy,
        calendar: Calendar,
    ) -> None:
        """Create the service.

        Args:
            repository (SupportRepository): Support persistence port.
            masters (MasterLookup): Resolves suppliers, products and customers.
            managers (ManagerPolicy): Decides who may write.
            calendar (Calendar): Business-day clock.
        """

        self.repository = repository
        self.masters = masters
        self.managers = managers
        self.calendar = calendar

    # ------------------------------------------------------------ validation

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")

    def _check(self, data: SupportData) -> None:
        """Business rules that need no lookup."""

        for name, value, allowed in (
            ("kind", data.kind, vocabulary.KINDS),
            ("funding", data.funding, vocabulary.FUNDINGS),
            ("calc_method", data.calc_method, vocabulary.CALC_METHODS),
            ("status", data.status, vocabulary.STATUSES),
        ):
            if value not in allowed:
                raise FieldError(
                    f"{_label(name)}: valor no válido {value!r} (válidos: {', '.join(allowed)})",
                    name,
                )
        if data.settlement_method and data.settlement_method not in vocabulary.SETTLEMENTS:
            raise FieldError(
                f"{_label('settlement_method')}: valor no válido {data.settlement_method!r}",
                "settlement_method",
            )
        if data.value_unit and data.value_unit not in vocabulary.VALUE_UNITS:
            raise FieldError(
                f"{_label('value_unit')}: valor no válido {data.value_unit!r}", "value_unit"
            )
        if data.valid_from and data.valid_to and data.valid_to < data.valid_from:
            raise FieldError("La Fecha Fin no puede ser anterior a la Fecha Inicio", "valid_to")
        method = vocabulary.CALC_METHOD_LABELS.get(data.calc_method, data.calc_method)
        if data.calc_method in _VALUE_REQUIRED and data.supplier_value is None:
            raise FieldError(
                f"El tipo «{method}» necesita el «{_label('supplier_value')}»", "supplier_value"
            )
        if data.calc_method == "margen_minimo" and data.min_margin_pct is None:
            # The shown field: _normalize copies it to min_margin_pct.
            raise FieldError(
                "La Excepción margen JDH necesita el margen mínimo autorizado: escríbelo en "
                f"«{_label('supported_margin_pct')}»",
                "supported_margin_pct",
            )
        if data.calc_method == "precio_neto" and data.target_margin_pct is None:
            raise FieldError(
                f"El tipo «{method}» necesita el «{_label('target_margin_pct')}»",
                "target_margin_pct",
            )
        if data.calc_method in _PCT_METHODS and data.supplier_value is not None:
            if not Decimal(0) <= data.supplier_value <= Decimal(1):
                raise FieldError(
                    f"«{_label('supplier_value')}»: el porcentaje debe estar entre 0 % y 100 %",
                    "supplier_value",
                )
        elif data.supplier_value is not None and data.supplier_value < 0:
            raise FieldError(
                f"«{_label('supplier_value')}» no puede ser negativo", "supplier_value"
            )
        for name in ("target_margin_pct", "list_margin_pct", "supported_margin_pct"):
            value = getattr(data, name)
            if value is not None and not Decimal(0) <= value < Decimal(1):
                raise FieldError(f"«{_label(name)}» debe estar entre 0 % y 100 %", name)
        if data.jdh_pct is not None and not Decimal(-1) <= data.jdh_pct <= Decimal(1):
            raise FieldError("jdh_pct debe estar entre -1 y 1", "jdh_pct")
        if data.jdh_net_cost is not None:
            if data.calc_method != "costo_neto" or not Decimal(0) <= data.jdh_net_cost < Decimal(
                "1000000000000"
            ):
                raise FieldError(
                    "El costo JDH solo aplica a costo_neto y debe ser no negativo", "jdh_net_cost"
                )
        for name in ("reference_cost", "reference_list_price", "pack_factor"):
            value = getattr(data, name)
            if value is not None and value <= 0:
                raise FieldError(f"{name} debe ser mayor que cero", name)
        for tier in data.tiers:
            if tier.threshold_type not in vocabulary.THRESHOLD_TYPES:
                raise InvalidInputError(f"Tipo de escala inválido: {tier.threshold_type!r}")
            if tier.to_value is not None and tier.to_value < tier.from_value:
                raise InvalidInputError("Una escala termina antes de empezar")
        for issue in data.issues:
            if issue.code not in vocabulary.ISSUE_CODES:
                raise InvalidInputError(f"Inconsistencia desconocida: {issue.code!r}")

    def _normalize(self, data: SupportData) -> SupportData:
        changes: dict[str, Any] = {
            "customer_nit": clean_nit(data.customer_nit),
            "product_sku": _clean(data.product_sku),
        }
        # Rounded to the column's scale, so what is compared is what is stored (an Excel
        # float like 74625.73866666666 is 74625.7387 once saved).
        for name, places in _SCALES.items():
            value = getattr(data, name)
            if isinstance(value, Decimal):
                changes[name] = value.quantize(places)
        for name in _TEXT_FIELDS:
            changes[name] = _clean(getattr(data, name))
        for name in _ARRAY_FIELDS:
            changes[name] = _normalize_scope(getattr(data, name))
        if data.calc_method == "margen_minimo":
            # The form and the template show one JDH margin, «Excepción margen mínimo autorizado
            # JDH» (supported_margin_pct); for a margin exception it is the exception's margin.
            # Either field is accepted and both are kept equal, the shown one winning.
            shown = changes.get("supported_margin_pct", data.supported_margin_pct)
            stored = changes.get("min_margin_pct", data.min_margin_pct)
            if shown is not None:
                changes["min_margin_pct"] = shown
            elif stored is not None:
                changes["supported_margin_pct"] = stored
        return replace(data, **changes)

    async def _resolve(
        self,
        data: SupportData,
        *,
        supplier_code: str | None,
        strict: bool,
        previous: SupportData | None = None,
        masters: MasterLookup | None = None,
    ) -> SupportData:
        """Bind the support to real supplier, product and customer rows.

        With ``strict`` (manual writes) an unknown reference is an error; the
        importer passes ``strict=False`` and gets issues instead, so nothing in
        the workbook is lost. References equal to `previous` are not looked up again,
        so editing a note of a support with a pending SKU does not fail.
        """

        lookup = masters or self.masters
        issues = list(data.issues)
        supplier_id = data.commercial_supplier_id
        if supplier_code is not None:
            found = await lookup.commercial_suppliers_by_code([supplier_code.strip()])
            supplier_id = found.get(supplier_code.strip())
            if supplier_id is None:
                if strict:
                    raise FieldError(
                        f"Proveedor comercial desconocido: {supplier_code}", "supplier_code"
                    )
                issues.append(Issue("proveedor_sin_cruce", supplier_code, "supplier"))
        elif supplier_id is not None and (
            previous is None or previous.commercial_supplier_id != supplier_id
        ):
            if supplier_id not in await lookup.commercial_supplier_names([supplier_id]):
                raise InvalidInputError("El proveedor comercial no existe")

        unchanged = previous is not None and (previous.supplier_sku, previous.product_sku) == (
            data.supplier_sku,
            data.product_sku,
        )
        if not data.product_sku and data.supplier_sku and supplier_id is not None and not unchanged:
            data, issue = await self._jdh_sku_of(data, supplier_id, lookup, strict=strict)
            if issue is not None:
                issues.append(issue)

        product_id, brand = data.product_id, data.brand
        product: ProductRef | None = None
        if previous is not None and previous.product_sku == data.product_sku:
            product_id = previous.product_id
        elif data.product_sku:
            product = await self._product_of(data.product_sku, lookup)
            if product is None:
                if strict:
                    raise FieldError(
                        f"Referencia JDH desconocida: {data.product_sku}", "product_sku"
                    )
                product_id = None
                issues.append(Issue("sku_sin_cruce", data.product_sku, "product_sku"))
            else:
                product_id = product.id
                brand = product.brand or brand
                data = self._fill_unit(replace(data, product_sku=product.sku), product)
        else:
            product_id = None

        if data.customer_nit and (
            previous is None
            or (previous.customer_nit, previous.customer_branch_id)
            != (data.customer_nit, data.customer_branch_id)
        ):
            customer = await self._customer_of(data.customer_nit, lookup)
            if customer is None:
                if strict:
                    raise FieldError(
                        f"NIT de cliente desconocido: {data.customer_nit}", "customer_nit"
                    )
                issues.append(Issue("nit_sin_cruce", data.customer_nit, "customer_nit"))
            else:
                branch = data.customer_branch_id
                if branch is not None:
                    known_branch = _branch_in(branch, customer.branches)
                    if known_branch is None and strict:
                        shown = ", ".join(customer.branches[:10]) or "ninguna"
                        raise FieldError(
                            f"La sucursal {branch} no existe para el NIT {customer.nit} "
                            f"(sucursales: {shown})",
                            "customer_branch_id",
                        )
                    branch = known_branch or branch
                # The customer's name and business group are the master's, not typed.
                data = replace(
                    data,
                    customer_nit=customer.nit,
                    customer_name=customer.name or data.customer_name,
                    customer_group=customer.business_group or data.customer_group,
                    customer_branch_id=branch,
                )

        status = data.status
        if issues and status == "activo" and not strict:
            status = "pendiente_revision"
        unique = list({(issue.code, issue.field, issue.detail): issue for issue in issues}.values())
        return replace(
            data,
            commercial_supplier_id=supplier_id,
            product_id=product_id,
            brand=brand,
            issues=tuple(unique),
            status=status,
        )

    @staticmethod
    async def _product_of(sku: str, lookup: MasterLookup) -> ProductRef | None:
        """The product of a JDH SKU; ``012014`` is ``12014`` (JDH SKUs have no leading zeros)."""

        bare = sku.lstrip("0") if sku.isdigit() else sku
        found = await lookup.products_by_sku(list(dict.fromkeys([sku, bare])) if bare else [sku])
        return found.get(sku) or (found.get(bare) if bare else None)

    @staticmethod
    async def _customer_of(nit: str, lookup: MasterLookup) -> CustomerRef | None:
        """The customer of a NIT, written with or without its verification digit.

        The master stores NITs without it. A NIT that is not there but whose last digit is the
        DIAN check digit of the rest (``8001162991``, from ``800116299-1``) is read as the rest.
        """

        candidates = [nit]
        if len(nit) > 5 and nit_check_digit(nit[:-1]) == nit[-1]:
            candidates.append(nit[:-1])
        found = await lookup.customers(candidates)
        return next((found[item] for item in candidates if item in found), None)

    @staticmethod
    async def _jdh_sku_of(
        data: SupportData, supplier_id: UUID, lookup: MasterLookup, *, strict: bool
    ) -> tuple[SupportData, Issue | None]:
        """The JDH SKU of a support written only with the supplier's SKU.

        Without it a support would cover every product of the supplier. With ``strict`` an
        unknown or ambiguous code is an error; otherwise it becomes an issue to review.
        """

        code = (data.supplier_sku or "").strip()
        products = (await lookup.products_by_supplier_sku(supplier_id, [code])).get(
            code.upper(), []
        )
        if len(products) == 1:
            return replace(data, product_sku=products[0].sku), None
        problem = (
            f"El SKU de proveedor {code} corresponde a varias referencias JDH "
            f"({', '.join(sorted(product.sku for product in products))}): escribe el SKU JDH"
            if products
            else f"El SKU de proveedor {code} no está en el maestro de productos: "
            "escribe el SKU JDH"
        )
        if strict:
            raise FieldError(problem, "supplier_sku")
        return data, Issue("sku_sin_cruce", code, "supplier_sku")

    @staticmethod
    def _fill_unit(data: SupportData, product: ProductRef) -> SupportData:
        """Complete the supplier's SKU, the pack factor and the unit of the cost from the
        product master.

        The supplier's SKU is the master's whenever it has one (BI, 2026-11-18: it is pulled
        from the master, not typed). The pack factor falls back to the warehouse's. When the
        sheet did not say whether its cost is per box or per unit, it is inferred against the
        warehouse's average unit cost; a person who set the unit keeps it (provenance
        ``manual``).
        """

        pack = data.pack_factor or product.pack_factor
        unit, source = data.value_unit, data.unit_basis_source
        if unit is None:
            guess = infer_value_unit(data.reference_cost, product.avg_unit_cost, pack)
            if guess is not None:
                unit, source = guess, "inferido_dwh"
        elif source == "desconocido":
            source = "manual"
        return replace(
            data,
            supplier_sku=product.supplier_sku or data.supplier_sku,
            pack_factor=pack,
            value_unit=unit,
            unit_basis_source=source,
        )

    async def _prepare(
        self,
        data: SupportData,
        *,
        supplier_code: str | None,
        strict: bool,
        previous: SupportData | None = None,
        masters: MasterLookup | None = None,
    ) -> SupportData:
        prepared = await self._resolve(
            self._normalize(data),
            supplier_code=supplier_code,
            strict=strict,
            previous=previous,
            masters=masters,
        )
        self._check(prepared)
        if previous is None or (previous.kind, previous.calc_method) != (
            prepared.kind,
            prepared.calc_method,
        ):
            self._check_class(prepared)
        if prepared.kind != "excepcion_margen" and prepared.commercial_supplier_id is None:
            raise InvalidInputError("El apoyo requiere un proveedor comercial")
        return prepared

    @staticmethod
    def _check_class(data: SupportData) -> None:
        """Rules v2: only three classes, each with its types (a new or reclassified support)."""

        if data.kind not in vocabulary.ACTIVE_KINDS:
            labels = ", ".join(vocabulary.KIND_LABELS[kind] for kind in vocabulary.ACTIVE_KINDS)
            raise FieldError(
                f"La clase {vocabulary.KIND_LABELS.get(data.kind, data.kind)} ya no se crea "
                f"(clases: {labels})",
                "kind",
            )
        allowed = vocabulary.METHODS_BY_KIND[data.kind]
        if data.calc_method not in allowed:
            labels = ", ".join(vocabulary.CALC_METHOD_LABELS[method] for method in allowed)
            raise FieldError(
                f"{vocabulary.KIND_LABELS[data.kind]} admite estos tipos: {labels}",
                "calc_method",
            )

    # --------------------------------------------------------------- queries

    async def _views(self, rows: Sequence[SupportRow]) -> list[SupportView]:
        today = self.calendar.today()
        return [self._view(row, today) for row in rows]

    @staticmethod
    def _view(row: SupportRow, today: date) -> SupportView:
        data = row.support.data
        validity = validity_on(data.valid_from, data.valid_to, today)
        voided = data.status == "anulado"
        return SupportView(
            support=row.support,
            supplier_code=row.supplier_code,
            supplier_name=row.supplier_name,
            product_description=row.product_description,
            product_brand=row.product_brand,
            validity=validity,
            # The single state people see; a voided support is no longer in force.
            state="anulado" if voided else ("inactivo" if not data.is_enabled else validity),
            needs_review=data.status == "pendiente_revision",
            has_conflicts=bool(row.conflicts),
            derived=derive(
                data,
                row.dwh_cost.unit_cost if row.dwh_cost else None,
                row.product_margin,
                row.product_pack_factor,
            ),
            conflicts=row.conflicts,
            attachment_count=row.attachment_count,
            dwh_cost=row.dwh_cost,
            business_group=data.customer_group or row.customer_business_group,
            margin_list=row.margin_field,
        )

    async def get(self, support_id: UUID) -> SupportView:
        """One support with names, validity, conflicts and derived values."""

        row = await self.repository.get_row(support_id)
        if row is None:
            raise NotFoundError("Apoyo no encontrado")
        return (await self._views([row]))[0]

    async def search(
        self,
        *,
        match: str = "all",
        conditions: Sequence[Mapping[str, Any]] | None = None,
        customer_nits: Sequence[str] | None = None,
        supplier_codes: Sequence[str] | None = None,
        product_skus: Sequence[str] | None = None,
        q: str | None = None,
        active_on: date | None = None,
        validity: str | None = None,
        state: str | None = None,
        needs_review: bool | None = None,
        has_issues: bool | None = None,
        has_conflicts: bool | None = None,
        has_attachments: bool | None = None,
        effective_only: bool = False,
        include_voided: bool = False,
        sort: Sequence[str] | None = None,
        page: int = 1,
        size: int = 50,
    ) -> SupportPage:
        """Filter supports with the dynamic filter language.

        Args:
            match (str): ``all`` (AND) or ``any`` (OR) for the top-level conditions.
            conditions (Sequence[Mapping[str, Any]] | None): Conditions or nested groups;
                see :mod:`app.modules.supplier_supports.domain.filters`.
            customer_nits (Sequence[str] | None): Base search by client NIT(s).
            supplier_codes (Sequence[str] | None): Base search by commercial supplier code(s).
            product_skus (Sequence[str] | None): Base search by JDH reference(s). The three base
                filters are independent and optional (any one, two or all); the ones sent are
                ANDed with each other and with ``conditions``.
            q (str | None): Free text over program, client, SKU, brand, supplier and notes.
            active_on (date | None): Day used for ``validity`` (default today).
            validity (str | None): ``vigente``, ``vencido`` or ``programado`` on ``active_on``.
            state (str | None): The state people see: a validity or ``anulado``. ``anulado``
                lists voided supports; any other value excludes them.
            needs_review (bool | None): Only supports flagged ``pendiente_revision`` (or not).
            has_issues (bool | None): Only supports with (or without) data issues.
            has_conflicts (bool | None): Only supports that overlap another one.
            has_attachments (bool | None): Only supports with (or without) a supporting document.
            effective_only (bool): Keep the effective support per supplier x client x SKU.
            include_voided (bool): Include voided supports.
            sort (Sequence[str] | None): Ordering keys, ``-`` prefix for descending.
            page (int): 1-based page.
            size (int): Page size, up to :data:`MAX_PAGE_SIZE`.

        Returns:
            SupportPage: Matching supports.

        Raises:
            InvalidInputError: If a field, operator, value or bound is invalid.
        """

        spec = self._spec(
            match=match,
            conditions=conditions,
            customer_nits=customer_nits,
            supplier_codes=supplier_codes,
            product_skus=product_skus,
            q=q,
            active_on=active_on,
            validity=validity,
            state=state,
            needs_review=needs_review,
            has_issues=has_issues,
            has_conflicts=has_conflicts,
            has_attachments=has_attachments,
            effective_only=effective_only,
            include_voided=include_voided,
            sort=sort,
            page=page,
            size=size,
        )
        rows, total = await self.repository.search(spec)
        return SupportPage(items=await self._views(rows), total=total, page=page, size=size)

    async def search_ids(self, limit: int, **criteria: Any) -> list[UUID]:
        """Ids of the supports a search matches (same ``criteria`` as :meth:`search`).

        Args:
            limit (int): Most ids to return.
            **criteria (Any): The keyword arguments of :meth:`search`; paging is ignored.

        Returns:
            list[UUID]: Matching ids, up to ``limit``.

        Raises:
            InvalidInputError: If a field, operator, value or bound is invalid.
        """

        criteria.pop("page", None)
        criteria.pop("size", None)
        return await self.repository.search_ids(self._spec(**criteria), limit)

    def _spec(
        self,
        *,
        match: str = "all",
        conditions: Sequence[Mapping[str, Any]] | None = None,
        customer_nits: Sequence[str] | None = None,
        supplier_codes: Sequence[str] | None = None,
        product_skus: Sequence[str] | None = None,
        q: str | None = None,
        active_on: date | None = None,
        validity: str | None = None,
        state: str | None = None,
        needs_review: bool | None = None,
        has_issues: bool | None = None,
        has_conflicts: bool | None = None,
        has_attachments: bool | None = None,
        effective_only: bool = False,
        include_voided: bool = False,
        sort: Sequence[str] | None = None,
        page: int = 1,
        size: int = 50,
    ) -> filters.SearchSpec:
        if page < 1:
            raise InvalidInputError("La página debe ser mayor o igual a 1")
        if not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El tamaño debe estar entre 1 y {MAX_PAGE_SIZE}")
        if validity is not None and validity not in vocabulary.VALIDITIES:
            raise InvalidInputError(f"validity debe ser uno de: {', '.join(vocabulary.VALIDITIES)}")
        if state is not None and state not in vocabulary.STATES:
            raise InvalidInputError(f"state debe ser uno de: {', '.join(vocabulary.STATES)}")
        return filters.SearchSpec(
            group=_with_base_filters(
                filters.parse_group({"match": match, "conditions": list(conditions or [])}),
                customer_nits,
                supplier_codes,
                product_skus,
            ),
            q=_clean(q),
            active_on=active_on or self.calendar.today(),
            validity=validity,
            state=state,
            needs_review=needs_review,
            has_issues=has_issues,
            has_conflicts=has_conflicts,
            effective_only=effective_only,
            include_voided=include_voided,
            sort=filters.parse_sort(sort),
            page=page,
            size=size,
            has_attachments=has_attachments,
        )

    async def can_manage(self, principal: Principal) -> bool:
        """Whether the caller may create, edit, switch and void supports (for UI gating)."""

        return await self.managers.is_manager(principal)

    def search_fields(self) -> list[dict[str, Any]]:
        """Catalog of filterable fields, their operators and allowed values."""

        return filters.catalog()

    async def catalogs(self) -> dict[str, Any]:
        """Closed vocabularies plus the scope values already in use.

        ``active_kinds`` are the classes that can be created (rules v2) and
        ``methods_by_kind`` the types each one admits; ``kinds`` keeps every stored class.
        """

        values = await self.repository.scope_values()
        return {
            "active_kinds": list(vocabulary.ACTIVE_KINDS),
            "kind_labels": dict(vocabulary.KIND_LABELS),
            "calc_method_labels": dict(vocabulary.CALC_METHOD_LABELS),
            "methods_by_kind": {kind: list(m) for kind, m in vocabulary.METHODS_BY_KIND.items()},
            "kinds": list(vocabulary.KINDS),
            "fundings": list(vocabulary.FUNDINGS),
            "settlement_methods": list(vocabulary.SETTLEMENTS),
            "calc_methods": list(vocabulary.CALC_METHODS),
            "value_units": list(vocabulary.VALUE_UNITS),
            "statuses": list(vocabulary.STATUSES),
            "validities": list(vocabulary.VALIDITIES),
            "states": list(vocabulary.STATES),
            "issue_codes": list(vocabulary.ISSUE_CODES),
            **values,
        }

    async def conflicts(self, support_id: UUID) -> list[Conflict]:
        """Supports in force at the same time over the same scope."""

        if await self.repository.get(support_id) is None:
            raise NotFoundError("Apoyo no encontrado")
        return await self.repository.conflicts(support_id)

    # ---------------------------------------------------------------- writes

    async def create(
        self,
        principal: Principal,
        data: SupportData,
        *,
        supplier_code: str | None = None,
        on_overlap: str | None = None,
        overlap_reason: str | None = None,
    ) -> SupportView:
        """Create a support bound to real masters.

        Args:
            principal (Principal): The caller; must be a manager.
            data (SupportData): Support inputs.
            supplier_code (str | None): Commercial supplier code, instead of its id.
            on_overlap (str | None): ``vencer_anterior`` or ``anular_anterior`` when it
                crosses supports of the same class and scope; without it a crossing is a 409.
            overlap_reason (str | None): The observation left on the supports it replaces.

        Returns:
            SupportView: The created support.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If a rule fails or a master reference does not exist.
            ConflictError: If ``source_key`` is already used.
        """

        await self._require_manager(principal)
        prepared = await self._prepare(data, supplier_code=supplier_code, strict=True)
        plan = await self._overlaps([prepared], on_overlap, overlap_reason)
        saved = await self.repository.create(
            uuid4(), prepared, principal.email, datetime.now(UTC), supersede=plan
        )
        return await self.get(saved.id)

    async def _overlaps(
        self,
        items: Sequence[SupportData],
        on_overlap: str | None,
        reason: str | None,
        exclude: Sequence[UUID] = (),
    ) -> list[Supersede]:
        """Rules v2: no two supports of the same class and scope in force on the same day.

        Without ``on_overlap`` a crossing is a 409 that lists the supports crossed (in
        ``overlaps``), so the person decides; with it, the plan that expires or voids them.

        Raises:
            ConflictError: If a support crosses another and no decision was given.
            InvalidInputError: If two supports of the same request cross each other.
        """

        if on_overlap is not None and on_overlap not in overlap.OVERLAP_ACTIONS:
            raise InvalidInputError(
                f"on_overlap inválido (válidos: {', '.join(overlap.OVERLAP_ACTIONS)})"
            )
        plan: dict[UUID, Supersede] = {}
        crossed: dict[UUID, Support] = {}
        for index, data in enumerate(items):
            if not overlap.is_live(data):
                continue
            if any(overlap.overlaps(data, other) for other in items[:index]):
                raise InvalidInputError(
                    f"El apoyo {index + 1} se cruza con otro del mismo envío (misma clase, "
                    "cliente, referencia y vigencia)"
                )
            found = await self.repository.overlapping(data, exclude)
            if not found:
                continue
            if on_overlap is None:
                crossed.update((support.id, support) for support in found)
                continue
            for item in overlap.supersede(data, found, on_overlap, _clean(reason)):
                plan.setdefault(item.support_id, item)
        if crossed:
            listed = list(crossed.values())
            raise ConflictError(
                f"Se cruza con {len(listed)} apoyo(s) vigente(s) de la misma clase, cliente y "
                "referencia. Indica si se vence o se anula el anterior (on_overlap).",
                extra={"overlaps": [overlap.describe(support) for support in listed]},
            )
        return list(plan.values())

    async def create_many(
        self,
        principal: Principal,
        items: Sequence[tuple[SupportData, str | None]],
        *,
        on_overlap: str | None = None,
        overlap_reason: str | None = None,
    ) -> list[SupportView]:
        """Create several supports; all are validated before any is written.

        Args:
            principal (Principal): The caller; must be a manager.
            items (Sequence[tuple[SupportData, str | None]]): Inputs with supplier code.
            on_overlap (str | None): What happens to the supports they cross.
            overlap_reason (str | None): The observation left on those supports.

        Returns:
            list[SupportView]: The created supports, in order.
        """

        await self._require_manager(principal)
        if not 1 <= len(items) <= MAX_BATCH:
            raise InvalidInputError(f"Envía entre 1 y {MAX_BATCH} apoyos por lote")
        prepared = [
            await self._prepare(data, supplier_code=code, strict=True) for data, code in items
        ]
        plan = await self._overlaps(prepared, on_overlap, overlap_reason)
        return await self._save_all(principal, prepared, plan)

    async def _save_all(
        self,
        principal: Principal,
        prepared: Sequence[SupportData],
        supersede: Sequence[Supersede] = (),
    ) -> list[SupportView]:
        created = await self.repository.create_many(
            [(uuid4(), data) for data in prepared],
            principal.email,
            datetime.now(UTC),
            supersede=supersede,
        )
        return [await self.get(support.id) for support in created]

    async def check_many(
        self, principal: Principal, items: Sequence[tuple[SupportData, str | None]]
    ) -> list[Checked]:
        """Run the strict checks of a manual create on every item, collecting each failure.

        Unlike :meth:`create_many`, which stops at the first bad item, this reports them
        all, so a file can be corrected in one pass. Nothing is written.

        Args:
            principal (Principal): The caller; must be a manager.
            items (Sequence[tuple[SupportData, str | None]]): Inputs with supplier code.

        Returns:
            list[Checked]: One per item, in order: the normalized data or the error.
        """

        await self._require_manager(principal)
        masters = PrefetchedMasters(self.masters)
        await masters.load(
            {code.strip() for _, code in items if code and code.strip()},
            {sku for data, _ in items if (sku := _clean(data.product_sku))},
            {nit for data, _ in items if (nit := clean_nit(data.customer_nit))},
        )
        checked: list[Checked] = []
        for data, code in items:
            try:
                checked.append(
                    Checked(
                        data=await self._prepare(
                            data, supplier_code=code, strict=True, masters=masters
                        )
                    )
                )
            except InvalidInputError as exc:
                checked.append(Checked(error=exc.detail, field=getattr(exc, "field", None)))
        return checked

    async def create_for_group(
        self,
        principal: Principal,
        data: SupportData,
        *,
        supplier_code: str | None = None,
        on_overlap: str | None = None,
        overlap_reason: str | None = None,
    ) -> list[SupportView]:
        """One support per active customer of ``data.customer_group``, all or none.

        Raises:
            InvalidInputError: Without a group, with a NIT too, or a group with no customers.
        """

        await self._require_manager(principal)
        group = _clean(data.customer_group)
        if not group:
            raise FieldError("Escribe el grupo empresarial", "customer_group")
        if data.customer_nit:
            raise FieldError("Con grupo empresarial deja el NIT vacío", "customer_nit")
        members = (await self.group_members([group])).get(group, [])
        if not members:
            raise FieldError(
                f"El grupo empresarial {group} no tiene clientes activos", "customer_group"
            )
        if len(members) > MAX_BATCH:
            raise InvalidInputError(f"El grupo tiene {len(members)} clientes; máximo {MAX_BATCH}")
        items = [
            (
                replace(data, customer_group=group, customer_nit=nit, customer_name=name),
                supplier_code,
            )
            for nit, name in members
        ]
        return await self.create_many(
            principal, items, on_overlap=on_overlap, overlap_reason=overlap_reason
        )

    async def business_groups(self, query: str) -> list[tuple[str, int]]:
        """Business groups whose name contains ``query``, with their active customers."""

        text = _clean(query)
        if not text or len(text) < 2:
            return []
        return await self.masters.business_groups(text, 20)

    async def preview(
        self,
        principal: Principal,
        data: SupportData,
        *,
        supplier_code: str | None,
        cost: Callable[[str, str | None], Awaitable[SupportCost]] | None = None,
    ) -> SupportView:
        """The figures of a support before saving it (the creation form).

        The same checks as a creation, the DWH cost with the quoter's rule (``cost``; without
        it, or with the DWH down, the reference cost), the optimal margin of the customer's
        list and the product master's pack factor. Nothing is written.
        """

        await self._require_manager(principal)
        prepared = await self._prepare(data, supplier_code=supplier_code, strict=True)
        margins, pack, field, group = await self.repository.pricing_context(
            prepared.product_sku, prepared.customer_nit
        )
        dwh = None
        if cost is not None and prepared.product_sku:
            try:
                dwh = await cost(prepared.product_sku, prepared.customer_nit)
            except DependencyUnavailableError:
                dwh = None
        now = datetime.now(UTC)
        row = SupportRow(
            support=Support(
                id=uuid4(),
                data=prepared,
                void_reason=None,
                voided_at=None,
                created_by=principal.email,
                updated_by=principal.email,
                version=0,
                created_at=now,
                updated_at=now,
            ),
            dwh_cost=dwh,
            product_margins=margins,
            product_pack_factor=pack,
            customer_margin_field=field,
            customer_business_group=group,
        )
        return self._view(row, self.calendar.today())

    async def group_members(self, groups: Sequence[str]) -> dict[str, list[tuple]]:
        """Active customers ``(nit, name)`` of each business group (by its name)."""

        return await self.masters.business_group_members(groups)

    def validate(self, data: SupportData) -> None:
        """The rules that need no lookup, on an already resolved support (a merged version).

        Raises:
            FieldError: If a rule fails; ``field`` names the field.
        """

        self._check(data)

    async def create_checked(
        self, principal: Principal, items: Sequence[SupportData]
    ) -> list[SupportView]:
        """Save, in one transaction, supports already accepted by :meth:`check_many`."""

        await self._require_manager(principal)
        return await self._save_all(principal, items)

    async def live_for_suppliers(
        self, principal: Principal, supplier_ids: Sequence[UUID]
    ) -> list[Support]:
        """Supports of these suppliers that are not voided (a bulk load matches against them)."""

        await self._require_manager(principal)
        return await self.repository.live_for_suppliers(supplier_ids)

    async def apply_load(self, principal: Principal, plan: LoadPlan) -> LoadResult:
        """Write a bulk load planned by the bulk service, all or nothing."""

        await self._require_manager(principal)
        return await self.repository.apply_load(plan, principal.email, datetime.now(UTC))

    async def update(
        self,
        principal: Principal,
        support_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, Any],
        supplier_code: str | None = None,
        on_overlap: str | None = None,
        overlap_reason: str | None = None,
    ) -> SupportView:
        """Apply the fields the client sent; the rest stays as it is.

        A change of scope, dates or switch that makes it cross another support of the same
        class is refused like a creation, unless ``on_overlap`` says what to do with it.

        Args:
            principal (Principal): The caller; must be a manager.
            support_id (UUID): Support to change.
            expected_version (int): Version the client edited; a newer one is a conflict.
            changes (Mapping[str, Any]): Field values, limited to :data:`EDITABLE_FIELDS`.
            supplier_code (str | None): Move the support to another commercial supplier.

        Returns:
            SupportView: The updated support.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the support does not exist.
            ConflictError: If the support is voided or changed since ``expected_version``.
            InvalidInputError: If a field is not editable or a value breaks a rule.
        """

        await self._require_manager(principal)
        unknown = sorted(set(changes) - EDITABLE_FIELDS)
        if unknown:
            raise InvalidInputError(f"Campos no editables: {', '.join(unknown)}")
        current = await self.repository.get(support_id)
        if current is None:
            raise NotFoundError("Apoyo no encontrado")
        if current.data.status == "anulado":
            raise ConflictError("El apoyo está anulado y no se puede editar")
        if current.version != expected_version:
            raise ConflictError("El apoyo cambió desde que lo abriste; recárgalo")
        values = dict(changes)
        if "status" in values and values["status"] == "anulado":
            raise InvalidInputError("Para anular un apoyo usa su endpoint de anulación")
        if values.get("status") == "activo":
            values["issues"] = ()
        if "value_unit" in values:
            values["unit_basis_source"] = "manual" if values["value_unit"] else "desconocido"
        if "min_margin_pct" in values and "supported_margin_pct" not in values:
            # An exception edited by its other margin field: both stay equal (see _normalize).
            exception = values.get("calc_method", current.data.calc_method) == "margen_minimo"
            if exception:
                values["supported_margin_pct"] = values["min_margin_pct"]
        merged = replace(current.data, **values)
        prepared = await self._prepare(
            merged, supplier_code=supplier_code, strict=True, previous=current.data
        )
        plan: list[Supersede] = []
        if any(getattr(prepared, name) != getattr(current.data, name) for name in _OVERLAP_FIELDS):
            plan = await self._overlaps([prepared], on_overlap, overlap_reason, (support_id,))
        saved = await self.repository.update(
            support_id,
            prepared,
            expected_version,
            principal.email,
            datetime.now(UTC),
            supersede=plan,
        )
        return await self.get(saved.id)

    async def retire_many(
        self,
        principal: Principal,
        action: str,
        reason: str,
        *,
        items: Sequence[tuple[UUID, int | None]] = (),
        criteria: Mapping[str, Any] | None = None,
        last_day: date | None = None,
    ) -> list[Support]:
        """Void or expire several supports at once, with the observation; all or none.

        Args:
            principal (Principal): The caller; must be a manager.
            action (str): ``anular`` or ``vencer``.
            reason (str): Why (``void_reason``; an expiry adds it to the notes).
            items (Sequence[tuple[UUID, int | None]]): The supports chosen, with the version
                the person saw (``None``: the current one).
            criteria (Mapping[str, Any] | None): Or every support a search matches (the
                keyword arguments of :meth:`search`), voided ones excluded.
            last_day (date | None): Last day in force of an expiry (default yesterday). A
                programmed support that starts later is voided instead.

        Returns:
            list[Support]: The supports changed.

        Raises:
            InvalidInputError: If the action, the observation or the selection is invalid.
            ConflictError: If one is already voided or changed since it was seen.
        """

        await self._require_manager(principal)
        if action not in ("anular", "vencer"):
            raise InvalidInputError("La acción debe ser anular o vencer")
        text = _clean(reason)
        if not text:
            raise InvalidInputError("Indica la observación: por qué se anulan o se vencen")
        chosen = dict(items)
        if criteria is not None:
            found = await self.search_ids(MAX_RETIRE + 1, **{**criteria, "include_voided": False})
            chosen.update((support_id, None) for support_id in found if support_id not in chosen)
        if not 1 <= len(chosen) <= MAX_RETIRE:
            raise InvalidInputError(f"Selecciona entre 1 y {MAX_RETIRE} apoyos")
        day = None
        if action == "vencer":
            day = last_day or self.calendar.today() - timedelta(days=1)
        plan = [Supersede(support_id, version, day, text) for support_id, version in chosen.items()]
        return await self.repository.supersede(plan, principal.email, datetime.now(UTC))

    async def void(
        self, principal: Principal, support_id: UUID, *, expected_version: int, reason: str
    ) -> SupportView:
        """Void a support; supports are never deleted so history stays auditable."""

        await self._require_manager(principal)
        text = _clean(reason)
        if not text:
            raise InvalidInputError("Indica el motivo de la anulación")
        current = await self.repository.get(support_id)
        if current is None:
            raise NotFoundError("Apoyo no encontrado")
        if current.data.status == "anulado":
            raise ConflictError("El apoyo ya está anulado")
        saved = await self.repository.void(
            support_id, text, expected_version, principal.email, datetime.now(UTC)
        )
        return await self.get(saved.id)

    # ---------------------------------------------------------------- import

    async def import_support(
        self,
        data: SupportData,
        *,
        supplier_code: str | None,
        dry_run: bool = False,
        refresh: bool = False,
    ) -> ImportOutcome:
        """Create one support from the workbook; never fails on unknown references.

        Idempotent by ``source_key``: a support already loaded is skipped, unless
        ``refresh`` is set, which re-reads it from the workbook (new fields, fixed rules).
        A refresh only rewrites supports nobody has touched (``updated_by`` is still the
        importer), so manual edits, switches and voids are never overwritten.
        Unknown suppliers, SKUs or NITs become issues and the support is left
        ``pendiente_revision`` so nothing in the workbook is lost. With ``dry_run``
        nothing is written and the outcome reports what would happen.
        """

        if not data.source_key:
            raise InvalidInputError("La importación requiere source_key")
        existing = await self.repository.get_by_source_key(data.source_key)
        if existing is not None and not refresh:
            return ImportOutcome(created=False, support_id=existing.id)
        if existing is not None and existing.updated_by != IMPORT_ACTOR:
            return ImportOutcome(created=False, support_id=existing.id, skipped_manual=True)
        prepared = await self._resolve(
            self._normalize(data), supplier_code=supplier_code, strict=False
        )
        issues = list(prepared.issues)
        if prepared.commercial_supplier_id is None and prepared.kind != "excepcion_margen":
            # No supplier to attach to: kept as an exception so it is still queryable.
            if not any(issue.code == "proveedor_sin_cruce" for issue in issues):
                issues.append(Issue("proveedor_sin_cruce", supplier_code, "supplier"))
            prepared = replace(prepared, kind="excepcion_margen")
        prepared = replace(
            prepared,
            issues=tuple(issues),
            status="pendiente_revision" if issues else prepared.status,
        )
        if existing is not None:
            if dry_run:
                return ImportOutcome(
                    created=False, support_id=existing.id, issues=issues, updated=True
                )
            saved = await self.repository.update(
                existing.id,
                prepared,
                existing.version,
                IMPORT_ACTOR,
                datetime.now(UTC),
                Trace("importacion"),
            )
            return ImportOutcome(created=False, support_id=saved.id, issues=issues, updated=True)
        if dry_run:
            return ImportOutcome(created=True, support_id=None, issues=issues)
        saved = await self.repository.create(
            uuid4(), prepared, IMPORT_ACTOR, datetime.now(UTC), Trace("importacion")
        )
        return ImportOutcome(created=True, support_id=saved.id, issues=issues)


__all__ = ["EDITABLE_FIELDS", "FieldError", "SupplierSupportService"]
