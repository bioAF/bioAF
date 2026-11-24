### Lab documents and literature

- PDF viewing uses a patched PDF reader (pdf.js 6.5) that closes font-name style injection, slow pattern matching
  and looping form-field structures in crafted PDFs. Embedded PDF scripts stay disabled.
- Closing a PDF, opening another document or version, or opening a file that cannot be read now stops the
  background PDF reader instead of leaving it running, including when the reader is stuck and does not respond.
- A PDF that takes more than 30 seconds to open, or to draw a page, is stopped and reported as not loaded instead of
  keeping the browser busy.
- A page that cannot be drawn shows a message in place of that page; the other pages, paging and Download stay
  available. Turning pages quickly no longer lets a slow page draw over the one you moved to.

### Security and dependencies

- Charts and Markdown dependencies are updated to patched releases: Recharts 3.11, Redux Toolkit 2.15, Immer 11.2,
  es-toolkit 1.53, structured-clone 1.4 and the inline style parser 0.2.9. The PDF reader's optional native canvas
  package moves to 1.2 (not included in the frontend image).
