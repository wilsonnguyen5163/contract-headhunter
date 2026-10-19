# Sample data (synthetic)

Fake contractors (`contractors.csv`, `contractor_facets.csv`) and fake postings (`jobs.csv`) for tests and demos. The jobs cover English, French and bilingual text and every status the pipeline produces: `new`, `needs_details` (a LinkedIn stub), and `filtered_out` (one permanent role, one US role). All URLs point to example.org. **Never put real contractor or job data here**: this folder is committed, while the rest of `data/` is git-ignored.

Check the files with `python -m jobmatch validate-data --data-dir data/samples`.
