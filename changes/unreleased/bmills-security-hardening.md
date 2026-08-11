### Security

- Updated the behind-the-scenes software libraries bioAF depends on, clearing
  every outstanding advisory raised by automated dependency scanning (29 in
  total, across the backend and the web interface). Routine security upkeep:
  how bioAF looks and works for you is unchanged.

- Hardened the built-in PDF viewer used for literature papers and lab
  documents. PDFs can carry embedded scripts, and the viewer previously ran
  them. Since these files come from outside sources (publishers, or whatever a
  colleague uploads), that was a way for a malicious document to run code in
  your browser session. The viewer no longer runs anything a PDF asks it to.
  Papers and documents display and page through exactly as before.

### Fixes

- Fixed a database upgrade step that could fail on installs where the bioAF
  database shares a server with other schemas, leaving the upgrade part-way
  through. Existing installations are unaffected.
