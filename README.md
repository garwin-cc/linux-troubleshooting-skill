# linux-troubleshooting

Diagnose Linux production problems end to end. Use this skill for service slowness, high load, CPU saturation, memory pressure, IO or disk latency, network latency, timeouts, OOM, iowait, packet loss, retransmission, container resource pressure, Kubernetes incidents, disk full, inode exhaustion, zombie processes, log file growth, or "server not responding" incidents.

## Structure

- `SKILL.md` defines the trigger description, first-response shape, 60-second snapshot, and top-level routing.
- `references/cpu.md` covers CPU saturation, scheduler pressure, softirq, context switching, steal time, and cgroup CPU throttling.
- `references/memory.md` covers `MemAvailable`, Page Cache, RSS/PSS, `smem`, cgroup file cache, swap, slab, OOM, and container limits.
- `references/io*.md` covers disk latency, filesystem, NFS, cloud volumes, cgroups, dirty writeback, and tracing.
- `references/network*.md` covers TCP queues, retransmits, DNS, HTTP, TLS, routing, MTU, CNI, conntrack, NAT, and NIC pressure.
- `references/live-ssh-mcp.md` covers the read-only live-host MCP workflow.
- `scripts/mcp_ssh_diagnostics.py` provides an MVP stdio MCP server for SSH diagnostic bundles.
- `examples/ssh-hosts.example.json` shows the allowlist-style SSH host config.
- `evals/evals.json` contains regression prompts.

## Install

For Codex, copy this repository directory into your Codex skills directory:

```bash
cp -R linux-troubleshooting ~/.codex/skills/
```

Or install directly from GitHub with `npx`:

```bash
npx degit garwin-cc/linux-troubleshooting-skill ~/.codex/skills/linux-troubleshooting
```

If your Codex environment supports slash-command skill installation, you can also use:

```text
/skill install https://github.com/garwin-cc/linux-troubleshooting-skill
```

For Claude Code, copy it into your Claude skills directory:

```bash
cp -R linux-troubleshooting ~/.claude/skills/
```

Or install directly from GitHub with `npx`:

```bash
npx degit garwin-cc/linux-troubleshooting-skill ~/.claude/skills/linux-troubleshooting
```

Or use Claude Code's slash command:

```text
/skill install https://github.com/garwin-cc/linux-troubleshooting-skill
```

Then restart or reload the agent runtime so the skill metadata is discovered.

## Example Prompts

```text
free shows high used memory and high buff/cache, and the service has a memory alert. How do I tell whether this is a memory leak?
```

```text
An API has intermittent timeouts. sar shows TCP retransmissions increasing, and CPU softirq is high. Help me troubleshoot it.
```

## Design Principles

- Start with read-only evidence.
- Interpret pasted output before asking for more commands.
- Route to the smallest reference that matches the evidence.
- Distinguish facts from inference.
- Treat restarts, `sysctl -w`, cache drops, firewall changes, qdisc changes, conntrack deletion, disk scheduler changes, and kernel tuning as proposed changes that need evidence and rollback.

## MCP SSH Diagnostics (MVP)

The repository includes an optional stdio MCP server that lets an agent run predefined read-only SSH diagnostic bundles on configured hosts. It does not expose arbitrary remote command execution and does not perform automatic fixes.

The diagnostics path includes:

- automatic fallback commands for common missing tools such as `mpstat`, `pidstat`, `iostat`, `sar`, `ss`, `nstat`, `ip`, and restricted `dmesg`
- automatic `platform_probe` metadata for distro family, kernel, init system, cgroup mode, container markers, and available tools
- a structured `diagnostic_report` with summary, confidence, extracted signals, next read-only bundle suggestions, command health, and safety metadata
- per-command timeout and output truncation fields so incomplete evidence is visible instead of hidden
- optional config-gated `sudo -n` for selected read-only command IDs only
- JSONL audit records with command metadata and hashes instead of raw command output
- per-host labels for filtering, comparison, and production context
- response redaction for secrets and sensitive host details
- multi-host bundle comparison and Kubernetes node/pod/cgroup mapping tools

Create a host allowlist:

```bash
mkdir -p ~/.config/linux-troubleshooting
cp examples/ssh-hosts.example.json ~/.config/linux-troubleshooting/ssh-hosts.json
```

