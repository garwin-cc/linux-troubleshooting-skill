# Changelog

## Unreleased

- Expand skill description trigger words to cover disk full, inode exhaustion, zombie processes, log file growth, and "server not responding" incidents.
- Add `%st` steal time row to Snapshot Interpretation routing table.
- Add cgroup OOM vs host OOM disambiguation to Cross-Domain Traps.
- Add `iotop` overhead caution to Safety Notes.
- Remove duplicate 60-second snapshot command block from First Response Shape.
- Remove orphaned `cat /proc/net/snmp` line from `references/network.md` first commands.
- Add evals 21–22 covering SSH MCP snapshot workflow and `sudo_used`/`evidence_gaps` handling.

## Previous

- Add Linux troubleshooting skill entry point and domain references.
- Add memory troubleshooting coverage for PSS, `smem`, cgroup file cache, Page Cache reclaim evidence, swap, slab, and OOM branches.
- Add an MVP read-only SSH diagnostics MCP server with host allowlist config, predefined diagnostic bundles, and live-host skill guidance.
- Enhance SSH diagnostics with automatic fallback commands, structured reports, next-bundle suggestions, and explicit timeout/truncation metadata.
- Add production controls for config-gated sudo, JSONL audit metadata, per-host labels, response redaction, multi-host comparison, and Kubernetes node/pod/cgroup mapping.
- Add platform probing, platform-aware evidence gaps, interpretation notes, and manual runbook fallbacks for minimal distributions, containers, cgroup v1/v2, and missing sysstat/iproute2 tools.
- Add eval prompts for CPU, memory, IO, network, Kubernetes, TLS, DNS, NFS, conntrack, and backup/cache interference scenarios.
