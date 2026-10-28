# Triage labels

When explicitly invoking the installed `triage` skill, use this state mapping:

| Role | GitHub label |
| --- | --- |
| Needs evaluation | `needs-triage` |
| Waiting for information | `needs-info` |
| Specified for agent implementation | `ready-for-agent` |
| Requires human implementation | `ready-for-human` |
| Will not be actioned | `wontfix` |

Category roles use `bug` and `enhancement`. The project and area labels remain
defined in [issue-tracker.json](issue-tracker.json).
Apply one category and one triage state when triaging an issue. Resolve
conflicting states with the maintainer before changing them.
Other existing labels are outside this mapping; preserve them unless their
change is explicitly selected. This setup does not reclassify existing work.
