from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

from core.datagrid import DEFAULT_PER_PAGE, resolve_per_page

DEFAULT_PAGE_SIZE = DEFAULT_PER_PAGE


def paginate_request(
    request,
    queryset,
    *,
    per_page=None,
    orphans=0,
    page_param="page",
    allow_empty_first_page=False,
):
    """
    Return a Page for queryset, preserving other GET params via templates
    using the ``page_url`` template tag for links.

    If ``per_page`` is None, reads ``per_page`` from the request query string
    (validated against :data:`core.datagrid.PER_PAGE_CHOICES`).
    """
    if per_page is None:
        per_page = resolve_per_page(request)
    paginator = Paginator(
        queryset,
        per_page,
        orphans=orphans,
        allow_empty_first_page=allow_empty_first_page,
    )
    raw_page = request.GET.get(page_param) or 1
    try:
        page_number = int(raw_page)
    except (TypeError, ValueError):
        page_number = 1
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        if paginator.num_pages > 0:
            return paginator.page(paginator.num_pages)
        # Empty object_list yields num_pages == 0; serve one empty page.
        paginator = Paginator(
            queryset,
            per_page,
            orphans=orphans,
            allow_empty_first_page=True,
        )
        return paginator.page(1)
