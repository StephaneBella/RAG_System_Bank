import pytest

from app.services.document_service import parse_stored_name


@pytest.mark.parametrize(
    ("file_name", "visibility", "department"),
    [
        ("Prive__Credit_Risques.pdf", "private", "credit_&_risques"),
        ("PRIVE__DIRECTION_CONFORMITE.PDF", "private", "direction_conformite"),
        ("Privé__Direction_Generale_Strategie.pdf", "private", "direction_generale"),
        ("Public__Front_Office_Gestionnaires_Comptes.pdf", "public", "front_office"),
        (
            "private__credit_&_risques__Risque_en_Entreprise.pdf",
            "private",
            "credit_&_risques",
        ),
        (
            "PUBLIC__DIRECTION_CONFORMITE__Reglement_COBAC.pdf",
            "public",
            "direction_conformite",
        ),
    ],
)
def test_parse_stored_name_accepts_legacy_and_canonical_case_variants(
    file_name, visibility, department
):
    parsed = parse_stored_name(file_name)

    assert parsed is not None
    assert parsed.visibility == visibility
    assert parsed.department_token == department


@pytest.mark.parametrize(
    "file_name",
    [
        "secret.pdf",
        "internal__Credit_Risques.pdf",
        "private__report.pdf",
    ],
)
def test_parse_stored_name_rejects_unclassifiable_names(file_name):
    assert parse_stored_name(file_name) is None
