"""Deterministic article-link policy for the Content Strategy pipeline.

This module deliberately does not invent URLs or translated copy. It selects
only from real pages persisted by ``run_content_audit`` and carries an explicit
language fallback order to Article Writer. That makes links a pipeline contract,
not an optional writing suggestion.
"""
from __future__ import annotations

from urllib.parse import urlparse

_ACTION_TERMS = {
    "ru": ("контакт", "связ", "консультац", "заявк", "заказ", "подобрать", "расчет"),
    "ro": ("contact", "consult", "cerere", "oferta", "solicit", "alege"),
    "en": ("contact", "consult", "quote", "request", "book", "order"),
}


def language_priority(target_language: str, site_languages: list[str]) -> list[str]:
    """Return target language first, then the site's declared language order.

    This is intentionally deterministic: for a Russian article on a ``[ru,
    ro]`` site it returns ``[ru, ro]``; for Romanian it returns ``[ro, ru]``.
    Duplicate/empty values are removed and an unknown target still stays first.
    """
    target = target_language.strip().lower()
    ordered = [target] + [str(lang).strip().lower() for lang in site_languages]
    result: list[str] = []
    for lang in ordered:
        if lang and lang not in result:
            result.append(lang)
    return result or ["en"]


def _page_language_matches(page_language: str, requested: str) -> bool:
    page_lang = (page_language or "").lower()
    if page_lang == requested:
        return True
    # Existing audit can reliably distinguish Cyrillic but only knows
    # "latin" for a non-Cyrillic page. It is an allowed fallback match for
    # a requested Latin-script language, never for Russian.
    return page_lang == "latin" and requested != "ru"


def resolve_key_action_page(
    pages: list[dict], target_language: str, site_languages: list[str],
) -> tuple[dict | None, list[str]]:
    """Choose the highest-ranked real action page and report language order.

    Ranking only uses real title/slug/content strings collected during audit:
    action intent words outrank a generic page; then language priority; then a
    stable URL tie-break. ``None`` means the audit found no suitable page and
    is deliberately surfaced as a pipeline block rather than fabricating a
    contact URL.
    """
    priorities = language_priority(target_language, site_languages)
    ranked: list[tuple[int, int, str, dict]] = []
    for page in pages:
        url = str(page.get("link", "")).strip()
        if not url:
            continue
        haystack = " ".join(str(page.get(k, "")) for k in ("title", "slug", "content")).lower()
        lang_index = next(
            (index for index, lang in enumerate(priorities)
             if _page_language_matches(str(page.get("lang", "")), lang)),
            None,
        )
        if lang_index is None:
            continue
        action_score = sum(term in haystack for terms in _ACTION_TERMS.values() for term in terms)
        if not action_score:
            continue
        ranked.append((-action_score, lang_index, url, page))
    if not ranked:
        return None, priorities
    ranked.sort(key=lambda item: item[:3])
    return ranked[0][3], priorities


def resolve_external_source(
    sources_i18n: dict[str, list[str]], target_language: str, site_languages: list[str],
) -> tuple[str, str, list[str]]:
    """Select one configured, verified external source by language priority.

    Source provenance is intentionally a site-profile responsibility: the
    pipeline may choose between verified URLs, but it must never search-result
    scrape or fabricate a citation. The returned tuple is ``(url, language,
    priority)``; an empty URL is a hard gate for brief creation.
    """
    priority = language_priority(target_language, site_languages)
    normalized = {
        str(language).strip().lower(): urls
        for language, urls in (sources_i18n or {}).items()
    }
    for language in priority:
        for url in normalized.get(language, []) or []:
            parsed = urlparse(str(url).strip())
            if parsed.scheme in {"https", "http"} and parsed.netloc:
                return str(url).strip(), language, priority
    return "", "", priority


def external_link_policy(target_language: str, site_languages: list[str]) -> dict:
    """Return the non-negotiable source-language rule for a writer brief."""
    priority = language_priority(target_language, site_languages)
    return {
        "required": True,
        "minimum_links": 1,
        "language_priority": priority,
        "rule": (
            "Use a verified, authoritative external source in the article language "
            f"({priority[0]}) first. Only when no suitable source exists in that "
            "language may the next language in language_priority be used. Never "
            "invent a source or a URL."
        ),
    }


def make_action_anchor(target_language: str, cta_goal: str, page_title: str) -> str:
    """Create an honest anchor instruction, without pretending to translate.

    The CTA copy comes from the explicitly configured per-language site profile
    whenever possible. If it is absent, the real action-page title is safer
    than an invented translation.
    """
    return cta_goal.strip() or page_title.strip()
