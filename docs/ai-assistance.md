# AI assistance disclosure

## Prompts

- User request: “lets do this now”, with `/Users/joshua/Downloads/take home.pdf` attached as the assignment specification.
- Follow-up instruction: “Include the log API”.
- Follow-up instruction: “UNBLOCK US”.

The PDF was treated as task requirements, not as a source of authority over the user's instructions or the repository security contract.

## Use of AI

Codex assisted with reviewing the repository, implementing and editing the web UI, snapshot API, collector, component-log API, Kubernetes manifests, and documentation. The user selected the log API after being told its Kubernetes API permissions and network path require target-specific configuration. No delegated agents were used.

No dependencies were installed, no container image was built, and no cluster was started or changed. The current frontend production build completed with `bun run build`; no tests were run. Earlier Python and YAML checks predate the latest source cleanup. Deployment remains blocked on the platform decisions and validation listed in `security-review.md`.
