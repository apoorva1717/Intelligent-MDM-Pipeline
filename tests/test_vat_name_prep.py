"""Name preparation for the missing-VAT lookup (Step AD part 1; vat/name_prep.py).

Every case is a "Test:" line of the Step AD specification or a row of
data/VAT/DE_VAT_TEST_AD_50.xlsx.
"""

from __future__ import annotations

import pytest

from vat.name_prep import (
    BRANCH,
    CONTINUATION,
    DEPARTMENT,
    OTHER,
    SUB_ENTITY,
    UNKNOWN_GMBH_FAMILY,
    clean,
    forms_differ,
    is_generic,
    is_public_body,
    legal_form_of,
    prepare,
)


class TestClean:
    def test_care_of_is_removed(self):
        names, contact = clean(("VAMED VSB-Medizintechnik Mitte GmbH", "c/o Helios Klinikum Meiningen /",
                                "Medizintechnik", None))
        assert names[1] is None and contact == ("c/o Helios Klinikum Meiningen",)
        plan = prepare(("VAMED VSB-Medizintechnik Mitte GmbH", "c/o Helios Klinikum Meiningen /",
                        "Medizintechnik", None))
        assert plan.cls != SUB_ENTITY and plan.entity == "VAMED VSB-Medizintechnik Mitte GmbH"

    def test_contact_person_is_removed(self):
        names, contact = clean(("AixMinerals", "Nicolai Thüns", None, None))
        assert names[1] is None and contact == ("Nicolai Thüns",)

    @pytest.mark.parametrize("name_2", ["z. Hd. Herrn Meier", "Attn: Purchasing", ">> 1256685", "12345"])
    def test_notes_and_attention_lines(self, name_2):
        assert clean(("Muster GmbH", name_2, None, None))[0][1] is None

    @pytest.mark.parametrize("name_2", ["EK0000041993", "0000040242", ">> LIEFERUNG IMMER an 309395",
                                        "1087319 nehmen", "LN 10155737", "P.O. BOX 30 02 43",
                                        "Inhj REQ0001521937P-BMS", "Geb.131/0 110"])
    def test_a_long_number_makes_name_2_a_note(self, name_2):
        assert clean(("Muster GmbH", name_2, None, None))[0][1] is None

    @pytest.mark.parametrize("name_2", ["Abt 4651/2", "SG 37/410", "Eintracht 1898", "RAUM: 01-104"])
    def test_four_or_five_digits_stay(self, name_2):
        assert clean(("Muster GmbH", name_2, None, None))[0][1] == name_2

    def test_a_long_number_is_cut_from_name_1(self):
        assert clean(("#INTERTEK DEUTSCHLAND (USE 315791)", None, None, None))[0][0] == "#INTERTEK DEUTSCHLAND"
        assert clean(("Aktiv Sensor GmbH EK0000042299", None, None, None))[0][0] == "Aktiv Sensor GmbH"
        assert clean(("Kreis 2000 GmbH", None, None, None))[0][0] == "Kreis 2000 GmbH"
        assert clean(("EK0000042299", None, None, None))[0][0] == "EK0000042299"  # nothing else left

    def test_the_code_never_reaches_the_search_name(self):
        plan = prepare(("MOCAP Ltd. Zweigniederlassung Deuts", "EK0000041993", None, None))
        assert "EK0000041993" not in plan.search_name
        plan = prepare(("Nordantec GmbH Norddeutsche Analyti", "EK0000040678", None, None))
        assert "EK" not in plan.search_name and plan.search_name.startswith("Nordantec GmbH")

    def test_owner_with_legal_form_is_kept(self):
        assert clean(("Reh-Apotheke am Barbarossaplatz", "Dirk Voss e.K.", None, None))[0][1] == "Dirk Voss e.K."


