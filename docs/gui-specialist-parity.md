# Modern GUI specialist-workflow disposition

This inventory records where the existing desktop actions live in the modern
manager. It is deliberately explicit about operations that remain CLI-only;
those operations are not represented by non-functional GUI buttons.

| Existing capability | Modern location | Disposition |
| --- | --- | --- |
| Open/register an existing export | Databases → Add existing | Implemented; inspection is read-only and registration preserves identity. |
| Generate a new database | Databases → Create database | Implemented as the three-step create flow. |
| Update an existing database | Library → Update | Implemented as check → review → Apply update. |
| Reconcile / mirror removals | Database → Maintenance | Implemented as an explicit exact-record removal review; ordinary Update retains missing records. |
| Rebuild an existing folder export | Database → Maintenance → Review rebuild | Implemented for folder sources through staged replacement; managed rebuild is blocked until a producer release exists. |
| Speaker and episode selection | Database → Content | Implemented with global rules, episode exclusions/overrides, search, and shared-context explanation. |
| Collection and representation details | Database → Overview / History / Settings | Implemented as read-only identity and active-version details. |
| Source discovery and readiness | Advanced tools → Source connections | Implemented with owned source connections, context/release inventory, inspect and prepare actions; preparation remains review-first. |
| GPU/CUDA diagnosis | Settings & help → Environment | Implemented as explicit diagnosis plus a backend-reviewed supported-launcher repair job and copyable fallback; no install occurs on page load. |
| Legacy-state migration proposal | Settings & help → Legacy-state migration | Implemented as read-only, field-scoped proposals; acceptance/repair remains the documented CLI path. |
| Semantic redundancy analysis | Advanced tools → Redundancy analysis · semantic judge | Implemented as scoped coverage/report actions over validated exports, with visible per-context judge configuration, loopback endpoint probing, bounded policy controls, frozen pilot review, confirmed execution, and live durable progress feedback. |
| Release promotion, rollback, and prune | History / release administration | CLI-only where the existing adapter lacks a verifiable exact-plan contract; no GUI action claims unsupported completion. |
| Settings import/export | Settings & help | Implemented for scoped application-default transfers with field-level review/apply; runtime configuration and secrets remain excluded. |
| Standalone validation and specialist reports | Advanced CLI | Existing validated commands remain available; Activity can export durable job reports through the bridge. |

The root bootstrap launches Modern by default, while the explicit `-Ui Legacy`
path remains available as a fallback during the remaining native acceptance
checks.
