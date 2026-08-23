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

Resolved observations that are not blockers:

- 2026-08-22: Python 3.12 is not installed locally. Python 3.14.4 is available and satisfies
  the project's Python 3.12+ requirement; deployed images will use Python 3.12.
- 2026-08-23: Installing alpha/beta components into a repo-local Cloud SDK copy triggered the
  SDK's optional macOS Python helper, which attempted `sudo` and failed immediately because no
  password was supplied. No system-wide change occurred; no further component installers were
  used. The required Cloud Run proxy binary, when investigated, was unpacked directly under the
  ignored `.deploy/` directory instead.
