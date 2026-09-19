# Modern UI startup and database loading

## What the UI loads at startup

The Modern UI does not open every Chroma database or read its vectors to build
the library list. The list is read from the app-owned SQLite catalog
(`gui_catalog.sqlite3`): `AppCatalog.list_databases()` selects the registered
rows, and the UI requests that list alongside recent jobs. Chroma export details
and content are requested later, when a user opens a database or explicitly
runs an inspection.

Startup has work before and after that catalog request:

1. The desktop host resolves the GUI state folder, constructs
   `WorkflowService`, initializes the app catalog, and performs best-effort
   recovery of interrupted jobs and activation journals before creating the
   window.
2. The React UI waits for the desktop bridge handshake, then requests databases
   and jobs. Once those responses arrive, it renders the library.
3. Source reconciliation is started separately after the library response; it
   is not awaited by the database-list refresh. It checks each registered
   producer connection, refreshes the app-owned managed-context cache, and
   inspects producer status for its discovered contexts. This is distinct from
   loading the registered Chroma database rows and can continue after the
   library appears.

Source reconciliation can still be expensive on a slow or remote source path.
It checks producer manifests and pointers, reads current handoff/state metadata,
and checks referenced processed-cache files. The amount of registered producer
work and the storage speed matter more here than the number of Chroma databases
shown in the library.

## Interpreting a roughly 60-second delay

A 60-second delay is not an intentional wait in the database-list UI flow. The
bridge's wait for pywebview readiness is bounded at five seconds. App-catalog
SQLite connections, however, allow a lock wait of up to 30 seconds per
connection. Lock contention could therefore contribute to a long startup when
several catalog operations are blocked, but the configured timeout alone does
not establish that this is the cause.

Use what appears late to narrow the investigation:

- If the window or library itself appears late, measure host startup and the
  handshake, database-list, and job-list calls. Check for slow GUI-state storage
  or another process holding the app catalog open.
- If the library appears promptly but source status or partition cards arrive
  late, measure `reconcile_sources` and inspect the registered producer paths,
  especially remote/network locations and large handoff or state folders.
- If only a database's detail/content view is late, measure that database's
  detail/content request separately; it is not part of the initial list query.

These are diagnostic branches, not a confirmed explanation for a particular
machine. To identify the exact delay, capture elapsed time for host/service
initialization, bridge readiness, handshake, `list_databases`, `list_jobs`, and
`reconcile_sources` independently. Do not infer Chroma vector-loading time from
the library's initial appearance.
