"""El sobre {codigo, detalle[, campos]} de `datos_invalidos`, desde cada ValidationError."""

from apps.core.exceptions import DETALLE_DE_CAMPOS, sobre_de_datos_invalidos


def test_a_list_of_errors_becomes_one_readable_detail_without_fields():
    sobre = sobre_de_datos_invalidos(["Primero.", "Segundo."])

    assert sobre == {"codigo": "datos_invalidos", "detalle": "Primero. Segundo."}


def test_field_errors_go_to_campos_and_the_detail_is_generic():
    sobre = sobre_de_datos_invalidos({"correo": ["Mal."], "rol": ["Otro.", "Y otro."]})

    assert sobre == {
        "codigo": "datos_invalidos",
        "detalle": DETALLE_DE_CAMPOS,
        "campos": {"correo": ["Mal."], "rol": ["Otro.", "Y otro."]},
    }


def test_errors_without_a_field_become_the_detail_and_do_not_pollute_campos():
    sobre = sobre_de_datos_invalidos({"non_field_errors": ["Fechas cruzadas."], "hasta": ["Mal."]})

    assert sobre["detalle"] == "Fechas cruzadas."
    assert sobre["campos"] == {"hasta": ["Mal."]}
    assert "non_field_errors" not in sobre["campos"]


def test_only_general_errors_have_no_campos_key():
    sobre = sobre_de_datos_invalidos({"non_field_errors": ["Solo esto."]})

    assert sobre == {"codigo": "datos_invalidos", "detalle": "Solo esto."}


def test_nested_errors_are_flattened_into_the_messages_of_their_field():
    sobre = sobre_de_datos_invalidos({"direccion": {"calle": ["Falta."], "cp": ["Mal."]}})

    assert sobre["campos"] == {"direccion": ["Falta.", "Mal."]}