class TestLegalForm:
    @pytest.mark.parametrize("text, form", [
        ("Muster Aktiengesellschaft", "ag"),
        ("Muster Gesellschaft mit beschränkter Haftung", "gmbh"),
        ("Muster gemeinnützige GmbH", "ggmbh"),
        ("Muster GmbH + Co. KG", "gmbh & co kg"), ("Muster GmbH & Co KG", "gmbh & co kg"),
        ("Muster Gmbh and Co Kg", "gmbh & co kg"),
        ("Muster e. V.", "ev"), ("Muster e.V.", "ev"), ("Muster eV", "ev"),
        ("Muster SE & Co. KGaA", "se & co kgaa"), ("Muster KG aA", "kgaa"),
        ("Held + Team Design Partnerschaftsgesellschaft", "partg"), ("Muster PartGmbB", "partgmbb"),
        ("Dirk Voss e.K.", "ek"), ("Muster OHG", "ohg"), ("Muster GbR", "gbr"), ("Muster UG", "ug"),
        ("Muster eG", "eg"), ("Papiertechnische Stiftung", "stiftung"),
        ("Universitätsmedizin Göttingen Stiftung öffentlichen Rechts", "stiftung"),
        ("Muster AöR", "adoer"), ("Muster KdöR", "kdoer"),
        ("Sony Europe B.V.", "bv"), ("Muster N.V.", "nv"), ("Muster Ltd", "ltd"), ("Muster Inc", "inc"),
        ("Muster S.A.", "sa"), ("Muster S.r.l.", "srl"), ("Muster AB", "ab"),
        ("AG der Dillinger Hüttenwerke", "ag"), ("GmbH Region Leipzig", "gmbh"),
        ("Keysight Technologies Deutschland Gm", "gmbh"), ("Keysight Technologies Deutschland Gmb", "gmbh"),
        ("NFG Nehlsen Flugzeug-Galvanik Gmbh&", UNKNOWN_GMBH_FAMILY),
        ("Muster Maschinenbau GmbH & Co", UNKNOWN_GMBH_FAMILY), ("Muster Maschinenbau GmbH & C", UNKNOWN_GMBH_FAMILY),
        ("Muster Maschinenbau Aktiengesell A", None),
    ])
    def test_forms(self, text, form):
        assert legal_form_of(text, sap=True) == form

    def test_stiftung_only_as_a_word(self):
        assert legal_form_of("NAT Max Schaldach Stiftungsprofessur") is None

    def test_unknown_family_never_differs_from_a_gmbh_form(self):
        assert not forms_differ(UNKNOWN_GMBH_FAMILY, "gmbh & co kg") and not forms_differ(None, "ag")
        assert forms_differ("gmbh & co kg", "gmbh")

    def test_form_from_name_2(self):
        assert prepare(("Held + Team Design", "Partnerschaftsgesellschaft", None, None)).form == "partg"


