"""Offline Russian→English query translation for CLIP search."""

import logging
import re
import threading

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

# Словарь: русские названия категорий (из categories.ts) + частые фото-запросы
_RU_DICT: dict[str, str] = {
    # Категории (label → key)
    "портреты": "portrait", "портрет": "portrait",
    "селфи": "selfie",
    "групповые": "group photo", "групповое фото": "group photo",
    "природа": "nature",
    "архитектура": "architecture",
    "памятники": "monument", "памятник": "monument",
    "музеи": "museum", "музей": "museum",
    "еда": "food",
    "животные": "animals", "животное": "animal",
    "транспорт": "transport",
    "интерьеры": "interior", "интерьер": "interior",
    "спорт": "sports",
    "мероприятия": "event", "мероприятие": "event",
    "книги": "book", "книга": "book",
    "скриншоты": "screenshot", "скриншот": "screenshot",
    "чеки": "receipt", "чек": "receipt",
    "документы": "document", "документ": "document",
    "каршеринг": "carsharing",
    "мемы": "meme", "мем": "meme",
    "фото экрана": "screen photo",
    "справочные": "reference",
    "qr": "qr_code", "штрихкод": "qr_code",
    # Частые запросы
    "кот": "cat", "кошка": "cat", "котик": "cat",
    "собака": "dog", "пёс": "dog",
    "пляж": "beach", "море": "sea", "океан": "ocean",
    "горы": "mountains", "гора": "mountain",
    "закат": "sunset", "рассвет": "sunrise",
    "цветы": "flowers", "цветок": "flower",
    "лес": "forest", "дерево": "tree",
    "небо": "sky", "облака": "clouds",
    "снег": "snow", "зима": "winter", "лето": "summer",
    "дети": "children", "ребёнок": "child", "ребенок": "child",
    "свадьба": "wedding", "машина": "car",
    "люди": "people", "человек": "person",
    "город": "city", "улица": "street",
    "парк": "park", "река": "river", "озеро": "lake",
}

_translator = None
_translator_lock = threading.Lock()
_translator_failed = False


def has_cyrillic(text: str) -> bool:
    """Check if text contains Cyrillic characters."""
    return bool(_CYRILLIC_RE.search(text))


def _get_translator():
    """Lazy-load argos-translate ru→en from locally installed packages (no network).

    Network-based download should be done via ensure_downloaded() during pipeline startup.
    """
    global _translator, _translator_failed
    if _translator is not None or _translator_failed:
        return _translator
    with _translator_lock:
        if _translator is not None or _translator_failed:
            return _translator
        try:
            import argostranslate.package
            import argostranslate.translate

            installed = argostranslate.package.get_installed_packages()
            if not any(p.from_code == "ru" and p.to_code == "en" for p in installed):
                logger.info("argos-translate ru→en not installed; skipping")
                _translator_failed = True
                return None
            _translator = argostranslate.translate
            return _translator
        except Exception:
            logger.warning("Failed to initialize argos-translate", exc_info=True)
            _translator_failed = True
            return None


def is_installed() -> bool:
    """Check if argos-translate ru→en is installed (no network)."""
    try:
        import argostranslate.package
        return any(p.from_code == "ru" and p.to_code == "en"
                   for p in argostranslate.package.get_installed_packages())
    except Exception:
        return False


def ensure_downloaded() -> None:
    """Download+install argos-translate ru→en if not installed. Safe to call from background thread."""
    global _translator, _translator_failed
    with _translator_lock:
        if _translator is not None:
            return
        try:
            import argostranslate.package
            import argostranslate.translate

            installed = argostranslate.package.get_installed_packages()
            if not any(p.from_code == "ru" and p.to_code == "en" for p in installed):
                argostranslate.package.update_package_index()
                available = argostranslate.package.get_available_packages()
                ru_en = next(
                    (p for p in available if p.from_code == "ru" and p.to_code == "en"),
                    None,
                )
                if ru_en:
                    logger.info("Installing argos-translate ru→en model...")
                    ru_en.install()
                    logger.info("argos-translate ru→en model installed")
            _translator = argostranslate.translate
            _translator_failed = False
        except Exception:
            logger.warning("Failed to download argos-translate", exc_info=True)


def translate_query(query: str) -> str | None:
    """Translate Russian query to English. Returns None if no translation needed/possible."""
    if not has_cyrillic(query):
        return None
    lower = query.lower().strip()
    if lower in _RU_DICT:
        return _RU_DICT[lower]
    translator = _get_translator()
    if translator is None:
        return None
    try:
        result = translator.translate(query, "ru", "en")
        return result.strip() if result else None
    except Exception:
        logger.warning("argos-translate failed for query: %s", query, exc_info=True)
        return None
