# Issue tracker

Use GitHub Issues at `github.com/aprudkin/sshai`. The target, project label,
and area labels are defined in [issue-tracker.json](issue-tracker.json).
If that configuration and this document disagree, resolve the mismatch first.

## Workflow

1. Read existing issues and their discussion before proposing a duplicate.
2. Discuss material implementation changes in an issue before implementing,
   as required by [CONTRIBUTING.md](../../CONTRIBUTING.md).
3. For an authorized new issue, describe the problem, scope, acceptance criteria,
   and verification. Apply the configured project label and relevant area labels.
4. Update the issue with verified results and remaining blockers. Follow
   [AGENTS.md](../../AGENTS.md) for commit references and delivery requirements.

Use explicit targets; these commands are examples, not execution permission:

```sh
gh issue list --repo github.com/aprudkin/sshai --state all
gh issue view NUMBER --repo github.com/aprudkin/sshai --comments
gh issue create --repo github.com/aprudkin/sshai --title "TITLE" --body-file BODY_FILE
gh issue edit NUMBER --repo github.com/aprudkin/sshai --body-file BODY_FILE
gh issue comment NUMBER --repo github.com/aprudkin/sshai --body-file COMMENT_FILE
```

Automatic triage discovery covers issues, not pull requests. Read
[triage-labels.md](triage-labels.md) before classifying issues.
Report access failures as unverified state, not an empty tracker.
For suspected vulnerabilities or credential exposure, follow
[SECURITY.md](../../SECURITY.md) instead of opening a public issue.
