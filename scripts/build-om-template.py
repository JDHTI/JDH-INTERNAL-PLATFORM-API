"""Turn Calidad's filled P.06.F.04 (Oportunidad de Mejora) into the docxtpl template.

    python scripts/build-om-template.py "P.06.F.04 Oportunidad de Mejora JDH V2.docx" \
        app/modules/pqrsf/assets/templates/om-p06-v02.docx

The sample is an OM already filled by hand. Every value becomes a ``{{ … }}`` mark, the
product row loops over ``products``, the photos over ``photos`` and the signature takes
whoever generates the OM. The header (code, version, date, logo) stays as Calidad left it;
the sample photos are dropped so the template weighs only the logo.

Paragraphs are found by their label, so a new version of the format with the same labels
builds the same way. Otherwise put the marks by hand in Word (the list is in
``docs/pqrsf.md``, section «Oportunidad de mejora»).
"""

from __future__ import annotations

import copy
import sys
import unicodedata
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Label the paragraph starts with -> mark that replaces its value.
_LABELLED = {
    "no. consecutivo": "{{ code }}",
    "ciudad": "{{ city }}",
    "direccion sede": "{{ address }}",
    "fecha de reporte": "{{ report_date }}",
    "descripcion medio recibo": "{{ channel }}",
    "orden de recogida": "{{ pickup_order }}",
    "documento no": "{{ document_number }}",
    "fecha documento": "{{ document_date }}",
    "clasificacion": "{{ classification }}",
    "novedad de la salida": "{{ novelty }}",
    "dependencia de la salida": "{{ dependency }}",
    "usuario": "{{ requester }}",
    "ejecutivo comercial": "{{ sales_executive }}",
    "acciones inmediatas": "{{ immediate_actions }}",
    "razon social del cliente": "{{ customer }}",
    "razon social del proveedor": "{{ supplier }}",
    "causa salida no conforme": "{{ cause }}",
}
_SIGNATURE = {
    "nombre:": "Nombre: {{ signer_name }}",
    "cargo:": "Cargo: {{ signer_position }}",
    "correo:": "Correo: {{ signer_email }}",
    "cel:": "Cel: {{ signer_phone }}",
}
_PRODUCT_FIELDS = ("p.sku", "p.description", "p.lot", "p.expiry", "p.quantity")


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


def _text(paragraph) -> str:
    return "".join(node.text or "" for node in paragraph.iter(qn("w:t")))


def _text_runs(paragraph) -> list:
    return [r for r in paragraph.findall(qn("w:r")) if r.find(qn("w:t")) is not None]


def _only(paragraph, mark: str) -> None:
    """The whole paragraph becomes the mark, with the format of its first run."""

    runs = _text_runs(paragraph)
    for extra in runs[1:]:
        paragraph.remove(extra)
    node = runs[0].find(qn("w:t"))
    node.text = mark
    node.set(_SPACE, "preserve")


def _labelled(paragraph, mark: str) -> None:
    """Keep «Label:» and put the mark in the value run (a new one if there is none)."""

    runs = _text_runs(paragraph)
    colon = next(i for i, r in enumerate(runs) if ":" in (r.find(qn("w:t")).text or ""))
    values = [r for r in runs[colon + 1 :] if (r.find(qn("w:t")).text or "").strip()]
    if values:
        target = values[0]
        for extra in values[1:]:
            paragraph.remove(extra)
        target.find(qn("w:t")).text = mark
    else:
        # A label with no value: the value takes the format of the label's last run, and the
        # blank runs after the colon go (one space separates label and value).
        for blank in runs[colon + 1 :]:
            paragraph.remove(blank)
        runs = runs[: colon + 1]
        label_node = runs[colon].find(qn("w:t"))
        label_node.text = label_node.text.rstrip() + " "
        label_node.set(_SPACE, "preserve")
        target = copy.deepcopy(runs[colon])
        bold = target.find(qn("w:rPr"))
        if bold is not None:
            for tag in ("w:b", "w:bCs"):
                found = bold.find(qn(tag))
                if found is not None:
                    bold.remove(found)
        target.find(qn("w:t")).text = mark
        runs[-1].addnext(target)
    target.find(qn("w:t")).set(_SPACE, "preserve")


