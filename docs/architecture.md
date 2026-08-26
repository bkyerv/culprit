# Architecture

Culprit has two Cloud Run services separated by a hard trust boundary. The public control service
can read evidence and enqueue work, but it contains no sandbox driver and never executes subject
code. The internal runner holds the ADK agent process and drives credential-free Cloud Run
sandboxes. A detached sandbox remains local to one runner instance, so each run or branch finishes
inside one request.

```mermaid
flowchart TB
  USER["Browser · Culprit UI"]

  subgraph PUBLIC["PUBLIC TRUSTED CONTROL PLANE · no subject code execution"]
    CONTROL["culprit-control · Cloud Run\nFastAPI · REST + SSE · Basic Auth"]
    SECRET["Secret Manager\nculprit-basic-auth"]
  end

  subgraph STATE["MANAGED STATE AND DELIVERY"]
    TASKS["Cloud Tasks\n3-way fan-out · retry/rate policy"]
    FIRESTORE[("Firestore\nruns · events · effects · grades")]
    STORAGE[("Cloud Storage\ncheckpoints · artifacts · evalsets")]
  end

  subgraph EXECUTION["INTERNAL EXECUTION PLANE · Cloud Run internal ingress"]
    RUNNER["culprit-runner · Cloud Run gen2\nGoogle ADK fleet · effect broker"]
    VERTEX["Vertex AI\ngemini-3.7-flash · global"]
    SANDBOX["Cloud Run sandbox\nworkspace + tool execution"]
    DENIED["DENIED\nnetwork egress · environment · metadata server"]
  end

  subgraph SUPPLY["BUILD SUPPLY CHAIN"]
    BUILD["Cloud Build"]
    AR["Artifact Registry\ncontrol + runner images"]
  end

  USER -->|"HTTPS · Basic Auth · REST/SSE"| CONTROL
  SECRET -->|"credential version"| CONTROL
  CONTROL -->|"read evidence"| FIRESTORE
  CONTROL -->|"read evalsets/artifacts"| STORAGE
  CONTROL -->|"enqueue only"| TASKS

  TASKS -->|"OIDC as runner service account"| RUNNER
  RUNNER -->|"ADK model calls"| VERTEX
  RUNNER -->|"events · effects · grades"| FIRESTORE
  RUNNER -->|"checkpoint tars · evalsets"| STORAGE
  RUNNER -->|"sandbox CLI · no credentials cross"| SANDBOX
  SANDBOX -. blocked .-> DENIED

  BUILD -->|"push"| AR
  AR -->|"deploy image"| CONTROL
  AR -->|"deploy image"| RUNNER
```

The same diagram is committed as [a rendered SVG](architecture.svg) for viewers that do not render
Mermaid.

## Trust boundary

| Surface | Reachable from | Authority | Explicitly cannot do |
|---|---|---|---|
| `culprit-control` | Public internet after application Basic Auth | Read Firestore/GCS, read its auth secret, enqueue Cloud Tasks | Import the sandbox driver, launch a sandbox, or execute subject code |
| `culprit-runner` | Internal Cloud Run ingress through Cloud Tasks OIDC | Call Vertex AI, persist evidence, drive local sandboxes | Accept an unauthenticated public request |
| Cloud Run sandbox | Runner's sandbox CLI only | Read/write its imported ephemeral workspace and run allowed tools | Reach the network, inherit host environment variables, query the metadata server, or receive cloud credentials |

The effect broker runs in the runner host, not inside the sandbox. All MVP effects are
`simulate`/`replay`; there is no real sender or payment executor.

