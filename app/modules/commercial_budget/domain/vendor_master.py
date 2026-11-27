"""What uploading a rep master would change, before it is sent (2026-11-26).

The Excel «Maestro Vendedores JDH» is the source of truth of each rep's team, regional and
business unit. dwh-platform loads it (``cargar_maestro_vendedores.py``) with no simulation:
every upload writes. This module reads the file with the same rules and compares it with
what the warehouse publishes today, so BI sees every change first.

The loader's rules mirrored here:

- Header in row 1 of each sheet, normalized: upper case, spaces collapsed; «N° DOCUMENTO»
  is «DOCUMENTO». Required: DOCUMENTO and UNIDAD NEGOCIO in COMERCIALES JDH, CEDULA in
  Coordinadores and Directores; repeated headers reject the file.
- A rep row is identified by its DOCUMENTO (the tercero's identification or NIT, with the
  leading-zero variants); none or several reps is a rejection.
- The team is the rep row's EQUIPO, created by name; its class is the first CLASE seen for
  the team (a different one later is a warning).
- REGIONAL: an operation center's description gives its regional (ACO URABA → URABA), else
  the regional's name; empty, VARIOS or NACIONAL is VARIOS.
- UNIDAD NEGOCIO: accents dropped, MIX FOOD-MASIVO → FOOD SERVICE / MASIVO, MIX
  RETAIL-MASIVO → RETAIL / MASIVO.
- A coordinator's team (sheet Coordinadores) is linked only when a rep row has that same
  team name; otherwise it keeps its previous team. Reps not in the file keep everything.

The platform's own rule on top (BI, 2026-11-27): the teams come from the team master
(``externo.equipo`` as ``consumo.equipos`` publishes it, plus the teams created in the
platform). The template offers only those, a row with another team is rejected, and the
class is the master's: the platform writes it in the file before sending it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

REPS_SHEET = "COMERCIALES JDH"
COORDINATORS_SHEET = "Coordinadores"
DIRECTORS_SHEET = "Directores"
WILDCARD_REGIONALS = frozenset({"VARIOS", "NACIONAL"})
NO_PERSON = frozenset({"N/A", "NA", "NINGUNO", "NO APLICA", "-"})
VARIOS = "VARIOS"
_UNIT_ALIASES = {
    "MIX FOOD-MASIVO": "FOOD SERVICE / MASIVO",
    "MIX RETAIL-MASIVO": "RETAIL / MASIVO",
}
FIELDS = ("team", "team_class", "regional", "business_unit")
FIELD_LABELS = {
    "team": "Equipo",
    "team_class": "Clase",
    "regional": "Regional",
    "business_unit": "Unidad de negocio",
}


def normalize(value: object) -> str | None:
    """Upper case, spaces collapsed, ends trimmed (the loader's ``normalizar``)."""

    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = re.sub(r"\s+", " ", str(value)).strip().upper()
    return text or None


def normalize_unit(value: object) -> str | None:
    """A business unit as the loader stores it (``normalizar_unidad_negocio``)."""

    text = normalize(value)
    if text is None:
        return None
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = _UNIT_ALIASES.get(text, text)
    return re.sub(r"\s*/\s*", " / ", text)


def document_variants(key: str) -> list[str]:
    """The same number with other leading zeros (Excel keeps '0314' as 314)."""

    if not key.isdigit():
        return [key]
    bare = key.lstrip("0") or "0"
    others = dict.fromkeys("0" * n + bare for n in range(4))
    return [key, *(item for item in others if item != key)]


@dataclass(frozen=True)
class RepRow:
    """A row of the rep sheet."""

    row: int
    document: str | None
    name: str | None
    team: str | None
    team_class: str | None
    regional: str | None
    business_unit: str | None
    coordinator: str | None
    director: str | None


@dataclass(frozen=True)
class CoordinatorRow:
    row: int
    document: str | None
    name: str | None
    team: str | None


@dataclass(frozen=True)
class MasterFile:
    reps: list[RepRow]
    coordinators: list[CoordinatorRow]
    director_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CurrentRep:
    """A rep as the warehouse publishes it today (``consumo.vendedores``)."""

    code: str
    name: str | None
    documents: tuple[str, ...]
    team: str | None
    team_class: str | None
    regional: str | None
    business_unit: str | None
    is_active: bool = True


@dataclass(frozen=True)
class CurrentCoordinator:
    document: str
    name: str | None
    team: str | None


@dataclass(frozen=True)
class FieldChange:
    code: str
    name: str | None
    field: str
    current: str | None
    new: str | None


@dataclass(frozen=True)
class RowProblem:
    sheet: str
    row: int
    value: str | None
    reason: str


@dataclass(frozen=True)
class MissingRep:
    """An active rep with recent sales that the file leaves out (keeps its assignment)."""

    code: str
    name: str | None
    team: str | None
    lines: int


@dataclass(frozen=True)
class CoordinatorIssue:
    document: str | None
    name: str | None
    team: str | None
    reason: str


@dataclass(frozen=True)
class TeamCount:
    team: str
    current: int
    new: int


@dataclass
class Preview:
    reps_read: int = 0
    reps_resolved: int = 0
    unchanged: int = 0
    changes: list[FieldChange] = field(default_factory=list)
    rejected: list[RowProblem] = field(default_factory=list)
    warnings: list[RowProblem] = field(default_factory=list)
    new_teams: list[str] = field(default_factory=list)
    emptied_teams: list[str] = field(default_factory=list)
    teams: list[TeamCount] = field(default_factory=list)
    missing: list[MissingRep] = field(default_factory=list)
    coordinators: list[CoordinatorIssue] = field(default_factory=list)

    @property
    def changed_reps(self) -> int:
        return len({item.code for item in self.changes})


DWH = "dwh"
PLATFORM = "plataforma"


@dataclass(frozen=True)
class TeamEntry:
    """A team of the master the template offers (2026-11-27).

    ``dwh``: published by the warehouse (``consumo.equipos``, from ``externo.equipo``).
    ``plataforma``: created in the platform and not loaded yet; the first upload with a rep in
    it creates it in the warehouse. A stored row may also hide a warehouse team
    (``is_active`` false) or give it the class the next upload writes.
    """

    name: str
    team_class: str | None
    source: str
    is_active: bool = True
    reps: int = 0
    note: str | None = None


@dataclass(frozen=True)
class TeamOverride:
    """The platform's row for a team: a new one, or a warehouse team's class or visibility."""

    name: str
    team_class: str | None
    is_active: bool
    note: str | None = None


def team_catalog(
    warehouse: Mapping[str, tuple[str | None, int]], overrides: Iterable[TeamOverride]
) -> list[TeamEntry]:
    """The teams of the master: the warehouse's (name → class, active reps), each with the
    platform's class or visibility when it has a row, plus the ones created in the platform."""

    stored = {item.name: item for item in overrides}
    entries = []
    for name, (team_class, reps) in warehouse.items():
        own = stored.pop(name, None)
        entries.append(
            TeamEntry(
                name,
                own.team_class if own and own.team_class else team_class,
                DWH,
                own.is_active if own else True,
                reps,
                own.note if own else None,
            )
        )
    entries += [
        TeamEntry(item.name, item.team_class, PLATFORM, item.is_active, 0, item.note)
        for item in stored.values()
    ]
    return sorted(entries, key=lambda item: item.name)


def allowed_teams(catalog: Iterable[TeamEntry]) -> dict[str, str | None]:
    """The teams a file may use (the active ones), with their class."""

    return {item.name: item.team_class for item in catalog if item.is_active}


def _regional(name: str | None, centers: Mapping[str, str]) -> tuple[str, bool]:
    """The regional the loader stores (as ``consumo`` shows it) and whether it resolved."""

    if name is None or name in WILDCARD_REGIONALS:
        return VARIOS, True
    if name in centers:
        return centers[name], True
    return name, False


def preview(
    master: MasterFile,
    current: Sequence[CurrentRep],
    coordinators: Sequence[CurrentCoordinator],
    *,
    centers: Mapping[str, str],
    regionals: Iterable[str],
    recent: Mapping[str, int],
    teams: Mapping[str, str | None] | None = None,
) -> Preview:
    """Compare the file with the warehouse.

    Args:
        master (MasterFile): The file, read.
        current (Sequence[CurrentRep]): Every rep the warehouse has.
        coordinators (Sequence[CurrentCoordinator]): The coordinators it has.
        centers (Mapping[str, str]): Operation center description → its regional.
        regionals (Iterable[str]): The ERP's regional names.
        recent (Mapping[str, int]): Rep code → sale lines in the recent months.
        teams (Mapping[str, str | None] | None): The team master (name → class, 2026-11-27):
            a team outside it rejects the row, and the class is the master's, never the
            file's. ``None`` keeps the loader's rule (any team, the first class seen).
    """

    result = Preview(reps_read=len(master.reps))
    by_document: dict[str, list[CurrentRep]] = defaultdict(list)
    for rep in current:
        for document in rep.documents:
            if rep not in by_document[document]:
                by_document[document].append(rep)
    known_regionals = {normalize(name) for name in regionals if normalize(name)}
    center_regionals = {key: value for key, value in centers.items()}

    def find(document: str) -> list[CurrentRep]:
        exact = by_document.get(document)
        if exact:
            return exact
        found: list[CurrentRep] = []
        for variant in document_variants(document)[1:]:
            for rep in by_document.get(variant, ()):
                if rep not in found:
                    found.append(rep)
        return found

    team_class: dict[str, str | None] = {}
    resolved: dict[str, tuple[CurrentRep, RepRow]] = {}
    for row in master.reps:
        if row.document is None:
            result.rejected.append(RowProblem(REPS_SHEET, row.row, row.name, "DOCUMENTO vacío"))
            continue
        candidates = find(row.document)
        if not candidates:
            result.rejected.append(
                RowProblem(
                    REPS_SHEET, row.row, row.name, "El documento no es de ningún vendedor del ERP"
                )
            )
            continue
        if len(candidates) > 1:
            codes = ", ".join(sorted(rep.code for rep in candidates))
            result.rejected.append(
                RowProblem(
                    REPS_SHEET, row.row, row.name, f"El documento es de varios vendedores: {codes}"
                )
            )
            continue
        if teams is not None and row.team is not None and row.team not in teams:
            result.rejected.append(
                RowProblem(
                    REPS_SHEET,
                    row.row,
                    row.name,
                    f"El equipo '{row.team}' no está en el maestro de equipos: escógelo de la "
                    "lista o créalo primero en la plataforma",
                )
            )
            continue
        rep = candidates[0]
        if row.document not in rep.documents:
            result.warnings.append(
                RowProblem(
                    REPS_SHEET, row.row, row.name, f"Se encontró sin los ceros del ERP: {rep.code}"
                )
            )
        if rep.code in resolved:
            result.warnings.append(
                RowProblem(
                    REPS_SHEET,
                    row.row,
                    row.name,
                    f"El vendedor {rep.code} ya viene en la fila {resolved[rep.code][1].row}: "
                    "gana la última",
                )
            )
        resolved[rep.code] = (rep, row)
        if row.team and teams is not None:
            team_class[row.team] = teams[row.team]
        elif row.team:
            if row.team not in team_class or team_class[row.team] is None:
                team_class[row.team] = row.team_class
            elif row.team_class and team_class[row.team] != row.team_class:
                result.warnings.append(
                    RowProblem(
                        REPS_SHEET,
                        row.row,
                        row.name,
                        f"CLASE '{row.team_class}' contradice '{team_class[row.team]}' del equipo "
                        f"'{row.team}': queda la primera",
                    )
                )
        else:
            result.warnings.append(
                RowProblem(
                    REPS_SHEET, row.row, row.name, "Sin EQUIPO: el vendedor queda sin equipo"
                )
            )
    result.reps_resolved = len(resolved)

    for code, (rep, row) in sorted(resolved.items()):
        regional, known = _regional(row.regional, center_regionals)
        if not known and regional not in known_regionals:
            result.warnings.append(
                RowProblem(
                    REPS_SHEET,
                    row.row,
                    row.name,
                    f"REGIONAL '{row.regional}' no es un centro de operación ni una regional del "
                    "ERP: queda en VARIOS",
                )
            )
            regional = VARIOS
        if row.regional is None:
            result.warnings.append(
                RowProblem(REPS_SHEET, row.row, row.name, "REGIONAL vacía: queda en VARIOS")
            )
        new = {
            "team": row.team,
            "team_class": team_class.get(row.team) if row.team else None,
            "regional": regional,
            "business_unit": row.business_unit,
        }
        old = {
            "team": rep.team,
            "team_class": rep.team_class,
            "regional": rep.regional or VARIOS,
            "business_unit": rep.business_unit,
        }
        changed = False
        for name in FIELDS:
            if (old[name] or None) != (new[name] or None):
                result.changes.append(FieldChange(code, rep.name, name, old[name], new[name]))
                changed = True
        if not changed:
            result.unchanged += 1

    # Teams before and after: reps not in the file keep their team.
    before = Counter(rep.team for rep in current if rep.team and rep.is_active)
    after = Counter(
        rep.team for rep in current if rep.team and rep.is_active and rep.code not in resolved
    )
    after.update(row.team for _, row in resolved.values() if row.team)
    result.teams = [
        TeamCount(team, before.get(team, 0), after.get(team, 0))
        for team in sorted(set(before) | set(after))
    ]
    existing = {rep.team for rep in current if rep.team}
    result.new_teams = sorted({row.team for _, row in resolved.values() if row.team} - existing)
    result.emptied_teams = sorted(team for team in before if after.get(team, 0) == 0)

    in_file = set(resolved)
    by_code = {rep.code: rep for rep in current}
    result.missing = sorted(
        (
            MissingRep(code, by_code[code].name, by_code[code].team, lines)
            for code, lines in recent.items()
            if code in by_code and code not in in_file and lines > 0 and by_code[code].is_active
        ),
        key=lambda item: -item.lines,
    )

    # Coordinators: their team links only to a team of the rep sheet.
    file_teams = {row.team for _, row in resolved.values() if row.team}
    supervisor_names = {row.name for row in master.coordinators if row.name} | set(
        master.director_names
    )
    for row in master.coordinators:
        if row.team is None:
            result.coordinators.append(
                CoordinatorIssue(
                    row.document, row.name, None, "Sin EQUIPO en la hoja Coordinadores"
                )
            )
        elif teams is not None and row.team not in teams:
            result.rejected.append(
                RowProblem(
                    COORDINATORS_SHEET,
                    row.row,
                    row.name,
                    f"El equipo '{row.team}' no está en el maestro de equipos",
                )
            )
        elif row.team not in file_teams:
            previous = next(
                (item.team for item in coordinators if item.document == row.document), None
            )
            keeps = f"; conserva su equipo actual ({previous})" if previous else ""
            result.coordinators.append(
                CoordinatorIssue(
                    row.document,
                    row.name,
                    row.team,
                    "Su EQUIPO no es el de ningún vendedor del archivo: no se enlaza a sus "
                    f"vendedores{keeps}",
                )
            )
    missing_names: dict[str, list[int]] = defaultdict(list)
    for row in master.reps:
        for value in (row.coordinator, row.director):
            if value and value not in NO_PERSON and value not in supervisor_names:
                missing_names[value].append(row.row)
    for name, rows in sorted(missing_names.items()):
        result.warnings.append(
            RowProblem(
                REPS_SHEET,
                rows[0],
                name,
                "No está en las hojas Coordinadores ni Directores "
                f"(filas {', '.join(map(str, rows))})",
            )
        )
    return result


__all__ = [
    "DWH",
    "PLATFORM",
    "TeamEntry",
    "TeamOverride",
    "allowed_teams",
    "team_catalog",
    "COORDINATORS_SHEET",
    "DIRECTORS_SHEET",
    "FIELDS",
    "FIELD_LABELS",
    "REPS_SHEET",
    "CoordinatorIssue",
    "CoordinatorRow",
    "CurrentCoordinator",
    "CurrentRep",
    "FieldChange",
    "MasterFile",
    "MissingRep",
    "Preview",
    "RepRow",
    "RowProblem",
    "TeamCount",
    "document_variants",
    "normalize",
    "normalize_unit",
    "preview",
]
