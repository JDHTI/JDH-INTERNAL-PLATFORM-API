import uuid

from app.modules.pqrsf.domain.email import (
    acknowledgment,
    clean_subject,
    find_case_code,
    fold,
    is_automatic,
    match_name,
    parse_request,
    strip_quoted_reply,
)

TEMPLATE_BODY = """Tipo: Queja
Sede: Medellín
Nombre: Ana Pérez
Razón social: Distribuciones XYZ S.A.S.
NIT: 900123456-7
Motivo: Cambio de factura
Descripción:
Me facturaron un producto que no pedí.
Necesito la nota crédito.
"""


def test_the_template_is_read_field_by_field() -> None:
    parsed = parse_request(TEMPLATE_BODY)

    assert parsed.request_type == "Queja"
    assert parsed.site == "Medellín"
    assert parsed.requester_name == "Ana Pérez"
    assert parsed.business_name == "Distribuciones XYZ S.A.S."
    assert parsed.nit == "900123456-7"
    assert parsed.subject == "Cambio de factura"
    assert parsed.description == "Me facturaron un producto que no pedí.\nNecesito la nota crédito."
    assert not parsed.free_text


def test_keys_ignore_case_accents_and_aliases() -> None:
    parsed = parse_request("TIPO DE SOLICITUD: reclamo\nrazon social: ACME\nDETALLE: texto")

    assert parsed.request_type == "reclamo"
    assert parsed.business_name == "ACME"
    assert parsed.description == "texto"


def test_the_description_keeps_lines_that_look_like_fields() -> None:
    parsed = parse_request("Tipo: Queja\nDescripción:\nNIT: 123 estaba mal en la factura")

    assert parsed.nit is None
    assert parsed.description == "NIT: 123 estaba mal en la factura"


def test_without_a_description_key_the_loose_text_is_the_description() -> None:
    parsed = parse_request("Tipo: Petición\nHola, necesito un certificado.\nGracias")

    assert parsed.request_type == "Petición"
    assert parsed.description == "Hola, necesito un certificado.\nGracias"


def test_free_text_is_flagged_and_kept() -> None:
    parsed = parse_request("Buenas tardes, quiero reportar un problema con mi pedido.")

    assert parsed.free_text
    assert parsed.description == "Buenas tardes, quiero reportar un problema con mi pedido."


def test_quoted_history_is_cut_from_replies() -> None:
    body = (
        "Adjunto el soporte.\n\n"
        "El lun, 5 oct 2026 a las 9:00, PQRSF <pqrsf@jdh.com.co> escribió:\n"
        "> Hemos recibido tu solicitud\n"
    )

    assert strip_quoted_reply(body) == "Adjunto el soporte."
    assert strip_quoted_reply("ok\n> citado\nfin") == "ok\nfin"
    assert strip_quoted_reply("Gracias\n\nFrom: ana@x.com\nSent: today") == "Gracias"


def test_case_codes_are_found_in_subjects() -> None:
    assert find_case_code("RE: Su solicitud P26AC0012 recibida") == "P26AC0012"
    assert find_case_code("[PQRSF] q26gn0003 seguimiento") == "Q26GN0003"
    assert find_case_code("Factura 12345") is None


def test_subject_prefixes_and_tags_are_dropped() -> None:
    assert clean_subject("RE: [PQRSF] Cambio de factura") == "Cambio de factura"
    assert clean_subject("Cambio de factura") == "Cambio de factura"


def test_names_match_without_case_or_accents() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    options = [("Medellín", first), ("MD", first), ("Bogotá", second)]

    assert match_name("medellin", options) == first
    assert match_name(" BOGOTA ", options) == second
    assert match_name("Cali", options) is None
    assert match_name(None, options) is None
    assert fold("  Razón   Social ") == "razon social"


def test_automatic_mail_never_opens_a_case() -> None:
    own = ["pqrsf@jdh.com.co", "info@jdh.com.co"]

    assert is_automatic({}, "ana@x.com", own) is None
    assert is_automatic({}, "INFO@jdh.com.co", own)
    assert is_automatic({}, "mailer-daemon@x.com", own)
    assert is_automatic({}, "no-reply@x.com", own)
    assert is_automatic({"auto-submitted": "auto-replied"}, "ana@x.com", own)
    assert is_automatic({"auto-submitted": "no"}, "ana@x.com", own) is None
    assert is_automatic({"precedence": "bulk"}, "ana@x.com", own)
    assert is_automatic({"x-auto-response-suppress": "All"}, "ana@x.com", own)
    assert is_automatic({}, "", own)


def test_the_acknowledgment_carries_the_code_and_the_template_only_when_needed() -> None:
    plain = acknowledgment("P26AC0012", needs_review=False)
    flagged = acknowledgment("P26AC0012", needs_review=True)

    assert "P26AC0012" in plain and "Tipo:" not in plain
    assert "P26AC0012" in flagged and "Tipo:" in flagged
