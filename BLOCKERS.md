# Blockers

## 2026-08-23 — organization Resource Manager tag unavailable

- Scope: P0 project classification only; this does not block the sandbox probe.
- Required state: bind `environment=Development` as a Google Cloud Resource Manager tag.
- Checked: `gcloud organizations list` returns no organizations for `bkyerv@gmail.com`, and the
  active account's existing project reports no organization/folder parent. Both the installed
  Cloud SDK and current repo-local SDK require TagKeys to have an `organizations/{id}` parent.
- Safe fallback applied by `infra/setup.sh`: create and maintain the project label
  `environment=development`.
- Needed to finish the exact tag requirement: an existing namespaced organization tag value
  (for example `<org-id>/environment/Development`) visible to this account, supplied as
  `CULPRIT_ENVIRONMENT_TAG`. Do not create an organization-level TagKey from this build because it
  is outside the dedicated-project blast radius.

## 2026-08-23 — deliberate runner service-account Token Creator binding (expected)

- Scope: recorded IAM context; this is not a blocker or an intrusion.
- Observed state: the runner service account has
  `roles/iam.serviceAccountTokenCreator` for `user:bkyerv@gmail.com` in addition to the intended
  control-to-runner `roles/iam.serviceAccountUser` binding.
- Intent: the orchestrator added this binding deliberately so the authenticated operator can mint
  runner identity tokens for IAM-protected invocation. It is expected infrastructure, not an
  unexpected concurrent change.
- Safe handling: preserve the exact service-account binding. Do not flag or remove it while the
  gcloud invocation path depends on identity-token minting.

## 2026-08-24 — second-project clean-room spin-up cannot be executed under §0

- Scope: P5 reproducible-deployment verification only; this does not block the existing live app or
  the submission documents.
- Required check: start from a fresh clone, create a different `culprit-xxxxx` Google Cloud project,
  run `infra/setup.sh`, deploy both services, execute the supplier scenario, and open the new UI.
- Blocking condition: Blueprint §0 permits cloud mutation only in the dedicated project
  `culprit-6f973`. Creating or modifying a second project solely to test the README would exceed the
  allowed blast radius.
- Verified within scope: every documented command was traced to the current scripts; shell syntax
  passes; required gcloud 581 flag surfaces exist; the live project was re-described read-only and
  matches the deploy flags, including internal runner ingress, gen2, `sandboxLauncher: true`, OIDC
  Invoker, queue settings, service resources, Firestore, Storage, and Secret Manager.
- Unverified remainder: the new-project portability path, including organization-specific Cloud
  Build service-agent policy. README marks this explicitly and does not claim a clean-room pass.
- Needed to close: the owner or a judge must run the documented commands in an authorized fresh
  project and record the result. Do not create that project from this build unless §0 is changed.

Resolved observations that are not blockers:

- 2026-08-22: Python 3.12 is not installed locally. Python 3.14.4 is available and satisfies
  the project's Python 3.12+ requirement; deployed images will use Python 3.12.
- 2026-08-23: Installing alpha/beta components into a repo-local Cloud SDK copy triggered the
  SDK's optional macOS Python helper, which attempted `sudo` and failed immediately because no
  password was supplied. No system-wide change occurred; no further component installers were
  used. The required Cloud Run proxy binary, when investigated, was unpacked directly under the
  ignored `.deploy/` directory instead.
- 2026-08-23: The first executable P3 triplet had no all-pass branch. It was excluded from the gate,
  and judging now fails closed without exporting a winner when every intervention misses a
  criterion.
- 2026-08-23: Gemini rejected a discriminated intervention-union output schema, and a later
  unconstrained object field arrived as `{}`. AnalystAgent now emits a flat schema whose JSON
  replacement string is parsed and validated against the existing exact intervention models.
- 2026-08-23: The base `google-adk` install lacked evaluation runtime dependencies. The runner now
  installs the official `google-adk[eval]` extra; both `adk eval` and the generated
  `AgentEvaluator.evaluate(...)` pytest pass on the exported P3 artifact.
- 2026-08-23: The first control revision failed startup because its installed Python package could
  not find the sibling static directory. The container now supplies an explicit in-image web path;
  revision `culprit-control-00004-g7v` is ready and serving 100% traffic.
- 2026-08-23: The preconfigured Chrome debugging port was initially idle. P4 visual verification
  used a temporary headless Chrome profile under the repository's ignored `.deploy/` directory;
  no user browser profile or global configuration was changed.
