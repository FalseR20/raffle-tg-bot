from bot.utils.csv import safe_csv_value, safe_excel_value


def test_formula_like_values_are_prefixed():
    for value in ("=2+2", "+1", "-1", "@SUM(A1)"):
        assert safe_csv_value(value) == "'" + value


def test_normal_values_are_not_changed():
    assert safe_csv_value("Иван") == "Иван"
    assert safe_csv_value(10) == 10
    assert safe_csv_value(None) is None


def test_excel_formula_values_are_prefixed():
    assert safe_excel_value("=2+2") == "'=2+2"
    assert safe_excel_value("+2") == "+2"
    assert safe_excel_value(10) == 10
