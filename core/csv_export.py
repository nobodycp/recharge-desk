"""Helpers for streaming CSV downloads.

Excel on Windows opens UTF-8 CSVs as Latin-1 by default, which mangles
Arabic text. Prepending the UTF-8 BOM (``\ufeff``) makes Excel detect
the encoding correctly while remaining a no-op for Numbers, LibreOffice,
Google Sheets, and `csv.reader` consumers. The CSV module already
handles quoting so values containing commas, newlines or quotes survive
the round trip unchanged.

Each export view supplies an iterable of header strings and a generator
of row tuples; we yield one HTTP chunk per row to keep memory flat even
when the report covers tens of thousands of records.
"""

from __future__ import annotations

import csv
import io
from typing import Callable, Iterable, Sequence

from django.http import StreamingHttpResponse
from django.utils import timezone


class _Echo:
    """File-like target whose ``write`` returns the value, so ``csv.writer``
    can be turned into a chunk generator without buffering."""

    def write(self, value):
        return value


def _stream(headers: Sequence[str], rows: Iterable[Sequence]):
    writer = csv.writer(_Echo())
    yield "\ufeff"  # BOM for Excel
    yield writer.writerow(headers)
    for row in rows:
        yield writer.writerow(["" if v is None else v for v in row])


def csv_response(
    filename_stem: str,
    headers: Sequence[str],
    row_iter: Iterable[Sequence],
    *,
    timestamp: bool = True,
) -> StreamingHttpResponse:
    """Build a ``StreamingHttpResponse`` for a CSV download.

    ``filename_stem`` is appended with the current local date (so reruns
    don't overwrite each other in the user's Downloads folder) unless
    ``timestamp=False`` is passed for fixed-name exports.
    """
    name = filename_stem
    if timestamp:
        name = f"{filename_stem}-{timezone.localdate().isoformat()}"
    response = StreamingHttpResponse(
        _stream(headers, row_iter), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    # Hint to browsers / proxies that this URL is per-request data.
    response["Cache-Control"] = "no-store"
    return response


def csv_string(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    """Render a CSV (BOM + body) into a string. Useful for tests."""
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue()


__all__ = ["csv_response", "csv_string"]


# Convenience helper kept here so callers don't reimplement the same
# boolean-to-Arabic-friendly-yes/no conversion across exports.
def yesno(value, yes: str = "نعم", no: str = "لا") -> str:
    return yes if value else no


def fmt_dt(value, fmt: Callable | None = None) -> str:
    """Format a datetime for CSV: ISO-ish but local-time, no microseconds."""
    if value is None:
        return ""
    if fmt is not None:
        return fmt(value)
    local = timezone.localtime(value)
    return local.strftime("%Y-%m-%d %H:%M")
