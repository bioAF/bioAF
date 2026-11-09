"""Query parameters shared by the offset-paginated list routes that declare no bound.

``page`` and ``UnboundedPageSize`` keep these routes' currently accepted range:
any page size is honored and nothing is clamped. Whether to cap them is an open
decision; when it is made, the bound goes here, once, for every route using them.
Routes that already declare bounds (``Query(ge=1, le=...)``) or clamp in code keep
their own declarations, and cursor-paginated integration routes are separate.
"""

from typing import Annotated

from fastapi import Query

PageNumber = Annotated[int, Query()]
UnboundedPageSize = Annotated[int, Query()]
