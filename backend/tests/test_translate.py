"""Tests for photogal.translate — offline ru→en query translation."""

from photogal.translate import has_cyrillic, translate_query


class TestHasCyrillic:
    def test_cyrillic_text(self):
        assert has_cyrillic("кот") is True

    def test_latin_text(self):
        assert has_cyrillic("cat") is False

    def test_mixed_text(self):
        assert has_cyrillic("hello мир") is True

    def test_empty_string(self):
        assert has_cyrillic("") is False

    def test_numbers_only(self):
        assert has_cyrillic("12345") is False


class TestTranslateQuery:
    def test_english_returns_none(self):
        assert translate_query("cat") is None

    def test_dict_exact_match(self):
        assert translate_query("портрет") == "portrait"

    def test_dict_case_insensitive(self):
        assert translate_query("Кот") == "cat"

    def test_dict_with_whitespace(self):
        assert translate_query("  портрет  ") == "portrait"

    def test_dict_category_portrait(self):
        assert translate_query("портреты") == "portrait"

    def test_dict_category_food(self):
        assert translate_query("еда") == "food"

    def test_dict_category_screenshot(self):
        assert translate_query("скриншот") == "screenshot"

    def test_dict_common_word_dog(self):
        assert translate_query("собака") == "dog"

    def test_dict_common_word_sunset(self):
        assert translate_query("закат") == "sunset"

    def test_empty_string(self):
        assert translate_query("") is None

    def test_category_shortcut_integration(self):
        """'портрет' should translate to 'portrait' which matches _CATEGORIES."""
        from photogal.pipeline.analyzer import _CATEGORIES
        translated = translate_query("портрет")
        assert translated == "portrait"
        assert translated in _CATEGORIES
