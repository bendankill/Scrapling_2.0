"""V2.2.1 category hierarchy evidence, validation, and thread-safe cache."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse


ROOT_CATEGORY_NAMES = frozenset({"emag", "acasa", "home"})
_PATH_KEYS = frozenset({
    "categorytrail", "categorypath", "categoryhierarchy", "categorychain",
})
_BREADCRUMB_KEYS = frozenset({"breadcrumb", "breadcrumbs", "breadcrumblist"})
_EMBEDDED_SCRIPT_MARKERS = (
    "__next_data__", "__initial_state__", "__preloaded_state__",
    "application_state", "digitaldata",
)
_IGNORED_BREADCRUMB_ANCESTORS = frozenset({
    "footer", "script", "template", "noscript", "head",
})


@dataclass(frozen=True)
class CategoryPathEvidence:
    """A category path plus the facts used to validate it."""

    levels: tuple[str, ...]
    source: str
    reliability: int
    current_category_explicit: bool = True
    current_category_appended: bool = False
    category_links_verified: bool = False
    structured_breadcrumb: bool = False
    is_tentative: bool = False
    validation_reason: str = ""


@dataclass(frozen=True)
class _PathDescriptor:
    levels: tuple[str, ...]
    source: str
    parent_links_verified: bool = False
    structured_breadcrumb: bool = False
    has_noncategory_parent_link: bool = False


def normalize_category_name(value: Any) -> str:
    """Compare category names without case, accents, punctuation, or spacing."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-z]+", "", without_marks.casefold())


