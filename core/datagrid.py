"""Shared DataGrid helpers (page size, URL state)."""

PER_PAGE_CHOICES = (10, 25, 50, 100)
DEFAULT_PER_PAGE = 25


def resolve_per_page(request) -> int:
    """Read validated ``per_page`` from query string."""
    raw = (request.GET.get("per_page") or "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PER_PAGE
    if n in PER_PAGE_CHOICES:
        return n
    return DEFAULT_PER_PAGE


def windowed_page_numbers(current: int, total: int, *, radius: int = 2):
    """
    Build a list of page numbers with None marking ellipsis gaps.
    Example: [1, None, 4, 5, 6, None, 12]
    """
    if total <= 0:
        return []
    if total == 1:
        return [1]
    if total <= 7:
        return list(range(1, total + 1))
    pages = {1, total, current}
    for i in range(max(1, current - radius), min(total, current + radius) + 1):
        pages.add(i)
    ordered = sorted(pages)
    out = []
    prev = None
    for p in ordered:
        if prev is not None and p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out
