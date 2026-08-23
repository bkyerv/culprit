# Cloud Run sandbox launcher and CLI research

Research date: 2026-08-23. Scope: first-party Google Cloud documentation and Google-owned source code, plus read-only inspection of the Cloud SDK installed on this machine. No Cloud Run resource was created or changed for this research.

## Authority and confidence

This note separates two kinds of evidence:

- **Document-derived:** current first-party documentation or Google-owned source says the behavior exists.
- **Runtime verification required:** the deployed P0 `/probe` must establish the actual binary, accepted argument ordering, behavior, and output in the project's revision. Cloud Run sandboxes are Preview, so the deployed binary is the final authority.

Nothing below claims that the P0 round-trip has succeeded.

## Deployment mechanism

### Current documented path

**Document-derived.** Cloud Run sandboxes are a Preview feature for services, jobs, and worker pools. For a service, Google documents:

```bash
gcloud beta run deploy SERVICE \
  --image IMAGE_URL \
  --execution-environment=gen2 \
  --sandbox-launcher
```

Google's service configuration page says sandboxes run in the second-generation environment, share the host instance's CPU and memory, and are enabled with `gcloud beta run deploy ... --sandbox-launcher` or the equivalent YAML. The code-execution guide says enabling the feature mounts the binary at `/usr/local/gcp/bin/sandbox`. Sources: [Configure sandboxes for services](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes), [Code execution in Cloud Run](https://docs.cloud.google.com/run/docs/code-execution).

The service configuration page says enabling sandboxes causes the service to deploy in gen2. Culprit should still pass `--execution-environment=gen2` explicitly, as required by the blueprint, and verify the resulting revision.

### What `--sandbox-launcher` represents in service YAML

**Document-derived.** The launcher switch is a **container field**, not a `run.googleapis.com/sandbox-launcher` annotation. Preview use also requires the `run.googleapis.com/launch-stage: BETA` service annotation. An explicit gen2 selection is a revision-template annotation:

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: culprit-runner
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/execution-environment: gen2
    spec:
      containers:
        - name: culprit-runner
          image: IMAGE_URL
          sandboxLauncher: true
          ports:
            - containerPort: 8080
```

Google's configuration guide shows `sandboxLauncher: true` inside the container and deploys it with `gcloud run services replace service.yaml`. The Cloud Run v1 YAML reference independently places `sandboxLauncher` on each container and places execution environment under the revision-template annotation. Sources: [service sandbox YAML procedure](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes#yaml), [Cloud Run v1 YAML reference](https://docs.cloud.google.com/run/docs/reference/yaml/v1).

`run.googleapis.com/launch-stage: BETA` only selects the Preview launch stage. It is **not** a substitute for `sandboxLauncher: true`.

### Compatibility finding for this machine's older Cloud SDK

**Locally observed, no cloud mutation.** This machine currently has Cloud SDK `554.0.0` (core dated 2026-01-23). The beta component is not installed. Google added `--sandbox-launcher` for `gcloud beta run deploy` and `gcloud beta run services update` in Cloud SDK `575.0.0` on 2026-06-30. Source: [Google Cloud CLI 575.0.0 release notes](https://docs.cloud.google.com/sdk/docs/release-notes#57500_2026-06-30).

Read-only checks found:

- `gcloud run deploy --help` has no launcher flag.
- `gcloud beta run ...` prompts to install the old beta component and was not installed.
- The SDK's packaged Cloud Run v1 message schema contains no `sandboxLauncher` field.
- Passing the documented YAML object to that local schema's `DictToMessageWithErrorCheck` fails with `DecodeError: Service.spec.template.spec.containers[0].sandboxLauncher`.

Therefore the YAML shape above explains the API representation, but **this installed 554.0.0 CLI cannot be assumed to transmit it with `gcloud run services replace`**. The launch-stage annotation alone will not enable the launcher. A launcher deployment needs a current Cloud SDK (at least 575.0.0 for the service flag) or another path that sends the current Cloud Run v1 container schema. Do not update or install components into the existing SDK because it is outside the repository blast radius.

**Runtime verification required.** After deployment, inspect the service/revision export for both:

```yaml
run.googleapis.com/execution-environment: gen2
sandboxLauncher: true
```

Then make `/probe` verify that `/usr/local/gcp/bin/sandbox` exists and is executable. Google explicitly recommends checking `sandboxLauncher: true` in the described service configuration. Source: [View sandbox settings](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes#view).

## Current documented sandbox CLI

The following is a compact transcription of the first-party [Cloud Run sandbox CLI reference](https://docs.cloud.google.com/run/docs/reference/sandbox-cli), last updated there on 2026-07-22. It is **document-derived**, not the P0 ground-truth dump.

### `sandbox`

```text
sandbox
  -h, --help
```

The reference defines `<sandbox-id>` as either a generated unique ID or a user-supplied name validated for uniqueness. The top-level reference also documents `fork`, although P0 only requires help capture for `run`, `exec`, `tar`, `do`, and `delete`.

### `sandbox run`

Creates and starts a named sandbox.

```text
sandbox run <sandbox-id> [command-to-execute] [flags]

  --allow-egress
  --detach
  -e, --env string
  -h, --help
  --import-tar string
  --mount string
  --rootfs string       (default "/")
  -w, --workdir string
  --write
```

### `sandbox exec`

Executes a command inside an existing running sandbox.

```text
sandbox exec <sandbox-id> <command-to-execute> [flags]

  -e, --env string
  -h, --help
  -w, --workdir string
```

### `sandbox tar`

Exports a tar archive containing the running sandbox's writable overlay filesystem.

```text
sandbox tar <sandbox-id> [flags]

  --file string
  -h, --help
```

### `sandbox do`

Creates a new sandbox, runs one command, and destroys the sandbox after exit.

```text
sandbox do [flags] [command-to-execute]

  --allow-egress
  -e, --env string
  --export-tar string
  -h, --help
  --import-tar string
  --mount string
  --rootfs string       (default "/", read-only by default)
  --sandbox-name string
  --sync-tar string
  -w, --workdir string
  --write
```

### `sandbox delete`

Removes a sandbox and cleans up its resources.

```text
sandbox delete <sandbox-id> [flags]

  --force
  -h, --help
```

## Documented behavior relevant to the P0 round-trip

**Document-derived.** The code-execution guide establishes all of the following:

- The CLI is available only inside a Cloud Run resource after sandboxes are enabled.
- The sandbox does not inherit host environment variables. Commands should use absolute executable paths or explicitly provide `PATH`.
- Egress is denied by default and enabled per sandbox with `--allow-egress`.
- The host root filesystem is read-only by default inside the sandbox; `--write` adds a temporary writable overlay.
- `sandbox tar NAME --file=PATH` exports the writable overlay of a running sandbox.
- `--import-tar` imports an existing standard tar into a new sandbox before execution. The guide demonstrates this for separate `sandbox do` calls; the CLI reference also documents `--import-tar` on `sandbox run`.
- Ephemeral overlay data not exported before sandbox termination is permanently deleted.
- A detached sandbox is reused with `sandbox exec` and must remain alive. Google's example supplies a long-running command to `sandbox run ... --detach`.

Source: [Code execution in Cloud Run: persistence, detached execution, snapshots, and filesystem behavior](https://docs.cloud.google.com/run/docs/code-execution).

Google's page says tar state persists across executions **within the same Cloud Run instance**, and the service configuration page says sandboxes run within the same instance as the host container. This supports the blueprint's requirement that the complete A-to-GCS-to-B round-trip happen within one `/probe` request on one runner instance. It does not establish cross-request sandbox identity.

GCS upload/download should be done by the trusted runner process, not from inside the sandbox: export A to a host-visible local tar path, upload that object, download the object to a different host path, and pass the downloaded local path to B's `--import-tar`. The CLI reference describes `--import-tar` as a filesystem path, not a `gs://` URI.

## Candidate probe sequence (still unverified)

The documentation suggests this maximum-information sequence. The deployed help output must be collected before relying on it:

```bash
SANDBOX=/usr/local/gcp/bin/sandbox

"$SANDBOX" run sandbox-a --write --detach -- /bin/sleep 600
"$SANDBOX" exec sandbox-a -- /bin/sh -c \
  'mkdir -p /work && printf %s "culprit-p0-known-content" > /work/probe.txt'
"$SANDBOX" tar sandbox-a --file=/tmp/sandbox-a.tar

# Trusted runner: upload /tmp/sandbox-a.tar to GCS, then download that exact
# object to a distinct host path such as /tmp/sandbox-b-import.tar.

"$SANDBOX" run sandbox-b --write --import-tar=/tmp/sandbox-b-import.tar \
  --detach -- /bin/sleep 600
"$SANDBOX" exec sandbox-b -- /bin/cat /work/probe.txt

"$SANDBOX" delete sandbox-a --force
"$SANDBOX" delete sandbox-b --force
```

Why include `/bin/sleep`: the official detached example runs a long-lived process. The reference makes the command optional, but does not guarantee how long a detached sandbox without a command remains running.

Why use distinct local paths: it makes the report prove that B was seeded from the bytes downloaded from the GCS object rather than accidentally reusing A's in-memory path.

The P0 report should compare SHA-256 and byte length at four points: source content, content read from A, exported/uploaded/downloaded tar bytes, and content read from B. The docs establish the mechanism, not byte identity; identity is the gate the probe must prove.

## Items the deployed `/probe` must settle

1. Exact stdout, stderr, and exit code for `sandbox --help` and each required subcommand's `--help`.
2. Whether `--help` is accepted before/after the subcommand and whether command flags can appear before/after `<sandbox-id>`.
3. Whether `--` is required, optional, or rejected before the executed command.
4. Whether a detached `run` needs an explicit long-running command to stay available for later `exec` and `tar`.
5. Exact `--import-tar` semantics for a tar produced by `sandbox tar` from a different sandbox ID.
6. Whether `delete` needs `--force` for the chosen long-running command and what happens during cleanup after an earlier failure.
7. Whether the tar is immediately complete and readable when `sandbox tar` exits.
8. Whether the deployed binary's help differs from the July 2026 reference, including undocumented commands or renamed flags.

## First-party source-code corroboration

Google ADK's current `CloudRunSandboxCodeExecutor` source defaults the binary to `/usr/local/gcp/bin/sandbox`, invokes one-shot `sandbox do`, leaves egress disabled unless requested, uses an absolute Python path because sandbox `PATH` can be empty, declares itself non-stateful, and always returns an empty output-file list. This corroborates the binary location and one-shot syntax but does not cover named `run`/`exec`/`tar` checkpointing. Source: [Google ADK Cloud Run sandbox executor](https://github.com/google/adk-python/blob/main/src/google/adk/integrations/cloud_run/_cloud_run_sandbox_code_executor.py).
