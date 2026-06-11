### Internal

- Refactored the Literature REST API for maintainability with no change to the
  HTTP surface. The 37 Pydantic request/response schemas that were declared
  inline now live in the shared `app/schemas/literature.py` layer, and the
  single 1,854-line router module is split into an `app/api/literature/` package
  with one sub-router per sub-domain (papers, comments, sources, searches, and
  so on). Request and response shapes, routes, and behavior are unchanged; a
  golden route-table test asserts the API surface is identical.