def normalize_category_url(url: Any) -> str:
    """Build a cache key that ignores query strings and pagination."""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and not ((parsed.scheme == "http" and port == 80) or
                     (parsed.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = re.sub(r"/p\d+(?=/c(?:/|$))", "", parsed.path.casefold())
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    return f"{host}{path}"


def split_category_path_all(path: Any) -> list[str]:
    if not isinstance(path, str) or not path.strip():
        return []
    return [segment.strip() for segment in path.split("/") if segment.strip()]


def clean_category_levels(raw_levels: Any) -> list[str]:
    """Remove site roots, empty values, and normalized duplicates."""
    if isinstance(raw_levels, str):
        values = split_category_path_all(raw_levels)
    elif isinstance(raw_levels, Sequence) and not isinstance(
            raw_levels, (bytes, bytearray)):
        values = []
        for value in raw_levels:
            if isinstance(value, str):
                text = value.strip()
            elif isinstance(value, Mapping):
                text = str(value.get("name") or value.get("title") or
                           value.get("label") or "").strip()
            else:
                text = ""
            if text:
                values.append(text)
    else:
        return []

    while values and normalize_category_name(values[0]) in ROOT_CATEGORY_NAMES:
        values.pop(0)

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_category_name(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(value)
    return cleaned


def validate_category_path(
    raw_levels: Any,
    current_category: str,
    *,
    allow_append_current: bool = False,
) -> list[str]:
    """Require the current category to be the leaf; append only when authorized."""
    levels = clean_category_levels(raw_levels)
    if not levels:
        return []
    current = str(current_category or "").strip()
    normalized_current = normalize_category_name(current)
    if not normalized_current:
        return []
    positions = [
        index for index, level in enumerate(levels)
        if normalize_category_name(level) == normalized_current
    ]
    if positions:
        return levels if positions[-1] == len(levels) - 1 else []
    if allow_append_current:
        return [*levels, current]
    return []


def category_paths_are_consistent(
    left: Sequence[str], right: Sequence[str],
) -> bool:
    """Return true when the shorter path is an ordered subsequence of the longer."""
    left_norm = [normalize_category_name(value) for value in left]
    right_norm = [normalize_category_name(value) for value in right]
    if not left_norm or not right_norm or left_norm[-1] != right_norm[-1]:
        return False
    shorter, longer = ((left_norm, right_norm) if len(left_norm) <= len(right_norm)
                       else (right_norm, left_norm))
    cursor = 0
    for value in longer:
        if cursor < len(shorter) and value == shorter[cursor]:
            cursor += 1
    return cursor == len(shorter)


def _evidence_strength(evidence: CategoryPathEvidence) -> tuple[int, int, int, int]:
    return (
        int(not evidence.is_tentative),
        int(evidence.category_links_verified or evidence.structured_breadcrumb),
        evidence.reliability,
        int(evidence.current_category_explicit),
    )


def _prefer_consistent(
    left: CategoryPathEvidence, right: CategoryPathEvidence,
) -> CategoryPathEvidence:
    if len(left.levels) != len(right.levels):
        return left if len(left.levels) > len(right.levels) else right
    return left if _evidence_strength(left) >= _evidence_strength(right) else right


def select_best_category_evidence(
    candidates: Iterable[CategoryPathEvidence],
) -> CategoryPathEvidence | None:
    """Choose by semantic evidence first; length only completes a consistent chain."""
    valid = [candidate for candidate in candidates if candidate.levels]
    if not valid:
        return None

    best = valid[0]
    for candidate in valid[1:]:
        if category_paths_are_consistent(best.levels, candidate.levels):
            best = _prefer_consistent(best, candidate)
            continue
        if (not best.is_tentative and not candidate.is_tentative and
                (best.category_links_verified or best.structured_breadcrumb) and
                (candidate.category_links_verified or
                 candidate.structured_breadcrumb)):
            # Conflicting independently verified chains are ambiguous; source
            # rank must not turn either one into a guess.
            return None
        best_strength = _evidence_strength(best)
        candidate_strength = _evidence_strength(candidate)
        if candidate_strength > best_strength and not candidate.is_tentative:
            best = candidate
        elif candidate_strength == best_strength:
            # Two equally strong, conflicting page chains are ambiguous.
            return None
    return best


def extract_page_category_evidence(
    soup: Any,
    current_category: str,
    page_url: str,
) -> CategoryPathEvidence | None:
    """Extract hierarchy once from the existing page Soup; never reparses HTML."""
    if soup is None or not str(current_category or "").strip():
        return None

    candidates: list[CategoryPathEvidence] = []
    for descriptor in _visible_breadcrumb_paths(soup):
        evidence = _descriptor_to_evidence(descriptor, current_category)
        if evidence:
            candidates.append(evidence)

    for descriptor in _json_ld_breadcrumb_paths(soup):
        evidence = _descriptor_to_evidence(descriptor, current_category)
        if evidence:
            candidates.append(evidence)

    for raw_levels, is_breadcrumb in _embedded_category_paths(soup):
        levels = validate_category_path(raw_levels, current_category)
        if levels:
            candidates.append(CategoryPathEvidence(
                tuple(levels),
                "embedded_category_state",
                170 if is_breadcrumb else 150,
                current_category_explicit=True,
                is_tentative=True,
                validation_reason="embedded path has an explicit current-category leaf",
            ))

    return select_best_category_evidence(candidates)


def _descriptor_to_evidence(
    descriptor: _PathDescriptor,
    current_category: str,
) -> CategoryPathEvidence | None:
    if descriptor.has_noncategory_parent_link:
        return None
    explicit = bool(validate_category_path(descriptor.levels, current_category))
    allow_append = descriptor.parent_links_verified
    levels = validate_category_path(
        descriptor.levels,
        current_category,
        allow_append_current=allow_append,
    )
    if not levels:
        return None
    appended = not explicit
    verified = descriptor.parent_links_verified
    structured = descriptor.structured_breadcrumb
    final = verified
    if descriptor.source == "json_ld_breadcrumb":
        reliability = 420 if verified else 230
    else:
        reliability = 400 if verified else 220
    reason = (
        "parent category nodes have verified category links"
        if appended else
        "current category is the explicit breadcrumb leaf"
    )
    return CategoryPathEvidence(
        tuple(levels), descriptor.source, reliability,
        current_category_explicit=explicit,
        current_category_appended=appended,
        category_links_verified=verified,
        structured_breadcrumb=structured,
        is_tentative=not final,
        validation_reason=reason,
    )


def select_product_category_levels(
    extra: Any,
    category_name: str,
    verified_levels: Sequence[str] | None = None,
    category_evidence: CategoryPathEvidence | None = None,
) -> list[str]:
    """Select page and product paths as one candidate set without losing detail."""
    page = category_evidence
    if page is not None:
        validated_page = validate_category_path(page.levels, category_name)
        page = (replace(page, levels=tuple(validated_page))
                if validated_page else None)
    if page is None and verified_levels:
        levels = validate_category_path(verified_levels, category_name)
        if levels:
            page = CategoryPathEvidence(
                tuple(levels), "legacy_page_levels", 350,
                category_links_verified=True,
                validation_reason="legacy caller supplied already-verified page levels",
            )

    product_candidates: list[CategoryPathEvidence] = []
    if isinstance(extra, Mapping):
        favorite = extra.get("favorite_data")
        if isinstance(favorite, Mapping):
            levels = validate_category_path(
                favorite.get("category_trail"), category_name)
            if levels:
                product_candidates.append(CategoryPathEvidence(
                    tuple(levels), "favorite_data", 280,
                    validation_reason="product favorite_data category_trail",
                ))
        levels = validate_category_path(
            extra.get("data-category-trail"), category_name)
        if levels:
            product_candidates.append(CategoryPathEvidence(
                tuple(levels), "card_data_attribute", 260,
                validation_reason="product card data-category-trail",
            ))

    product = _select_product_evidence(product_candidates)
    selected = _select_page_and_product_evidence(page, product)
    return list(selected.levels[:5]) if selected else []


def _select_product_evidence(
    candidates: Sequence[CategoryPathEvidence],
) -> CategoryPathEvidence | None:
    if not candidates:
        return None
    best = candidates[0]
    for candidate in candidates[1:]:
        if category_paths_are_consistent(best.levels, candidate.levels):
            best = _prefer_consistent(best, candidate)
        elif len(candidate.levels) > len(best.levels):
            # Both values belong to the same product card. Preserve V2.2.0's
            # non-destructive rule by retaining the more complete original
            # product path; page evidence is evaluated separately below.
            best = candidate
        elif (len(candidate.levels) == len(best.levels) and
              candidate.reliability > best.reliability):
            best = candidate
    return best


def _select_page_and_product_evidence(
    page: CategoryPathEvidence | None,
    product: CategoryPathEvidence | None,
) -> CategoryPathEvidence | None:
    if page is None:
        return product
    if product is None:
        return page
    if category_paths_are_consistent(page.levels, product.levels):
        return _prefer_consistent(page, product)
    # A shorter page chain can never delete a fuller product chain.
    if len(page.levels) <= len(product.levels):
        return product
    # Tentative page evidence cannot override a product's own path.
    if page.is_tentative:
        return product
    # Only explicit category-link/structured evidence may resolve a conflict.
    if (page.category_links_verified or page.structured_breadcrumb) and (
            page.reliability > product.reliability):
        return page
    return product


class CategoryLevelRegistry:
    """Thread-safe side-channel cache that allows upgrades and forbids downgrades."""

    def __init__(self) -> None:
        self._entries: dict[str, CategoryPathEvidence] = {}
        self._lock = RLock()

    def register(self, category_url: str, evidence: CategoryPathEvidence) -> bool:
        key = normalize_category_url(category_url)
        if not key or not evidence.levels:
            return False
        candidate = _copy_evidence(evidence)
        with self._lock:
            existing = self._entries.get(key)
            replacement = _registry_replacement(existing, candidate)
            if replacement is None or replacement == existing:
                return False
            self._entries[key] = replacement
            return True

    def get(self, category_url: str) -> CategoryPathEvidence | None:
        key = normalize_category_url(category_url)
        if not key:
            return None
        with self._lock:
            evidence = self._entries.get(key)
            return _copy_evidence(evidence) if evidence else None

    def snapshot(self) -> dict[str, CategoryPathEvidence]:
        with self._lock:
            return {key: _copy_evidence(value)
                    for key, value in self._entries.items()}


def _copy_evidence(evidence: CategoryPathEvidence) -> CategoryPathEvidence:
    return replace(evidence, levels=tuple(evidence.levels))


def _registry_replacement(
    existing: CategoryPathEvidence | None,
    candidate: CategoryPathEvidence,
) -> CategoryPathEvidence | None:
    if existing is None:
        return candidate
    if category_paths_are_consistent(existing.levels, candidate.levels):
        preferred = _prefer_consistent(existing, candidate)
        return preferred
    # Conflicting evidence only replaces an unresolved tentative entry with
    # stronger, independently verified category evidence.
    if (existing.is_tentative and not candidate.is_tentative and
            _evidence_strength(candidate) > _evidence_strength(existing)):
        return candidate
    return existing


def _is_category_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    path = parsed.path.casefold().rstrip("/")
    return bool(re.search(r"/(?:p\d+/)?c$", path))


def _node_category_href(node: Any) -> str:
    link = node if getattr(node, "name", None) == "a" else node.find("a", href=True)
    return str(link.get("href") or "") if link else ""


def _has_category_id(node: Any) -> bool:
    for current in (node, node.find(True) if hasattr(node, "find") else None):
        if current is None:
            continue
        for name in ("data-category-id", "data-id", "category-id"):
            if str(current.get(name) or "").strip():
                return True
    return False


def _is_breadcrumb_container(node: Any) -> bool:
    tag = str(getattr(node, "name", "") or "").casefold()
    itemtype = str(node.get("itemtype") or "").casefold()
    aria = str(node.get("aria-label") or "").strip().casefold()
    role = str(node.get("role") or "").casefold()
    classes = " ".join(node.get("class") or []).casefold()
    identifier = str(node.get("id") or "").casefold()
    schema = "breadcrumblist" in itemtype
    semantic_aria = aria in {"breadcrumb", "breadcrumbs", "firimituri"}
    semantic_class = "breadcrumb" in classes or "breadcrumb" in identifier
    semantic_tag = tag in {"nav", "ol", "ul"} or role == "navigation"
    # data-testid alone is deliberately insufficient.
    return schema or semantic_aria or (semantic_class and semantic_tag)


def _inside_ignored_breadcrumb_area(node: Any) -> bool:
    current = node
    while current is not None:
        if str(getattr(current, "name", "") or "").casefold() in (
                _IGNORED_BREADCRUMB_ANCESTORS):
            return True
        current = getattr(current, "parent", None)
    return False


def _visible_breadcrumb_paths(soup: Any) -> list[_PathDescriptor]:
    from utils import _is_visible_element

    paths: list[_PathDescriptor] = []
    seen: set[tuple[str, ...]] = set()
    for container in soup.find_all(_is_breadcrumb_container):
        if (_inside_ignored_breadcrumb_area(container) or
                not _is_visible_element(container)):
            continue
        name_nodes = container.select("[itemprop='name']")
        nodes = name_nodes or container.find_all("li") or container.select("a, span")
        values: list[str] = []
        links: list[str] = []
        category_ids: list[bool] = []
        for node in nodes:
            if not _is_visible_element(node):
                continue
            text = node.get_text(" ", strip=True)
            if not text or text in {"/", ">", "›", "»"}:
                continue
            values.append(text)
            links.append(_node_category_href(node))
            category_ids.append(_has_category_id(node))
        levels = clean_category_levels(values)
        if not levels:
            continue
        # Align evidence to cleaned levels by discarding root-node metadata.
        root_count = max(0, len(values) - len(levels))
        aligned_links = links[root_count:root_count + len(levels)]
        aligned_ids = category_ids[root_count:root_count + len(levels)]
        parent_pairs = list(zip(aligned_links[:-1], aligned_ids[:-1]))
        parent_verified = bool(parent_pairs) and all(
            _is_category_url(href) or has_id for href, has_id in parent_pairs)
        noncategory_parent = any(
            bool(href) and not _is_category_url(href) and not has_id
            for href, has_id in parent_pairs
        )
        key = tuple(normalize_category_name(value) for value in levels)
        if key in seen:
            continue
        seen.add(key)
        paths.append(_PathDescriptor(
            tuple(levels), "visible_breadcrumb",
            parent_links_verified=parent_verified,
            structured_breadcrumb="breadcrumblist" in str(
                container.get("itemtype") or "").casefold(),
            has_noncategory_parent_link=noncategory_parent,
        ))
    return paths


def _json_ld_breadcrumb_paths(soup: Any) -> list[_PathDescriptor]:
    paths: list[_PathDescriptor] = []
    for script in soup.find_all("script"):
        if "ld+json" not in str(script.get("type") or "").casefold():
            continue
        payload = _parse_json_payload(script.string or script.get_text() or "")
        for breadcrumb in _find_breadcrumb_lists(payload):
            descriptor = _breadcrumb_items_to_descriptor(
                breadcrumb.get("itemListElement") or [])
            if descriptor:
                paths.append(descriptor)
    return paths


def _find_breadcrumb_lists(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        raw_type = value.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any(str(item).casefold() == "breadcrumblist" for item in types):
            yield value
        for nested in value.values():
            yield from _find_breadcrumb_lists(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _find_breadcrumb_lists(nested)


def _breadcrumb_items_to_descriptor(items: Any) -> _PathDescriptor | None:
    if not isinstance(items, list):
        return None
    positioned: list[tuple[int, str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        nested = item.get("item")
        name = item.get("name")
        url = ""
        if isinstance(nested, Mapping):
            name = name or nested.get("name")
            url = str(nested.get("@id") or nested.get("url") or "")
        elif isinstance(nested, str):
            url = nested
        url = str(item.get("url") or url)
        if not name:
            continue
        try:
            position = int(item.get("position") or index + 1)
        except (TypeError, ValueError):
            position = index + 1
        positioned.append((position, str(name).strip(), url))
    positioned.sort()
    names = [name for _position, name, _url in positioned]
    levels = clean_category_levels(names)
    if not levels:
        return None
    root_count = max(0, len(names) - len(levels))
    urls = [url for _position, _name, url in positioned][root_count:]
    parents = urls[:-1]
    parent_verified = bool(parents) and all(_is_category_url(url) for url in parents)
    noncategory_parent = any(bool(url) and not _is_category_url(url) for url in parents)
    return _PathDescriptor(
        tuple(levels), "json_ld_breadcrumb",
        parent_links_verified=parent_verified,
        structured_breadcrumb=True,
        has_noncategory_parent_link=noncategory_parent,
    )


def _embedded_category_paths(soup: Any) -> list[tuple[Any, bool]]:
    results: list[tuple[Any, bool]] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold()
        if "ld+json" in script_type:
            continue
        script_id = str(script.get("id") or "").casefold()
        text = (script.string or script.get_text() or "").strip()
        lowered = text[:500].casefold()
        is_json = "application/json" in script_type
        marked = any(marker in script_id or marker in lowered
                     for marker in _EMBEDDED_SCRIPT_MARKERS)
        if not text or not (is_json or marked):
            continue
        payload = _parse_json_payload(text, allow_assignment=marked)
        results.extend(_find_embedded_paths(payload))
    return results


def _find_embedded_paths(value: Any) -> list[tuple[Any, bool]]:
    results: list[tuple[Any, bool]] = []
    stack = [value]
    visited = 0
    while stack and visited < 100_000:
        current = stack.pop()
        visited += 1
        if isinstance(current, Mapping):
            for key, nested in current.items():
                canonical = re.sub(r"[^a-z]", "", str(key).casefold())
                if canonical in _PATH_KEYS:
                    results.append((nested, False))
                elif canonical in _BREADCRUMB_KEYS:
                    results.append((nested, True))
                if isinstance(nested, (Mapping, list)):
                    stack.append(nested)
        elif isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (Mapping, list)))
    return results


def _parse_json_payload(text: str, *, allow_assignment: bool = False) -> Any:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        if not allow_assignment:
            return None
    start = min((index for index in (stripped.find("{"), stripped.find("["))
                 if index >= 0), default=-1)
    if start < 0:
        return None
    closer = "}" if stripped[start] == "{" else "]"
    end = stripped.rfind(closer)
    if end <= start:
        return None
    try:
        return json.loads(stripped[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
