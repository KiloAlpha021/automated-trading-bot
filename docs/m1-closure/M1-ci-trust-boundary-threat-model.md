# M1 hosted CI trust-boundary finding

Finding ID: `M1-AUDIT-CI-SELF-MODIFICATION`

Audited revision: `4a4bfb6879624b1f78cff69b06bba56a8707e66f`

Mapped atom: `IMP-001-M1-14`

Status: **OPEN — M1 REOPENED / BLOCKED**

Authorization: **NONE**

This is post-closure audit evidence. It does not authorize Stage 2, change any
financial authority, or claim that hosted enforcement has been repaired.

## Confirmed attack

The protected branch requires the GitHub Actions check
`m1-engineering-foundation`. The workflow runs on `pull_request`, so GitHub
evaluates the workflow definition from the pull-request revision. A contributor
with repository write access can make one pull request that:

1. keeps the workflow and job name `m1-engineering-foundation`;
2. replaces the canonical bootstrap with a trivial successful step;
3. receives a successful check from the same GitHub Actions app identity; and
4. satisfies the current required-check rule.

The current policy requires zero approving reviews and has no independently
hosted rule protecting the workflow path. The required evaluator can therefore
approve a change that weakened that evaluator. Repository tests cannot close
this boundary because the same pull request can modify or remove those tests.

The attack was modeled by applying those changes to an in-memory copy of the
workflow contract. The existing repository validator rejects the weakened
content, proving the local control, while the hosted merge model has no
independent rule requiring that validator to remain unchanged.

## Control options

| Option | Independent of PR code? | Single-owner practicality | Disposition |
|---|---:|---:|---|
| Repository push/ruleset restricting the workflow path, with no ordinary-contributor bypass | Yes | Good if the owner is the only explicit bypass actor | Preferred, subject to current GitHub plan/API support |
| CODEOWNERS plus required code-owner review for workflow changes | Yes | Poor with one owner because authors cannot approve their own pull requests | Valid only with a separate eligible reviewer |
| Ruleset requiring approval for protected workflow-path changes | Yes | Depends on plan and an independent reviewer | Valid if GitHub exposes and enforces it |
| Trusted reusable workflow | Only if caller/ref and trusted workflow are independently protected | Moderate | Insufficient by itself |
| Organization-level required workflow/ruleset | Yes | Good where the account and plan expose it | Preferred when available |
| One approval for every pull request | Yes | Poor and potentially meaningless in a single-owner repository | Not selected without a real independent reviewer |

## Required hosted resolution

Configure a GitHub-hosted rule that prevents ordinary contributors from
modifying `.github/workflows/m1-engineering-foundation.yml`, or requires an
independent authorized approval for such a modification. The rule must be
outside pull-request-controlled repository content. Its bypass actors must be
explicit and limited, and a disposable pull request must demonstrate that a
trivial same-name replacement job cannot satisfy the merge gate.

Until attributable live evidence and that negative hosted proof exist,
`IMP-001-M1-14` remains `INSUFFICIENT_EVIDENCE` and M1 remains blocked.

## Local defense in depth

The workflow declares `contents: read`, pins each action to a reviewed immutable
commit with its release identity in a comment, retains full history checkout,
and continues to invoke the canonical bootstrap. Adversarial repository tests
reject missing/broadened permissions, floating action references, changed check
identity, shallow checkout, ignored failures, weakened bootstrap invocation and
removed engineering/security controls. These checks are defense in depth only;
they are not the independent hosted enforcement required above.