def _product_rows(row) -> None:
    """The sample product row becomes ``{%tr for %}`` / its values / ``{%tr endfor %}``."""

    for cell, field in zip(row.findall(qn("w:tc")), _PRODUCT_FIELDS, strict=True):
        _only(cell.find(qn("w:p")), "{{ " + field + " }}")
    opening, closing = copy.deepcopy(row), copy.deepcopy(row)
    for copy_row, tag in ((opening, "{%tr for p in products %}"), (closing, "{%tr endfor %}")):
        for index, cell in enumerate(copy_row.findall(qn("w:tc"))):
            _only(cell.find(qn("w:p")), tag if index == 0 else "")
    row.addprevious(opening)
    row.addnext(closing)


def _photos(paragraph) -> None:
    """The sample pictures become a loop over the chosen evidence."""

    runs = paragraph.findall(qn("w:r"))
    model = copy.deepcopy(runs[0])
    for run in runs:
        paragraph.remove(run)
    for child in list(model):
        if child.tag != qn("w:rPr"):
            model.remove(child)
    for mark in ("{% for photo in photos %}", "{{ photo }}", " ", "{% endfor %}"):
        run = copy.deepcopy(model)
        node = run.makeelement(qn("w:t"), {})
        node.text = mark
        node.set(_SPACE, "preserve")
        run.append(node)
        paragraph.append(run)


def build(sample: Path, target: Path) -> None:
    document = Document(str(sample))
    body = document.element.body
    paragraphs = [
        p
        for p in body.iter(qn("w:p"))
        if _text(p).strip() or next(p.iter(qn("w:drawing")), None) is not None
    ]
    done: set[str] = set()

    description_at = photos_at = None
    for index, paragraph in enumerate(paragraphs):
        folded = _fold(_text(paragraph))
        for label, mark in _LABELLED.items():
            if label not in done and folded.startswith(label):
                _labelled(paragraph, mark)
                done.add(label)
                break
        else:
            for label, line in _SIGNATURE.items():
                if label not in done and folded.startswith(label):
                    _only(paragraph, line)
                    done.add(label)
                    break
        if folded.startswith("descripcion detallada"):
            description_at = index + 1
        elif folded.startswith("es conveniente"):
            _only(paragraph, "{{ request_note }}")
            done.add("note")
        elif folded.startswith("registro fotografico"):
            photos_at = index + 1

    missing = (set(_LABELLED) | set(_SIGNATURE) | {"note"}) - done
    if missing or description_at is None or photos_at is None:
        sys.exit(f"No encontré en la muestra: {sorted(missing)}")

    # The detailed description: the pasted email of the sample goes until the product table.
    first = paragraphs[description_at]
    _only(first, "{{ description }}")
    header_cell = next(p for p in paragraphs if _fold(_text(p)) == "codigo")
    for paragraph in paragraphs[description_at + 1 : paragraphs.index(header_cell)]:
        parent = paragraph.getparent()
        # A table cell must keep one paragraph; anything else of the sample email goes.
        if parent is not None and not (
            parent.tag == qn("w:tc") and len(parent.findall(qn("w:p"))) == 1
        ):
            parent.remove(paragraph)

    header_row = header_cell.getparent().getparent()
    _product_rows(header_row.getnext())

    photo_paragraph = next(
        p for p in paragraphs[photos_at:] if next(p.iter(qn("w:drawing")), None) is not None
    )
    _photos(photo_paragraph)

    # The blank paragraphs after the signature only push an empty last page.
    trailing = body[-1].getprevious() if body[-1].tag == qn("w:sectPr") else body[-1]
    while trailing is not None and trailing.tag == qn("w:p") and not _text(trailing).strip():
        previous = trailing.getprevious()
        if next(trailing.iter(qn("w:drawing")), None) is not None:
            break
        body.remove(trailing)
        trailing = previous

    # Drop the sample photos from the package: only the header logo stays.
    used = {
        value
        for element in body.iter()
        for key, value in element.attrib.items()
        if key.endswith("}embed") or key.endswith("}id")
    }
    part = document.part
    for rel_id, rel in list(part.rels.items()):
        if rel.reltype.endswith("/image") and rel_id not in used:
            part.drop_rel(rel_id)
    # Who edited the sample stays out of the template (and of every OM made from it).
    properties = document.core_properties
    properties.author = "Juan D. Hoyos Distribuciones"
    properties.last_modified_by = "Plataforma JDH"
    properties.comments = ""
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Plantilla escrita en {sys.argv[2]}")
