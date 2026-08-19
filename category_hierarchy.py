"""V2.2.1 category hierarchy evidence, validation, and thread-safe cache."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlparse


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
class CategoryBreadcrumbNode:
    """One indivisible breadcrumb item; metadata never drifts from its name."""

    name: str
    source: str
    url: str = ""
    category_id: str = ""
    position: int | None = None


@dataclass(frozen=True)
class _PathDescriptor:
    nodes: tuple[CategoryBreadcrumbNode, ...]
    source: str
    parent_links_verified: bool = False
    all_nodes_verified: bool = False
    structured_breadcrumb: bool = False
    has_noncategory_parent_link: bool = False
    has_noncategory_link: bool = False
    leaf_link: str = ""
    has_invalid_leaf_link: bool = False

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(node.name for node in self.nodes)


class CategoryEvidenceStatus(str, Enum):
    MISSING = "missing"
    TEMPORARY = "temporary"
    TEMPORARY_CONFLICTED = "temporary_conflicted"
    FINAL = "final"
    FINAL_CONFLICTED = "final_conflicted"


@dataclass(frozen=True)
class CategoryEvidenceDecision:
    """Deterministic result after all page candidates have been collected."""

    status: CategoryEvidenceStatus
    evidence: CategoryPathEvidence | None = None
    candidate_count: int = 0
    reason: str = ""


@dataclass(frozen=True)
class CategoryRegistryEntry:
    status: CategoryEvidenceStatus
    evidence: CategoryPathEvidence | None = None


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


def _clean_breadcrumb_nodes(
    raw_nodes: Sequence[CategoryBreadcrumbNode],
) -> list[CategoryBreadcrumbNode]:
    """Sort, trim, de-root and de-duplicate whole nodes without metadata slicing."""
    indexed: list[tuple[int, CategoryBreadcrumbNode]] = []
    for index, node in enumerate(raw_nodes):
        name = str(node.name or "").strip()
        if not name:
            continue
        position = node.position
        if isinstance(position, bool) or not isinstance(position, int) or position < 1:
            position = index + 1
        indexed.append((index, replace(
            node,
            name=name,
            url=str(node.url or "").strip(),
            category_id=str(node.category_id or "").strip(),
            position=position,
        )))
    indexed.sort(key=lambda item: (item[1].position or item[0] + 1, item[0]))
    nodes = [node for _index, node in indexed]
    while nodes and normalize_category_name(nodes[0].name) in ROOT_CATEGORY_NAMES:
        nodes.pop(0)

    cleaned: list[CategoryBreadcrumbNode] = []
    seen: set[str] = set()
    for node in nodes:
        normalized = normalize_category_name(node.name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(node)
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


def _evidence_tier(evidence: CategoryPathEvidence) -> int:
    """Rank independent trust before path completeness or source score."""
    if evidence.is_tentative:
        return 1
    if evidence.category_links_verified:
        return 4
    if evidence.structured_breadcrumb:
        return 3
    return 2


def _evidence_strength(evidence: CategoryPathEvidence) -> tuple[int, int, int]:
    return (
        _evidence_tier(evidence),
        evidence.reliability,
        int(evidence.current_category_explicit),
    )


def _prefer_consistent(
    left: CategoryPathEvidence, right: CategoryPathEvidence,
) -> CategoryPathEvidence:
    left_tier = _evidence_tier(left)
    right_tier = _evidence_tier(right)
    if left_tier != right_tier:
        return left if left_tier > right_tier else right
    if len(left.levels) != len(right.levels):
        return left if len(left.levels) > len(right.levels) else right
    return left if _evidence_strength(left) >= _evidence_strength(right) else right


def _deterministic_preference_key(
    evidence: CategoryPathEvidence,
) -> tuple[int, int, int, int, tuple[str, ...], str]:
    return (
        len(evidence.levels),
        evidence.reliability,
        int(evidence.structured_breadcrumb),
        int(evidence.current_category_explicit),
        tuple(normalize_category_name(level) for level in evidence.levels),
        evidence.source,
    )


def decide_category_evidence(
    candidates: Iterable[CategoryPathEvidence],
) -> CategoryEvidenceDecision:
    """Resolve a complete candidate set without depending on traversal order."""
    valid = [candidate for candidate in candidates if candidate.levels]
    if not valid:
        return CategoryEvidenceDecision(CategoryEvidenceStatus.MISSING)

    finals = [candidate for candidate in valid if not candidate.is_tentative]
    active = finals or valid
    conflict_status = (
        CategoryEvidenceStatus.FINAL_CONFLICTED
        if finals else CategoryEvidenceStatus.TEMPORARY_CONFLICTED)
    accepted_status = (
        CategoryEvidenceStatus.FINAL
        if finals else CategoryEvidenceStatus.TEMPORARY)

    ordered = sorted(active, key=_deterministic_preference_key, reverse=True)
    best = ordered[0]
    for candidate in ordered[1:]:
        if not category_paths_are_consistent(best.levels, candidate.levels):
            return CategoryEvidenceDecision(
                conflict_status,
                candidate_count=len(valid),
                reason="same-trust page category paths conflict",
            )
        best = _prefer_consistent(best, candidate)
    return CategoryEvidenceDecision(
        accepted_status,
        _copy_evidence(best),
        candidate_count=len(valid),
        reason="all accepted page paths are consistent",
    )


def select_best_category_evidence(
    candidates: Iterable[CategoryPathEvidence],
) -> CategoryPathEvidence | None:
    """Compatibility wrapper returning no public evidence for any conflict."""
    return decide_category_evidence(candidates).evidence


def extract_page_category_evidence(
    soup: Any,
    current_category: str,
    page_url: str,
) -> CategoryPathEvidence | None:
    """Compatibility wrapper for callers that only need accepted evidence."""
    return extract_page_category_decision(
        soup, current_category, page_url).evidence


def extract_page_category_decision(
    soup: Any,
    current_category: str,
    page_url: str,
) -> CategoryEvidenceDecision:
    """Collect every page candidate, then resolve once without order bias."""
    if soup is None or not str(current_category or "").strip():
        return CategoryEvidenceDecision(CategoryEvidenceStatus.MISSING)

    candidates: list[CategoryPathEvidence] = []
    for descriptor in _visible_breadcrumb_paths(soup, page_url):
        evidence = _descriptor_to_evidence(descriptor, current_category)
        if evidence:
            candidates.append(evidence)

    for descriptor in _json_ld_breadcrumb_paths(soup, page_url):
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

    return decide_category_evidence(candidates)


def _descriptor_to_evidence(
    descriptor: _PathDescriptor,
    current_category: str,
) -> CategoryPathEvidence | None:
    explicit = bool(validate_category_path(descriptor.levels, current_category))
    if explicit:
        if (descriptor.has_noncategory_parent_link or
                descriptor.has_invalid_leaf_link):
            return None
        verified = descriptor.parent_links_verified
    else:
        if descriptor.has_noncategory_link:
            return None
        verified = descriptor.all_nodes_verified
    if descriptor.source == "visible_breadcrumb" and not verified:
        # Visible text alone can describe account/help navigation. It is not
        # safe as a category-wide cache when no product path exists.
        return None
    allow_append = verified
    levels = validate_category_path(
        descriptor.levels,
        current_category,
        allow_append_current=allow_append,
    )
    if not levels:
        return None
    appended = not explicit
    structured = descriptor.structured_breadcrumb
    # A structured BreadcrumbList with an explicit leaf is independently
    # meaningful even when optional URLs are absent. Parent-only paths still
    # require verified links/IDs before the current category may be appended.
    final = verified or (structured and explicit)
    if descriptor.source == "json_ld_breadcrumb":
        reliability = 420 if verified else 230
    else:
        reliability = 400 if verified else 220
    reason = (
        "parent category nodes have verified category links or IDs"
        if appended else (
            "structured breadcrumb has an explicit current-category leaf"
            if structured and not verified else
            "current category is the explicit verified breadcrumb leaf"
        )
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
        # A product's own complete chain is not discarded by a shorter page
        # chain, even when that page chain is independently verified.
        if len(product.levels) > len(page.levels):
            return product
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
    """Atomic category evidence state machine with sticky final conflicts."""

    def __init__(self) -> None:
        self._entries: dict[str, CategoryRegistryEntry] = {}
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

    def register_conflict(self, category_url: str, *, final: bool) -> bool:
        """Atomically record a page-local conflict at its actual trust level."""
        key = normalize_category_url(category_url)
        if not key:
            return False
        conflict_status = (
            CategoryEvidenceStatus.FINAL_CONFLICTED
            if final else CategoryEvidenceStatus.TEMPORARY_CONFLICTED)
        with self._lock:
            existing = self._entries.get(key)
            replacement = _registry_conflict_replacement(
                existing, conflict_status)
            if replacement == existing:
                return False
            self._entries[key] = replacement
            return True

    def get(self, category_url: str) -> CategoryPathEvidence | None:
        key = normalize_category_url(category_url)
        if not key:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.status in {
                    CategoryEvidenceStatus.TEMPORARY_CONFLICTED,
                    CategoryEvidenceStatus.FINAL_CONFLICTED}:
                return None
            return _copy_evidence(entry.evidence) if entry.evidence else None

    def get_state(self, category_url: str) -> CategoryEvidenceStatus:
        key = normalize_category_url(category_url)
        if not key:
            return CategoryEvidenceStatus.MISSING
        with self._lock:
            entry = self._entries.get(key)
            return entry.status if entry else CategoryEvidenceStatus.MISSING

    def snapshot(self) -> dict[str, CategoryRegistryEntry]:
        with self._lock:
            return {
                key: CategoryRegistryEntry(
                    value.status,
                    _copy_evidence(value.evidence) if value.evidence else None,
                )
                for key, value in self._entries.items()
            }


def _copy_evidence(evidence: CategoryPathEvidence) -> CategoryPathEvidence:
    return replace(evidence, levels=tuple(evidence.levels))


def _registry_replacement(
    existing: CategoryRegistryEntry | None,
    candidate: CategoryPathEvidence,
) -> CategoryRegistryEntry:
    candidate_status = (
        CategoryEvidenceStatus.TEMPORARY
        if candidate.is_tentative else CategoryEvidenceStatus.FINAL)
    candidate_entry = CategoryRegistryEntry(candidate_status, candidate)
    if existing is None:
        return candidate_entry
    if existing.status == CategoryEvidenceStatus.FINAL_CONFLICTED:
        return existing
    if existing.status == CategoryEvidenceStatus.TEMPORARY_CONFLICTED:
        return candidate_entry if not candidate.is_tentative else existing
    current = existing.evidence
    if current is None:
        return candidate_entry

    current_final = existing.status == CategoryEvidenceStatus.FINAL
    candidate_final = not candidate.is_tentative
    if current_final and not candidate_final:
        return existing
    if not current_final and candidate_final:
        return candidate_entry
    if category_paths_are_consistent(current.levels, candidate.levels):
        preferred = _prefer_consistent(current, candidate)
        return CategoryRegistryEntry(existing.status, preferred)
    if current_final and candidate_final:
        return CategoryRegistryEntry(CategoryEvidenceStatus.FINAL_CONFLICTED)
    if not current_final and not candidate_final:
        return CategoryRegistryEntry(CategoryEvidenceStatus.TEMPORARY_CONFLICTED)
    return existing


def _registry_conflict_replacement(
    existing: CategoryRegistryEntry | None,
    conflict_status: CategoryEvidenceStatus,
) -> CategoryRegistryEntry:
    if existing is None:
        return CategoryRegistryEntry(conflict_status)
    if existing.status == CategoryEvidenceStatus.FINAL_CONFLICTED:
        return existing
    if conflict_status == CategoryEvidenceStatus.FINAL_CONFLICTED:
        return CategoryRegistryEntry(CategoryEvidenceStatus.FINAL_CONFLICTED)
    if existing.status == CategoryEvidenceStatus.FINAL:
        # Lower-trust temporary conflicts cannot disable accepted final proof.
        return existing
    return CategoryRegistryEntry(CategoryEvidenceStatus.TEMPORARY_CONFLICTED)


def _origin_key(parsed: Any) -> tuple[str, str, int | None]:
    scheme = str(parsed.scheme or "").casefold()
    host = str(parsed.hostname or "").casefold()
    if host in {"www.emag.ro", "emag.ro"}:
        host = "emag.ro"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if scheme == "https" else (80 if scheme == "http" else None)
    return scheme, host, port


def _is_category_url(value: Any, page_url: str) -> bool:
    """Accept category URLs only when they resolve to the current page origin."""
    raw = str(value or "").strip()
    base = urlparse(str(page_url or ""))
    if not raw or base.scheme.casefold() not in {"http", "https"} or not base.hostname:
        return False
    resolved = urlparse(urljoin(str(page_url), raw))
    if resolved.scheme.casefold() not in {"http", "https"}:
        return False
    if _origin_key(resolved) != _origin_key(base):
        return False
    path = resolved.path.casefold().rstrip("/")
    return bool(re.search(r"/(?:p\d+/)?c$", path))


def _leaf_category_url_matches(value: Any, page_url: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    if not _is_category_url(raw, page_url):
        return False
    return normalize_category_url(urljoin(str(page_url), raw)) == normalize_category_url(
        page_url)


def _itemprop_tokens(node: Any) -> set[str]:
    return {
        token.casefold()
        for token in str(node.get("itemprop") or "").split()
        if token.strip()
    }


def _category_id_value(*nodes: Any) -> str:
    for node in nodes:
        if node is None:
            continue
        candidates = [node]
        if hasattr(node, "find_all"):
            candidates.extend(node.find_all(True))
        for current in candidates:
            for name in ("data-category-id", "category-id"):
                value = str(current.get(name) or "").strip()
                if value:
                    return value
    return ""


def _has_category_id(node: Any) -> bool:
    return bool(_category_id_value(node))


def _find_itemprop_node(node: Any, itemprop: str) -> Any:
    wanted = itemprop.casefold()
    if wanted in _itemprop_tokens(node):
        return node
    for candidate in node.find_all(True):
        if wanted in _itemprop_tokens(candidate):
            return candidate
    return None


def _bounded_breadcrumb_link(item: Any, name_node: Any) -> Any:
    """Find a URL only inside the current logical item, including name parents."""
    if getattr(item, "name", None) in {"a", "link"} and item.get("href"):
        return item
    if name_node is not None:
        if getattr(name_node, "name", None) in {"a", "link"} and name_node.get("href"):
            return name_node
        nested = name_node.find(["a", "link"], href=True)
        if nested:
            return nested
        current = getattr(name_node, "parent", None)
        while current is not None:
            if getattr(current, "name", None) in {"a", "link"} and current.get("href"):
                return current
            if current is item:
                break
            current = getattr(current, "parent", None)
    item_link = _find_itemprop_node(item, "item")
    if item_link is not None and item_link.get("href"):
        return item_link
    return item.find(["a", "link"], href=True)


def _breadcrumb_item_position(item: Any, fallback: int) -> int:
    raw = item.get("position")
    position_node = _find_itemprop_node(item, "position")
    if position_node is not None:
        raw = (position_node.get("content") or position_node.get("value") or
               position_node.get_text(" ", strip=True) or raw)
    try:
        value = int(raw)
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _breadcrumb_node_from_item(
    item: Any,
    *,
    source: str,
    fallback_position: int,
) -> CategoryBreadcrumbNode | None:
    name_node = _find_itemprop_node(item, "name")
    if name_node is not None:
        name = str(name_node.get("content") or
                   name_node.get_text(" ", strip=True) or "").strip()
    else:
        name = str(item.get("content") or
                   item.get_text(" ", strip=True) or "").strip()
    if not name or name in {"/", ">", "›", "»"}:
        return None
    link = _bounded_breadcrumb_link(item, name_node)
    url = str(link.get("href") or "").strip() if link else ""
    return CategoryBreadcrumbNode(
        name=name,
        source=source,
        url=url,
        category_id=_category_id_value(item, name_node, link),
        position=_breadcrumb_item_position(item, fallback_position),
    )


def _breadcrumb_items(container: Any) -> list[Any]:
    schema_items = [
        node for node in container.find_all(True)
        if "itemlistelement" in _itemprop_tokens(node)
    ]
    if schema_items:
        # Nested descendants that repeat itemListElement are not separate items.
        return [
            node for node in schema_items
            if not any(
                ancestor is not container and
                "itemlistelement" in _itemprop_tokens(ancestor)
                for ancestor in node.parents
                if ancestor is not None
            )
        ]
    list_items = container.find_all("li")
    if list_items:
        return list_items
    anchors = container.find_all("a", href=True)
    if anchors:
        return anchors
    return container.find_all("span", recursive=True)


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


def _visible_breadcrumb_paths(
    soup: Any, page_url: str,
) -> list[_PathDescriptor]:
    from utils import _is_visible_element

    paths: list[_PathDescriptor] = []
    seen: set[tuple[str, ...]] = set()
    for container in soup.find_all(_is_breadcrumb_container):
        if (_inside_ignored_breadcrumb_area(container) or
                not _is_visible_element(container)):
            continue
        raw_nodes: list[CategoryBreadcrumbNode] = []
        items = _breadcrumb_items(container)
        for index, item in enumerate(items, 1):
            if not _is_visible_element(item):
                continue
            node = _breadcrumb_node_from_item(
                item, source="visible_breadcrumb", fallback_position=index)
            if node:
                raw_nodes.append(node)
        nodes = _clean_breadcrumb_nodes(raw_nodes)
        if not nodes:
            continue
        parent_pairs = [(node.url, bool(node.category_id)) for node in nodes[:-1]]
        all_pairs = [(node.url, bool(node.category_id)) for node in nodes]
        leaf_link = nodes[-1].url
        parent_verified = all(
            _is_category_url(href, page_url) if href else has_id
            for href, has_id in parent_pairs) and (
                bool(parent_pairs) or bool(leaf_link))
        all_nodes_verified = bool(all_pairs) and all(
            _is_category_url(href, page_url) if href else has_id
            for href, has_id in all_pairs)
        noncategory_parent = any(
            bool(href) and not _is_category_url(href, page_url)
            for href, has_id in parent_pairs
        )
        noncategory_link = any(
            bool(href) and not _is_category_url(href, page_url)
            for href, has_id in all_pairs)
        key = tuple(normalize_category_name(node.name) for node in nodes)
        if key in seen:
            continue
        seen.add(key)
        paths.append(_PathDescriptor(
            tuple(nodes), "visible_breadcrumb",
            parent_links_verified=parent_verified,
            all_nodes_verified=all_nodes_verified,
            structured_breadcrumb=(
                "breadcrumblist" in str(
                    container.get("itemtype") or "").casefold() or
                any("itemlistelement" in _itemprop_tokens(item)
                    for item in items)
            ),
            has_noncategory_parent_link=noncategory_parent,
            has_noncategory_link=noncategory_link,
            leaf_link=leaf_link,
            has_invalid_leaf_link=not _leaf_category_url_matches(
                leaf_link, page_url),
        ))
    return paths


def _json_ld_breadcrumb_paths(
    soup: Any, page_url: str,
) -> list[_PathDescriptor]:
    paths: list[_PathDescriptor] = []
    for script in soup.find_all("script"):
        if "ld+json" not in str(script.get("type") or "").casefold():
            continue
        payload = _parse_json_payload(script.string or script.get_text() or "")
        for breadcrumb in _find_breadcrumb_lists(payload):
            descriptor = _breadcrumb_items_to_descriptor(
                breadcrumb.get("itemListElement") or [], page_url)
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


def _breadcrumb_items_to_descriptor(
    items: Any, page_url: str,
) -> _PathDescriptor | None:
    if not isinstance(items, list):
        return None
    raw_nodes: list[CategoryBreadcrumbNode] = []
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
        category_id = str(
            item.get("category-id") or item.get("data-category-id") or
            (nested.get("category-id") if isinstance(nested, Mapping) else "") or
            (nested.get("data-category-id") if isinstance(nested, Mapping) else "") or
            "")
        raw_nodes.append(CategoryBreadcrumbNode(
            name=str(name).strip(),
            source="json_ld_breadcrumb",
            url=url,
            category_id=category_id,
            position=position,
        ))
    nodes = _clean_breadcrumb_nodes(raw_nodes)
    if not nodes:
        return None
    parents = [node.url for node in nodes[:-1]]
    leaf_link = nodes[-1].url
    parent_verified = all(
        (_is_category_url(node.url, page_url) if node.url else bool(node.category_id))
        for node in nodes[:-1]) and (
            bool(nodes[:-1]) or bool(leaf_link))
    all_nodes_verified = bool(nodes) and all(
        (_is_category_url(node.url, page_url) if node.url else bool(node.category_id))
        for node in nodes)
    noncategory_parent = any(
        bool(url) and not _is_category_url(url, page_url) for url in parents)
    noncategory_link = any(
        bool(node.url) and not _is_category_url(node.url, page_url)
        for node in nodes)
    return _PathDescriptor(
        tuple(nodes), "json_ld_breadcrumb",
        parent_links_verified=parent_verified,
        all_nodes_verified=all_nodes_verified,
        structured_breadcrumb=True,
        has_noncategory_parent_link=noncategory_parent,
        has_noncategory_link=noncategory_link,
        leaf_link=leaf_link,
        has_invalid_leaf_link=not _leaf_category_url_matches(
            leaf_link, page_url),
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
