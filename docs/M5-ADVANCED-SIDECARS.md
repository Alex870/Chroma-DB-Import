# M5 Immutable Sidecars

`conda run -n chroma-db-import python -m chroma_db_import.advanced_sidecars build` packages a release-aligned graph, late-chunk alignment, and explicitly supplied token vectors outside Chroma. It rejects release drift, document alignment drift, dimension drift, unresolved model revisions, and unsupported contract majors.

Packages are content addressed and immutable. `maxsim_search` scores only an explicit bounded candidate set. Removing the package leaves the accepted dense/hybrid release untouched, and this command never downloads a model.
