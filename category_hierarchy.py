"""V2.2.1 列表页真实类目层级提取、验证与线程安全旁路缓存。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CategoryPathEvidence:
    """已经通过当前类目末级一致性检查的路径证据。"""

    levels: tuple[str, ...]
    source: str
    reliability: int


def normalize_category_name(value: Any) -> str:
    """忽略大小写、重音、标点、连字符和多余空格后比较类目名。"""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-z]+", "", without_marks.casefold())


def normalize_category_url(url: Any) -> str:
    """生成旁路缓存键：域名和路径小写、忽略查询参数及分页段。"""
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
    """只按真实层级分隔符 / 拆分字符串，不提前截断。"""
    if not isinstance(path, str) or not path.strip():
        return []
    return [segment.strip() for segment in path.split("/") if segment.strip()]


def clean_category_levels(raw_levels: Any) -> list[str]:
    """清理根节点、空段及重复层级，同时保持原顺序和原始文字。"""
    if isinstance(raw_levels, str):
        values = split_category_path_all(raw_levels)
    elif isinstance(raw_levels, Sequence) and not isinstance(raw_levels, (bytes, bytearray)):
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
    """验证当前类目必须位于末级；可靠父级面包屑可补入当前类目。"""
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


def select_best_category_evidence(
    candidates: Iterable[CategoryPathEvidence],
) -> CategoryPathEvidence | None:
    """综合完整度和可靠性选取唯一候选，不拼接互相独立的路径。"""
    valid = [candidate for candidate in candidates if candidate.levels]
    if not valid:
        return None
    return max(
        enumerate(valid),
        key=lambda item: (
            len(item[1].levels), item[1].reliability, -item[0],
        ),
    )[1]


def extract_page_category_evidence(
    soup: Any,
    current_category: str,
    page_url: str,
) -> CategoryPathEvidence | None:
    """从已创建的页面 Soup 中提取一次类目证据，不重新解析 HTML。"""
    if soup is None or not str(current_category or "").strip():
        return None

    can_append = _has_reliable_current_category(
        soup, current_category, page_url)
    candidates: list[CategoryPathEvidence] = []

    for raw_levels in _visible_breadcrumb_paths(soup):
        levels = validate_category_path(
            raw_levels, current_category, allow_append_current=can_append)
        if levels:
            candidates.append(CategoryPathEvidence(
                tuple(levels), "visible_breadcrumb", 300))

    for raw_levels in _json_ld_breadcrumb_paths(soup):
        levels = validate_category_path(
            raw_levels, current_category, allow_append_current=can_append)
        if levels:
            candidates.append(CategoryPathEvidence(
                tuple(levels), "json_ld_breadcrumb", 300))

    for raw_levels, is_breadcrumb in _embedded_category_paths(soup):
        levels = validate_category_path(
            raw_levels,
            current_category,
            allow_append_current=bool(is_breadcrumb and can_append),
        )
        if levels:
            candidates.append(CategoryPathEvidence(
                tuple(levels), "embedded_category_state", 200))

    return select_best_category_evidence(candidates)


def select_product_category_levels(
    extra: Any,
    category_name: str,
    verified_levels: Sequence[str] | None = None,
) -> list[str]:
    """统一选择页面旁路证据或商品原有 extra 路径，最终最多五级。"""
    if verified_levels:
        levels = validate_category_path(verified_levels, category_name)
        if levels:
            return levels[:5]

    if not isinstance(extra, Mapping):
        return []
    favorite = extra.get("favorite_data")
    sources = []
    if isinstance(favorite, Mapping):
        sources.append((0, favorite.get("category_trail"), "favorite_data"))
    sources.append((1, extra.get("data-category-trail"), "card_data_attribute"))

    candidates: list[tuple[int, int, list[str]]] = []
    for priority, raw_path, _source in sources:
        levels = validate_category_path(raw_path, category_name)
        if levels:
            candidates.append((len(levels), -priority, levels))
    if not candidates:
        return []
    return max(candidates, key=lambda item: (item[0], item[1]))[2][:5]


class CategoryLevelRegistry:
    """按规范化类目 URL 保存旁路证据；线程安全且禁止较差证据覆盖。"""

    def __init__(self) -> None:
        self._entries: dict[str, CategoryPathEvidence] = {}
        self._lock = RLock()

    def register(self, category_url: str, evidence: CategoryPathEvidence) -> bool:
        key = normalize_category_url(category_url)
        if not key or not evidence.levels:
            return False
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and not _evidence_is_better(evidence, existing):
                return False
            self._entries[key] = CategoryPathEvidence(
                tuple(evidence.levels), evidence.source, evidence.reliability)
            return True

    def get(self, category_url: str) -> CategoryPathEvidence | None:
        key = normalize_category_url(category_url)
        if not key:
            return None
        with self._lock:
            evidence = self._entries.get(key)
            if evidence is None:
                return None
            return CategoryPathEvidence(
                tuple(evidence.levels), evidence.source, evidence.reliability)

    def snapshot(self) -> dict[str, CategoryPathEvidence]:
        with self._lock:
            return dict(self._entries)


def _evidence_is_better(
    candidate: CategoryPathEvidence,
    existing: CategoryPathEvidence,
) -> bool:
    if len(candidate.levels) != len(existing.levels):
        return len(candidate.levels) > len(existing.levels)
    return candidate.reliability > existing.reliability


def _has_reliable_current_category(soup: Any, current_category: str, page_url: str) -> bool:
    current = normalize_category_name(current_category)
    if not current:
        return False
    path_parts = [part for part in urlparse(str(page_url or "")).path.split("/") if part]
    for index, part in enumerate(path_parts):
        if part.casefold() == "c" and index:
            previous = path_parts[index - 1]
            if re.fullmatch(r"p\d+", previous, flags=re.IGNORECASE) and index >= 2:
                previous = path_parts[index - 2]
            if normalize_category_name(previous) == current:
                return True
    for node in soup.select("h1, [aria-current='page'], [data-category-name]"):
        value = node.get("data-category-name") or node.get_text(" ", strip=True)
        if normalize_category_name(value) == current:
            return True
    return False


def _visible_breadcrumb_paths(soup: Any) -> list[list[str]]:
    from utils import _is_visible_element

    containers = []
    seen_nodes: set[int] = set()
    for node in soup.find_all(True):
        attrs = " ".join([
            str(node.get("id") or ""),
            " ".join(node.get("class") or []),
            str(node.get("aria-label") or ""),
            str(node.get("data-testid") or ""),
            str(node.get("itemtype") or ""),
        ]).casefold()
        if "breadcrumb" not in attrs or id(node) in seen_nodes:
            continue
        if not _is_visible_element(node):
            continue
        seen_nodes.add(id(node))
        containers.append(node)

    paths: list[list[str]] = []
    for container in containers:
        name_nodes = container.select("[itemprop='name']")
        if name_nodes:
            nodes = name_nodes
        else:
            list_items = container.find_all("li")
            nodes = list_items if list_items else container.select("a, span")
        values = []
        for node in nodes:
            if not _is_visible_element(node):
                continue
            text = node.get_text(" ", strip=True)
            if text and text not in {"/", ">", "›", "»"}:
                values.append(text)
        cleaned = clean_category_levels(values)
        if cleaned and cleaned not in paths:
            paths.append(cleaned)
    return paths


def _json_ld_breadcrumb_paths(soup: Any) -> list[list[str]]:
    paths: list[list[str]] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold()
        if "ld+json" not in script_type:
            continue
        payload = _parse_json_payload(script.string or script.get_text() or "")
        for breadcrumb in _find_breadcrumb_lists(payload):
            levels = _breadcrumb_items_to_levels(
                breadcrumb.get("itemListElement") or [])
            if levels:
                paths.append(levels)
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


def _breadcrumb_items_to_levels(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    positioned = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        nested = item.get("item")
        name = item.get("name")
        if not name and isinstance(nested, Mapping):
            name = nested.get("name")
        if not name:
            continue
        try:
            position = int(item.get("position") or index + 1)
        except (TypeError, ValueError):
            position = index + 1
        positioned.append((position, str(name).strip()))
    return clean_category_levels([name for _position, name in sorted(positioned)])


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
    opener = stripped[start]
    closer = "}" if opener == "{" else "]"
    end = stripped.rfind(closer)
    if end <= start:
        return None
    try:
        return json.loads(stripped[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
