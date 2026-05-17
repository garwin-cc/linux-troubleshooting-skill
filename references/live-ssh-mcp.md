# Live SSH MCP Reference

Use this reference when an MCP server named `linux-ssh-diagnostics` or equivalent SSH diagnostics tools are available and the user asks to connect to a host, inspect a server, or automatically locate a Linux performance problem on a live machine.

## Safety Boundary

The MVP is observation-only. Do not run automatic fixes.

Allowed actions:

- List configured hosts.
- Run predefined read-only diagnostic bundles.
- Interpret outputs and choose the next read-only bundle.
- Propose risky commands as recommendations only.

Do not run:

- service restarts, `kill`, `reboot`, or process termination
- `sysctl -w`, cache drops, qdisc changes, firewall changes, conntrack deletion
- writes to `/proc`, `/sys`, config files, or service units
- broad packet capture or long-running tracing without explicit user approval and a separate tool path

## MVP Tools

Expected tools:

- `ssh_list_hosts`: list allowed host aliases and available bundles.
- `ssh_run_bundle`: run one read-only bundle on one allowed host.
- `ssh_compare_hosts`: compare one read-only bundle across selected hosts or labels.
- `ssh_k8s_map`: collect Kubernetes node/pod/cgroup mapping evidence from one configured node.

Each `ssh_run_bundle` result should include:

- `diagnostic_report.summary`: short machine-generated interpretation.
- `diagnostic_report.platform_profile`: distro, version, kernel, init, cgroup mode, container marker, available tools, and missing core tools.
- `diagnostic_report.evidence_gaps`: missing-tool fallback impact.
- `diagnostic_report.interpretation_notes`: platform-specific caveats for cgroup, container, Alpine/BusyBox, and log visibility.
- `diagnostic_report.signals`: extracted CPU, memory, IO, network, and collection signals.
- `diagnostic_report.next_read_only_bundles`: suggested next bundles, not fixes.
- `diagnostic_report.command_health`: failed, timed-out, truncated, and fallback-used command IDs.
- `commands[].attempts`: primary and fallback command attempts for auditability.
- `commands[].sudo_used`: true only when config-gated `sudo -n` was used for a read-only command.

Expected bundles:

- `platform_probe`: distro, kernel, init/logging, cgroup mode, container markers, and tool inventory.
- `snapshot_60s`: first-pass routing snapshot.
- `cpu_basic`: CPU, scheduler, run queue, and context switch evidence.
- `memory_basic`: memory pressure, RSS, slab, OOM, and PSI evidence.
- `io_basic`: iowait, block latency, filesystem, and kernel IO logs.
- `network_basic`: sockets, packet counters, TCP counters, softnet, and interface stats.
- `container_cgroup_basic`: cgroup CPU, memory, and IO files.
- `k8s_node_pod_cgroup`: Kubernetes node-side pod directory, runtime, kubelet log, and cgroup mapping evidence.
- `logs_oom_io_network`: kernel logs for OOM, blocked tasks, IO, network, and conntrack signals.

## Production Controls

- `sudo` is deny-by-default and must be enabled in config. Use it only for read-only command IDs such as kernel logs and cgroup reads.
- Audit logs should contain command IDs, hashes, duration, exit state, fallback, sudo, timeout, and truncation metadata. Do not store raw stdout/stderr by default.
- Redaction should stay enabled for production. Preserve repeated-value identity with stable placeholders so comparisons still work.
- Prefer labels such as `env=prod`, `role=api`, `cluster=prod-a`, and `az=az-a` for multi-host comparison.
- For Kubernetes symptoms, map pod -> node -> pod UID -> cgroup path before concluding whether pressure is process-local, cgroup-limited, or node-level.

## Workflow

1. Identify the host. If the user did not provide one, call `ssh_list_hosts` or ask for the target alias.
2. Tell the user the first step is a read-only snapshot.
3. Run `ssh_run_bundle` with `snapshot_60s`.
4. Interpret the output before collecting more data.
5. Choose the next bundle based on evidence:
   - load high + `vmstat b` or `%wa`: `io_basic`
   - load high + `vmstat r` or CPU hot: `cpu_basic`
   - low `MemAvailable`, swap, slab, or OOM: `memory_basic`
   - retransmits, drops, softirq, or socket pressure: `network_basic`
   - host healthy but container symptoms: `container_cgroup_basic`
   - suspicious kernel errors: `logs_oom_io_network`
6. Stop once the leading branch is clear enough to explain the likely bottleneck and next validation.

## Report Shape

````markdown
## Initial Diagnosis
[Leading hypothesis and confidence.]

## Evidence
- `[host] command`: field/value -> interpretation

## Branch Taken
CPU / Memory / IO / Network / Cgroup

## Next Read-Only Check
```bash
[focused command or bundle name]
```

## Risky Actions Not Run
- [restart/tuning/capture/etc. that would need explicit approval]

## Fix Direction
- Immediate mitigation:
- Durable fix:
- Monitoring to add:
````

## Missing Command Handling

If a command is missing, do not block. Interpret the available outputs and use fallbacks:

- missing `mpstat`, `pidstat`, `iostat`, or `sar`: use `top`, `ps`, `/proc/stat`, `/proc/meminfo`, `/proc/diskstats`, `/proc/net/*`, `ss`, `ip -s link`, and kernel logs.
- permission denied for `dmesg` or cgroup files: try configured read-only sudo if allowed; otherwise say which evidence is missing and ask for node-level or elevated read access if needed.
- if `command_health.timed_out` or `command_health.truncated` is non-empty, mark the diagnosis as partial and prefer a narrower read-only bundle before concluding.
- if `evidence_gaps` is non-empty, explain how the fallback changes confidence before making a root-cause claim.
