"""The person classifier (vat/person_classifier.py): who is asked, the answer check, the rule."""

from vat import person_classifier as pc
from vat.costs import NAME2, call_kind


def v(cls="PERSON", name="Jens Hohmann", conf=0.9):
    return pc.verdict_of({"class": cls, "person_name": name, "confidence": conf, "reason": "x"})


def test_skip_legal_form_and_university():
    assert pc.skip(["Hohmann GmbH", None]) == pc.LEGAL_FORM
    assert pc.skip(["Printmonks", "Lukas Ruppel e.K."]) == pc.LEGAL_FORM
    assert pc.skip(["Technische Universität Dresden", "Inst. Chemie"]) == pc.UNIVERSITY
    assert pc.skip(["University Heidelberg", "Apotheke"]) == pc.UNIVERSITY
    assert pc.skip(["Helmut Geppert", "BTU CottbusSenftenberg"]) == pc.UNIVERSITY
    assert pc.skip(["Stefan Kubik", "TU Kaiserslautern FB Chemie"]) == pc.UNIVERSITY
    assert pc.skip(["Hochschule Fulda", None]) == pc.UNIVERSITY
    assert pc.skip(["Thomas Maier", None]) is None
    assert pc.skip(["Th. Bauer Industrievertretung", None]) is None
    assert pc.skip(["Dipl.-Inform. (FH) Daniel Miericke", "Consulting"]) is None
    assert pc.skip(["FH Aachen", "FB Chemie"]) == pc.UNIVERSITY
    assert pc.skip(["Li Wei", None, None, None]) is None
    assert pc.skip(["Jens Hohmann", None]) is None


def test_prompt_has_the_record():
    system, user = pc.prompt(["Li Wei", None, "c/o Labor", None], "BERLIN")
    assert "Chinese" in system
    assert user.endswith("Record: Li Wei | c/o Labor | BERLIN\nAnswer:")


def test_verdict_checked():
    assert v()["class"] == pc.PERSON
    assert v(conf=3)["confidence"] == 1.0
    assert pc.verdict_of({"class": "COMPANY", "person_name": None, "confidence": 1}) is None
    assert pc.verdict_of({"class": "PERSON", "person_name": None, "confidence": 1}) is None
    assert pc.verdict_of({"class": "PERSON", "person_name": "A B", "confidence": "high"}) is None
    assert v(pc.ORGANISATION, None)["person_name"] is None


def test_is_person_needs_confidence():
    assert pc.is_person(v())
    assert pc.is_person(v(pc.PERSON_BUSINESS))
    assert not pc.is_person(v(conf=0.7))
    assert not pc.is_person(v(pc.ORGANISATION, None))
    assert not pc.is_person(None)


def test_names_person():
    assert pc.names_person("Nord-Apotheke Inhaber/-in: Dennis Saul", "Dennis Saul")
    assert pc.names_person("Josef Zürn", "Josef Zürn")
    assert pc.names_person("Stefan Schurtz IT-Sicherheit", "Stefan Schurtz")
    assert pc.names_person("Dr. Uwe Nabielek", "Dr. med. Uwe Nabielek")
    assert not pc.names_person("Hohmann GmbH", "Jens Hohmann")
    assert not pc.names_person("Werner GmbH", "Werner Kopp")
    assert not pc.names_person(None, "Jens Hohmann")


def test_unnamed_person_rule():
    assert pc.unnamed_person(v(), "Hohmann GmbH")
    assert not pc.unnamed_person(v(name="Josef Zürn"), "Josef Zürn")
    assert not pc.unnamed_person(v(conf=0.6), "Hohmann GmbH")
    assert not pc.unnamed_person(v(pc.ORGANISATION, None), "Marien-Apotheke")
    # A person's business is only labelled, never withdrawn.
    assert not pc.unnamed_person(v(pc.PERSON_BUSINESS, "Dietmar Nagel"), "Laserprint Nagel GmbH & Co. KG")


def test_customer_type():
    assert pc.customer_type(pc.LEGAL_FORM, None) == "ORGANISATION_LEGAL_FORM"
    assert pc.customer_type(pc.UNIVERSITY, None) == "ORGANISATION_UNIVERSITY"
    assert pc.customer_type(None, None) == "NOT_CLASSIFIED"
    assert pc.customer_type(None, v(conf=0.6)) == "UNSURE"
    assert pc.customer_type(None, v(pc.PERSON_BUSINESS)) == pc.PERSON_BUSINESS


def test_costed_as_helper_classifier():
    assert call_kind(pc.LABEL) == NAME2
