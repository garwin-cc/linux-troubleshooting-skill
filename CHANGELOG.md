# Changelog

## Unreleased

- Add Linux troubleshooting skill entry point and domain references.
- Add memory troubleshooting coverage for PSS, `smem`, cgroup file cache, Page Cache reclaim evidence, swap, slab, and OOM branches.
- Add an MVP read-only SSH diagnostics MCP server with host allowlist config, predefined diagnostic bundles, and live-host skill guidance.
- Enhance SSH diagnostics with automatic fallback commands, structured reports, next-bundle suggestions, and explicit timeout/truncation metadata.
- Add production controls for config-gated sudo, JSONL audit metadata, per-host labels, response redaction, multi-host comparison, and Kubernetes node/pod/cgroup mapping.
- Add platform probing, platform-aware evidence gaps, interpretation notes, and manual runbook fallbacks for minimal distributions, containers, cgroup v1/v2, and missing sysstat/iproute2 tools.
- Add eval prompts for CPU, memory, IO, network, Kubernetes, TLS, DNS, NFS, conntrack, and backup/cache interference scenarios.
