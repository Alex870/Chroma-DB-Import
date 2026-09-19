# Design change: contextual managed-partition database creation

Date: 2026-09-13  
Status: Approved for implementation  
Scope: Canonical React UI and importer-owned GUI bridge only; the D: runtime copy is unchanged.

## Decision

Keep automatic partition discovery, but make each eligible row in **Available
partitions** the direct entry point to the existing three-step database-creation
wizard. The selected row supplies a contextual managed-creation payload, so the
wizard opens with a complete, reviewable draft instead of asking the operator to
re-enter values that discovery already knows.

`ready_to_create` gets a primary **Create database** action. A profile mismatch
gets **Create separate database**. Linked databases retain their update/review
actions. Unready partitions show the blocking reason and no misleading create
control. The generic header action remains the unscoped processed-folder flow.

## Context payload

The additive `creation_defaults` object on a reconciled context contains:

- source root, connection ID, partition ID, corpus ID and importer-owned catalog path;
- the latest valid upstream release;
- a display name, falling back to the partition ID;
- effective selection policy and execution options;
- resolved representation/profile identity and fingerprint, read-only in the UI;
- the proposed exact target path and its resolved managed output root; and
- provenance for each resolved value (`partition`, `application`, `builtin`, or
  `latest_valid_release`).

The frontend keeps a defensive fallback resolver for older bridges that omit the
new object. This preserves compatibility while ensuring safe required values are
still shown.

## Output precedence and validation

The managed output root is selected in this order:

1. partition/context `output_root` or `managed_output_root`;
2. application `creation_defaults.output_parent`;
3. `<source root>\exports`.

The target shown in the output field is always:

`<output root>\partitions\<partition id>`

The draft and preview carry both `target.path` and
`target.managed_output_root`. The UI and managed adapter validate that pair as a
canonical `partitions/<partition-id>` target. Invalid forms are reported inline;
the importer never silently appends another nested partition path. Existing
legacy records without the additive root field remain readable.

## Interaction and invalidation

The contextual wizard shows its selected partition, readiness/release summary,
source identity, effective profile/device, selection policy, exact target and
import effects. Known values are editable where the current contract allows it.

Changing source root or partition ID clears the readiness and release snapshot,
invalidates the review and requires a fresh managed-source inspection. A
source-root edit also clears the old connection/catalog identity; a partition-only
edit may reuse that connection/catalog to inspect the newly selected partition.
A successful inspection repopulates the selected release and source metadata.
Name, target, selection and execution edits clear only the affected draft/review
state; they do not require a source reinspection.
Representation/profile identity remains read-only.

## Acceptance focus

The change is complete when multiple discovered partitions each expose a correctly
named accessible create action; selecting any one opens a nonblank, correctly
scoped wizard; saved, application and fallback output settings follow the stated
precedence; source identity edits require fresh inspection; preview contains the
release, effective settings, exact target and managed root; existing-target and
stale-preview paths remain non-destructive; and the existing folder-based create
flow remains unchanged.