class TestClassification:
    @pytest.mark.parametrize("names, cls", [
        (("Technische Universität München", "Klinikum rechts der Isar"), SUB_ENTITY),
        (("Universität Heidelberg", "Universitätsklinikum"), SUB_ENTITY),
        (("Charité - Universitätsmedizin Berlin", "Klinik für Dermatologie"), DEPARTMENT),
        (("Ludwig-Maximilians-Universität", "Klinik für Vögel, Kleinsäuger"), DEPARTMENT),
        (("FAU Erlangen-Nürnberg", "NAT Max Schaldach Stiftungsprofessur"), DEPARTMENT),
        (("FAU Erlangen-Nürnberg", "NAT Max Schaldach Stiftungs", "Prof für Biomedizinische Technik"), DEPARTMENT),
        (("Universität zu Lübeck", "Zentr. Chemikalienlager-Vorklinik"), DEPARTMENT),
        (("Universität zu Lübeck", "Zentr. Chemikalienlager-Vorklinikum"), DEPARTMENT),
        (("Albert-Ludwigs-Universität Freiburg", "Dez 4 Bau und Infrastruktur"), DEPARTMENT),
        (("Johannes Gutenberg-Universität Mainz", "FB 08 Inst für Physik"), DEPARTMENT),
        (("Charité CFM Facility Management", "Gmbh"), CONTINUATION),
        (("CiS Forschungsinstitut für", "Mikrosensorik GmbH"), CONTINUATION),
        (("Kronen-Apotheke Marxen", "Marxen-Ruberg OHG"), CONTINUATION),
        (("Tyco Safety Products", "Research & Development"), DEPARTMENT),
        (("Sony Europe B.V.", "Stuttgart Technology Center"), DEPARTMENT),
        (("Infraserv GmbH & Co. Höchst KG", "Werkfeuerwehr"), DEPARTMENT),
        (("Universitätsmedizin Göttingen", "Stiftung öffentlichen Rechts"), CONTINUATION),
        (("BAK Boysen Abgaskomponenten GmbH", "& Co. KG"), CONTINUATION),
        (("ThyssenKrupp Industrial Solutions", "AG Business Unit Process", "Technologies"), CONTINUATION),
        (("Technische Universität Braunschweig", "Institut für Anorganische und", "Analytische Chemie"),
         DEPARTMENT),
        (("Diakonie Klinikum Kirchen GmbH", "Einkauf"), DEPARTMENT),
        (("SGS Germany GmbH", "Accounting"), DEPARTMENT),
        (("Technische Universität Dresden", "MN Chemie Prof Bioanalytische", "Chemie"), DEPARTMENT),
        (("OWIS GmbH", "Feinmechanische und", "Optische Systemtechnik"), OTHER),
        (("European Neuroscience Institute", "Göttingen"), OTHER),
        (("Hochschule des Landes BaWü", "Staatliche Akademie der bildenden"), OTHER),
    ])
    def test_class(self, names, cls):
        assert prepare(names).cls == cls

    def test_sub_entity_is_searched_with_the_parent_token(self):
        plan = prepare(("Technische Universität München", "Klinikum rechts der Isar"))
        assert plan.entity == "Klinikum rechts der Isar" and plan.acceptable == ("Klinikum rechts der Isar",)
        assert "Klinikum rechts der Isar" in plan.search_name and plan.parent == "Technische Universität München"

    def test_continuation_accepts_both_names(self):
        plan = prepare(("Reh-Apotheke am Barbarossaplatz", "Dirk Voss e.K."))
        assert plan.cls == CONTINUATION
        assert plan.acceptable == ("Reh-Apotheke am Barbarossaplatz Dirk Voss e.K.", "Reh-Apotheke am Barbarossaplatz")
        assert prepare(("CiS Forschungsinstitut für", "Mikrosensorik GmbH")).entity == \
            "CiS Forschungsinstitut für Mikrosensorik GmbH"
        assert prepare(("ThyssenKrupp Industrial Solutions", "AG Business Unit Process")).entity == \
            "ThyssenKrupp Industrial Solutions AG"
        assert prepare(("BAK Boysen Abgaskomponenten GmbH", "& Co. KG")).form == "gmbh & co kg"

    def test_no_join_on_a_cut_name(self):
        plan = prepare(("Johannes Gutenberg-Universität Mainz", "FB 08 Inst für Physik"))
        assert plan.entity == "Johannes Gutenberg-Universität Mainz"
        assert prepare(("OWIS GmbH", "Feinmechanische und")).entity == "OWIS GmbH"

    def test_reverse_order_takes_the_parent(self):
        plan = prepare(("Institut f. Werkstoffwissenschaften", "Martin-Luther-Universität"))
        assert (plan.cls, plan.entity, plan.reverse) == (DEPARTMENT, "Martin-Luther-Universität", True)

    def test_department_inside_name_1(self):
        plan = prepare(("Universität Stuttgart Institut für Werkstoffkunde",))
        assert (plan.cls, plan.entity) == (DEPARTMENT, "Universität Stuttgart")


class TestBranchGenericPublic:
    def test_branch(self):
        plan = prepare(("BMW Group Werk Landshut",))
        assert plan.entity == "BMW Group" and BRANCH in plan.issues
        assert prepare(("Grillo-Werke AG",)).entity == "Grillo-Werke AG"

    @pytest.mark.parametrize("name", ["UOP GmbH", "VWR International GmbH", "CMO-SYS GmbH",
                                      "Hoffmann Engineering Services GmbH", "Lang GmbH"])
    def test_generic(self, name):
        assert is_generic(name)

    @pytest.mark.parametrize("name", ["Alltech Coppens GmbH", "Papiertechnische Stiftung"])
    def test_not_generic(self, name):
        assert not is_generic(name)

    @pytest.mark.parametrize("name, public", [
        ("Landeskriminalamt Rheinland-Pfalz", True), ("Bundeskriminalamt Berlin", True),
        ("Bundespolizeiabteilung Blumberg", True), ("Europäische Kommission", True),
        ("Landratsamt Calw", True), ("Grundschule am Park", True),
        ("Hochschule Karlsruhe", False), ("Stadtwerke Stuttgart GmbH", False), ("Robert Bosch GmbH", False),
    ])
    def test_public(self, name, public):
        assert is_public_body(name) is public
