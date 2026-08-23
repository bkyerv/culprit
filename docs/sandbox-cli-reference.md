# Cloud Run sandbox CLI — P0 runtime reference

This is the ground-truth command surface captured from
`/usr/local/gcp/bin/sandbox` in Cloud Run revision `culprit-runner-00001-npc` on
2026-08-23. The complete raw help output and command results are preserved in
[`p0-probe-report.json`](./p0-probe-report.json), request
`ebf27e8f2c6e4749a6b2cfa249871b53`.

All six required help commands exited 0. The service configuration was independently exported and
confirmed to contain both `sandboxLauncher: true` and
`run.googleapis.com/execution-environment: gen2`.

## Top-level command

```text
sandbox [command]
```

Commands: `completion`, `delete`, `do`, `exec`, `fork`, `help`, `run`, and `tar`.
The only top-level flag is `-h, --help`.

## `sandbox run`

```text
sandbox run <sandbox-id> [command-to-execute] [flags]
```

| Flag | Meaning observed in help |
|---|---|
| `--allow-egress` | Allow sandbox egress. |
| `--detach` | Detach the sandbox from the console. |
| `-e, --env string` | Set environment variables. |
| `-h, --help` | Show help. |
| `--import-tar string` | Import `rootfs-upper` from a tarball. |
| `--mount string` | Configure mounts. |
| `-p, --publish string` | Expose ports. |
| `--rootfs string` | Select the root filesystem; default `/`. |
| `--stderr`, `--stdin`, `--stdout` | Wire the respective command pipe; each defaults true. |
| `--template-var string` | Set a sandbox template variable as `KEY=VALUE`. |
| `-w, --workdir string` | Select the command working directory. |
| `--write` | Make mounted filesystems writable. |

The help states that omitting a command starts an empty sandbox. The verified P0 probe used an
explicit long-lived command so A and B remained alive for `exec` and `tar`:

```bash
sandbox run "$NAME" --detach --write -- /bin/sleep 300
sandbox run "$NAME" --detach --write --import-tar=/tmp/seed.tar -- /bin/sleep 300
```

Both forms exited 0. Flags after the sandbox ID and the `--` command separator are accepted.
Whether the separator is optional was not tested.

## `sandbox exec`

```text
sandbox exec <sandbox-id> <command-to-execute> [args...] [flags]
```

Flags: `-e, --env string`, `-h, --help`, `--stderr`, `--stdin`, `--stdout`, and
`-w, --workdir string`. The three pipe flags default true.

Verified form:

```bash
sandbox exec "$NAME" -- /usr/local/bin/python -c '<program>'
```

The probe used this form to write and read `/work/probe.txt` in both sandboxes; every invocation
exited 0.

## `sandbox tar`

```text
sandbox tar <sandbox-id> [flags]
```

Flags: `--file string`, `-h, --help`, `--stderr`, `--stdin`, and `--stdout`. The three pipe flags
default true.

Verified form:

```bash
sandbox tar "$NAME" --file=/tmp/checkpoint.tar
```

It exited 0 and produced a 633,856-byte tar. The trusted runner uploaded it to GCS, downloaded it
to a distinct local path, and imported those exact bytes into sandbox B.

## `sandbox do`

```text
sandbox do [flags] [command-to-execute]
```

| Flag | Meaning observed in help |
|---|---|
| `--allow-egress` | Allow sandbox egress. |
| `-e, --env string` | Set environment variables. |
| `--export-tar string` | Export `rootfs-upper` on exit. |
| `-h, --help` | Show help. |
| `--import-tar string` | Import `rootfs-upper` from a tarball. |
| `--mount string` | Configure mounts. |
| `-p, --publish string` | Expose ports. |
| `--rootfs string` | Select the root filesystem; default `/` and read-only. |
| `--sandbox-name string` | Supply an ID instead of a generated one. |
| `--stderr`, `--stdin`, `--stdout` | Wire the respective command pipe; each defaults true. |
| `--sync-tar string` | Import a tar if present and export it on exit. |
| `--template-var string` | Set a sandbox template variable as `KEY=VALUE`. |
| `-w, --workdir string` | Select the command working directory. |
| `--write` | Make mounted filesystems writable. |

P0 verified this help surface but deliberately did not execute `sandbox do`; checkpoint/restore was
proved with named detached sandboxes instead.

## `sandbox delete`

```text
sandbox delete <sandbox-id> [flags]
```

Flags: `--force`, `-h, --help`, `--stderr`, `--stdin`, and `--stdout`. The help says `--force`
deletes a running sandbox.

Runtime caveat: in this revision, `sandbox delete NAME --force` removed each running sandbox but
did not return within the probe's 120-second subprocess limit. A following plain
`sandbox delete NAME` immediately exited 1 with `file does not exist`, proving the resource had
already been removed. The round-trip passed; cleanup timing is recorded separately and is not
silently represented as a successful command exit.

## Verified P0 round-trip

- A and B used different IDs.
- Source, A-read, and B-read payloads were each 117 bytes with SHA-256
  `82dd5c3f3d2ce68928d3296da4ee55292e6d993c1afd1dfe9668cd58740738f9`.
- Exported and GCS-downloaded tars were each 633,856 bytes with SHA-256
  `149e446e741d302a5a4095ae28010a3b29434391d25e87f2a1c01a1896aaa02f`.
- Evidence tar:
  `gs://culprit-6f973-state/tmp/p0-probes/ebf27e8f2c6e4749a6b2cfa249871b53/sandbox-a.tar`.