Edit `~/.config/linux-troubleshooting/ssh-hosts.json` with your host aliases, users, ports, and identity files. Then verify the bundles:

```bash
python3 scripts/mcp_ssh_diagnostics.py --list-bundles
```

Example MCP server config:

```json
{
  "mcpServers": {
    "linux-ssh-diagnostics": {
      "command": "python3",
      "args": ["/path/to/linux-troubleshooting/scripts/mcp_ssh_diagnostics.py"],
      "env": {
        "LINUX_TROUBLESHOOTING_SSH_CONFIG": "~/.config/linux-troubleshooting/ssh-hosts.json"
      }
    }
  }
}
```

The MVP tool surface is:

- `ssh_list_hosts`: list configured host aliases and available bundles.
- `ssh_run_bundle`: run one predefined read-only bundle on one configured host.
- `ssh_compare_hosts`: run one bundle across selected hosts or labels and compare structured signals.
- `ssh_k8s_map`: collect node-side pod/cgroup mapping evidence on a configured Kubernetes node.

Start with `snapshot_60s`, interpret the output, then branch to one focused bundle such as `cpu_basic`, `memory_basic`, `io_basic`, `network_basic`, `container_cgroup_basic`, or `logs_oom_io_network`.

Each `ssh_run_bundle` response contains:

- `diagnostic_report`: structured interpretation for agent routing.
- `diagnostic_report.platform_profile`: distro, version, kernel, init, cgroup mode, container status, available tools, and missing core tools.
- `diagnostic_report.evidence_gaps` and `diagnostic_report.interpretation_notes`: fallback impact and platform-specific caveats.
- `commands`: selected command output plus all primary/fallback `attempts`.
- `timed_out`, `stdout_truncated`, `stderr_truncated`, `stdout_bytes`, `stderr_bytes`, and `max_output_bytes` for every command.

Production controls are configured in `ssh-hosts.json`:

- `sudo.enabled`, `sudo.command_ids`, and per-host `sudo` gate non-interactive `sudo -n` usage.
- `audit.enabled` and `audit.path` write JSONL metadata for host, bundle, command IDs, hashes, fallback, sudo, timeout, and truncation state.
- `redaction.enabled` controls response redaction. IPs are redacted by default; domains and paths can be enabled when needed.
- host `labels` can be a list or key/value map such as `env=prod`, `role=api`, `cluster=prod-a`, and `az=az-a`.

## Recent Improvements

**Routing and coverage:**

- Snapshot Interpretation table now includes a `%st` steal time row so hypervisor CPU contention is caught in the first routing pass.
- Cross-Domain Traps now covers the cgroup OOM vs host OOM false positive: a process killed by the OOM killer while `free` shows plenty of host memory is usually a cgroup memory limit, not host-level pressure. Check `dmesg` for `oom_kill_process` and the cgroup path in the log line.
- Skill description expanded to trigger on disk full, inode exhaustion, zombie processes, log file growth, and "server not responding" incidents.

**Safety notes:**

- `iotop` uses kernel taskstats and adds per-process accounting overhead on busy systems; prefer `pidstat -d` for a lower-overhead first pass.

**Content cleanup:**

- First Response Shape no longer duplicates the 60-second snapshot command block; it now references the single canonical block in the 60-Second Snapshot section.
- Removed orphaned `cat /proc/net/snmp` line from `references/network.md` first commands (it was already covered by the `nstat` fallback).

**Evals:**

- Added evals 21 and 22 covering the SSH MCP snapshot workflow (`snapshot_60s` as the mandatory first step) and correct handling of `sudo_used` and `evidence_gaps` fields in `ssh_run_bundle` responses.

**Memory reference guardrails:**

- Use PSS, `smem`, and `smaps_rollup` when RSS may double-count shared pages.
- Separate process anonymous memory from file-backed cache.
- Inspect cgroup `memory.stat` `anon` versus `file` when container memory alerts fire while host memory is healthy.
- Check Page Cache reclaim evidence such as `sar -B`, `/proc/vmstat`, inode steal counters, drop-cache counters, and memory PSI before tuning.

## Evals

Regression prompts live in `evals/evals.json`. A good eval run should compare answers with this skill against a baseline without the skill and review both keyword coverage and qualitative troubleshooting quality.

## License

MIT. See `LICENSE`.
