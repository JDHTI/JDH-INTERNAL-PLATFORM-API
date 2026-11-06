from app.modules.mail.layout import (
    LOGO_CONTENT_ID,
    BrandedEmail,
    logo_file,
    render_fragment,
    render_html,
    render_text,
)

EMAIL = BrandedEmail(
    title="Radicamos tu queja",
    greeting="Hola Ana,",
    paragraphs=("Recibimos tu queja.\nSegunda línea", "Otro <párrafo> & más"),
    facts=(("Radicado", "Q26MD0012"), ("Tipo de solicitud", "Queja")),
    closing=("Te daremos pronta respuesta.",),
    footnote="Recibiste este correo porque radicaste una solicitud.",
)


def test_the_content_survives_the_queue_as_json() -> None:
    assert BrandedEmail.from_payload(EMAIL.to_payload()) == EMAIL


def test_the_html_has_the_logo_the_title_and_the_radicado_highlighted() -> None:
    page = render_html(EMAIL)

    assert page.startswith("<!DOCTYPE html>") and 'lang="es"' in page
    assert f'src="cid:{LOGO_CONTENT_ID}"' in page
    assert ">Radicamos tu queja</h1>" in page
    # The radicado is the first fact: the brand blue, bigger than the rest.
    assert "font-size:18px;line-height:24px;font-weight:700;color:#16304E;" in page
    assert ">Q26MD0012</p>" in page
    assert "Recibimos tu queja.<br>Segunda línea" in page


def test_what_the_team_writes_is_escaped() -> None:
    page = render_html(EMAIL)

    assert "Otro &lt;párrafo&gt; &amp; más" in page and "<párrafo>" not in page


def test_the_fragment_has_no_document_around_it() -> None:
    fragment = render_fragment(EMAIL)

    assert fragment.startswith("<table") and "<html" not in fragment and "<body" not in fragment


def test_the_text_version_says_the_same() -> None:
    text = render_text(EMAIL)

    assert text.startswith("Hola Ana,\n\nRecibimos tu queja.")
    assert "Radicado: Q26MD0012\nTipo de solicitud: Queja" in text
    assert "PQRSF JDH" in text


def test_the_logo_ships_with_the_code() -> None:
    logo = logo_file()

    assert logo.content_id == LOGO_CONTENT_ID and logo.content_type == "image/png"
    assert logo.content.startswith(b"\x89PNG") and len(logo.content) < 50_000
