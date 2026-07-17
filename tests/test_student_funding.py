from mne_grade_manager.services.student_funding import (
    encode_funding_codes,
    format_funding_display,
    parse_funding_codes,
)


def test_parse_funding_codes_aliases():
    codes = parse_funding_codes("Campus France, EIFFEL, exemption frais")
    assert codes == {"campus_france", "eiffel", "tuition_exemption"}


def test_encode_funding_codes_order():
    assert encode_funding_codes({"eiffel", "campus_france"}) == "campus_france,eiffel"


def test_format_funding_display_with_other():
    text = format_funding_display("idex", "Bourse régionale")
    assert text == "IDEX, Bourse régionale"
