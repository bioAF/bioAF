"""plan_8_5 section 3.5: the executable code inside a document, kept as what it is.

Groff's supplement is five R Markdown documents inside one Word file: YAML front matter, prose, and
a hundred fenced chunks. bioAF recorded the whole thing as unreadable, so a paper that supplied all
of its code scored nothing for any code obligation.

Extracting it is not the same as treating the document as one R program. A chunk is a unit with its
own place in the document, its own label and its own options; the prose around it is not source; and
a document that cannot be split is still recorded as one bioAF could not split.
"""

from app.services.code_documents import rmarkdown_documents

_TEXT = """---
title: "Figure1_Embryo"
output: html_document
---

Code to generate figure 1, establish QC thresholds

```{r setup}
library(DESeq2)
x <- 1
```

Some prose between the chunks.

```{r plot, echo=FALSE, fig.width=7}
plot(x)
```

---
title: "Figure2"
---

```{python not_r}
print("this is not R")
```
"""


class TestTheChunksAreKeptAsChunks:
    def test_each_chunk_keeps_its_engine_label_and_options(self):
        documents = rmarkdown_documents(_TEXT)
        chunks = [c for d in documents for c in d["chunks"]]
        assert [c["label"] for c in chunks] == ["setup", "plot", "not_r"]
        assert chunks[0]["engine"] == "r"
        assert "echo=FALSE" in chunks[1]["options"]
        assert chunks[2]["engine"] == "python"

    def test_each_chunk_keeps_where_it_is_in_the_document(self):
        chunks = [c for d in rmarkdown_documents(_TEXT) for c in d["chunks"]]
        assert chunks[0]["line"] == 8
        assert chunks[0]["order"] == 1
        assert chunks[1]["order"] == 2

    def test_the_code_is_the_chunk_and_not_the_prose_around_it(self):
        chunks = [c for d in rmarkdown_documents(_TEXT) for c in d["chunks"]]
        assert chunks[0]["code"] == "library(DESeq2)\nx <- 1"
        assert "prose" not in chunks[0]["code"]

    def test_the_documents_are_split_where_the_front_matter_says_so(self):
        documents = rmarkdown_documents(_TEXT)
        assert [d["title"] for d in documents] == ["Figure1_Embryo", "Figure2"]
        assert len(documents[0]["chunks"]) == 2

    def test_a_chunk_that_never_closes_is_recorded_rather_than_swallowed(self):
        documents = rmarkdown_documents('```{r open}\nx <- 1\n')
        chunk = documents[0]["chunks"][0]
        assert chunk["unterminated"] is True
        assert chunk["code"] == "x <- 1"

    def test_a_document_with_no_chunks_yields_no_code(self):
        assert [c for d in rmarkdown_documents("just prose, nothing fenced") for c in d["chunks"]] == []
