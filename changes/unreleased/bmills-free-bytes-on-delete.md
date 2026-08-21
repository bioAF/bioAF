### Files

- Deleting a file now erases the file itself, not just its row. The storage it
  occupied is freed, which is the point of deleting it: until now the bytes
  stayed in the bucket and the space was never reclaimed. bioAF still keeps a
  permanent record that the file existed, who deleted it and when, and which
  runs used it, so no result loses its provenance.
- The confirmation before a delete says what actually happens: the data is
  erased permanently and cannot be recovered.
- If storage cannot be reached, the file is not deleted at all and the message
  says which of the selected files were erased and which are still here.
