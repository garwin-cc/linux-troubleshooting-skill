---
name: linux-troubleshooting
description: Diagnose Linux production problems end to end. Use this skill whenever the user asks how to locate Linux issues, service slowness, high load, CPU saturation, memory pressure, IO or disk latency, network latency, timeout, OOM, iowait, packet loss, retransmission, container or Kubernetes resource pressure, or wants a structured troubleshooting runbook. Start with a 60-second system snapshot, then branch into CPU, memory, IO, or network evidence instead of guessing.
---

# Linux Troubleshooting

Use this skill to locate Linux problems methodically from symptoms, command output, or a live-host investigation plan. The goal is to turn vague incidents such as service slowness, high load, request timeouts, memory alerts, disk latency, and intermittent failures into evidence, branches, and next actions.

## Source Model

Start with the "Linux performance analysis in 60 seconds" style popularized by Brendan Gregg and the Netflix Tech Blog article "Linux Performance Analysis in 60,000 Milliseconds":

```text
uptime -> dmesg -> vmstat -> mpstat -> pidstat -> iostat -> free -> sar network -> top
```

Then route to the local domain playbooks:

- CPU: `references/cpu.md`
- Memory: `references/memory.md`
- IO and filesystem: `references/io.md`
- Network: `references/network.md`

Load only the reference that matches the evidence. If the broad branch points to a specialized case, load the relevant deep reference:

- IO mapped/cloud/NFS/container storage: `references/io-storage-environments.md`
- IO tracing, eBPF, or block-layer attribution: `references/io-advanced-tools.md`
- IO optimization, dirty writeback, schedulers, mount options, backup interference: `references/io-optimization.md`
- Network packet capture and pcap analysis: `references/network-packet-analysis.md`
- TCP queues, timeouts, TIME_WAIT, idle connections, file descriptors: `references/network-tcp-queues-timeouts.md`
- Routing, ARP/neighbor, rp_filter, firewall, MTU/MSS: `references/network-routing-arp-mtu.md`
- Container, Kubernetes, CNI, overlay, service routing: `references/network-container-k8s.md`
- DNS, HTTP, Nginx, TLS: `references/network-http-tls-dns.md`
- NIC drops, conntrack/NAT, throughput, DDoS, load balancing, ECMP: `references/network-nic-conntrack-performance.md`
- Live SSH MCP investigation: `references/live-ssh-mcp.md`

If multiple resources are implicated, choose the branch that can disprove the largest uncertainty first.

## Operating Rules

- Prefer read-only observation first. Mark restarts, `sysctl -w`, cache drops, firewall changes, qdisc changes, conntrack deletion, disk scheduler changes, and kernel tuning as proposed changes that need evidence and rollback.
- Do not tune before locating the bottleneck. Kernel parameters often move pressure rather than remove it.
- Interpret command output before asking for more commands. When the user pasted data, state the most likely branch and the next command that reduces uncertainty.
- Keep interactive troubleshooting incremental. Give a minimum command set first; expand only when the user asks for a full runbook.
- Distinguish facts from inference. Use phrases such as "evidence points to", "not proven yet", and "this rules out".
- For production, call out intrusive commands: `strace -T`, `perf record`, `tcpdump -s 0`, `conntrack -L`, `blktrace`, broad `find`, full packet capture, and eBPF probes can add overhead or expose sensitive data.
- In containers and Kubernetes, check both host-level symptoms and cgroup or pod limits before concluding the host is overloaded.
- In live SSH MCP mode, run only configured read-only diagnostic bundles. Do not perform automatic fixes.

## Live SSH MCP Mode

Use `references/live-ssh-mcp.md` when the user asks to connect to a configured server or live host and SSH diagnostics MCP tools such as `ssh_list_hosts` and `ssh_run_bundle` are available.

MVP flow:

- Identify the target host alias. If it is not provided, list configured hosts or ask for the alias.
- Run only the read-only `snapshot_60s` bundle first.
- Interpret that output before collecting more data.
- Branch to one focused bundle: `cpu_basic`, `memory_basic`, `io_basic`, `network_basic`, `container_cgroup_basic`, or `logs_oom_io_network`.
- Use `ssh_compare_hosts` when the question is about blast radius, one bad node versus cluster-wide symptoms, or hosts selected by labels.
- Use `ssh_k8s_map` when a Kubernetes pod symptom needs node, pod UID, container, and cgroup correlation.
- Treat `sudo_used` as privileged read-only evidence collection; mention it in the report and never expand it into arbitrary command execution.
- Stop when the leading bottleneck is clear enough to explain the evidence, missing evidence, and next validation.
- Never restart services, kill processes, change sysctl values, drop caches, modify firewall/qdisc/conntrack state, or edit remote files from the MCP path.

If the MCP tools are not available, provide the same commands for the user to run manually instead of implying direct access.

## First Response Shape

If the user gives only symptoms, answer with:

```markdown
Start with a 60-second snapshot to route the incident:

```bash
date; hostname; uptime
(dmesg -T | tail -80) 2>/dev/null || (journalctl -k --no-pager -n 80) 2>/dev/null || tail -80 /var/log/messages 2>/dev/null || tail -80 /var/log/kern.log 2>/dev/null || true
vmstat 1 5
(command -v mpstat >/dev/null && mpstat -P ALL 1 3) || grep '^cpu' /proc/stat | head -40
(command -v pidstat >/dev/null && pidstat -u -d -r -w 1 5) || ps -eo pid,ppid,state,comm,pcpu,pmem,rss,vsz,wchan:24 --sort=-pcpu | head -40
(command -v iostat >/dev/null && iostat -xz 1 5) || cat /proc/diskstats
free -h 2>/dev/null || cat /proc/meminfo
(command -v sar >/dev/null && sar -n DEV,TCP,ETCP 1 5) || (cat /proc/net/dev; cat /proc/net/snmp; cat /proc/net/netstat)
top -bn1 | head -40
```

Routing rules:
- ...
```

If the user gives command output, answer with:

```markdown
## Initial Diagnosis
[Concrete conclusion and confidence.]

## Key Evidence
- [Field/value -> interpretation]

## Next Step
```bash
[one or a few focused commands]
```

## Branches
| If you see | It means | Next step |
|---|---|---|
```

If the user asks for a runbook or checklist, include commands, interpretation, and fix direction for each branch.

## 60-Second Snapshot

Use this command path for a live Linux host. Explain what each command proves when presenting it to a user.

```bash
date; hostname; uptime
(dmesg -T | tail -80) 2>/dev/null || (journalctl -k --no-pager -n 80) 2>/dev/null || tail -80 /var/log/messages 2>/dev/null || tail -80 /var/log/kern.log 2>/dev/null || true
vmstat 1 5
(command -v mpstat >/dev/null && mpstat -P ALL 1 3) || grep '^cpu' /proc/stat | head -40
(command -v pidstat >/dev/null && pidstat -u -d -r -w 1 5) || ps -eo pid,ppid,state,comm,pcpu,pmem,rss,vsz,wchan:24 --sort=-pcpu | head -40
(command -v iostat >/dev/null && iostat -xz 1 5) || cat /proc/diskstats
free -h 2>/dev/null || cat /proc/meminfo
(command -v sar >/dev/null && sar -n DEV,TCP,ETCP 1 5) || (cat /proc/net/dev; cat /proc/net/snmp; cat /proc/net/netstat)
top -bn1 | head -40
```

Fallbacks:

- If `mpstat`, `pidstat`, `iostat`, or `sar` are missing, do not block. Use `top`, `ps`, `/proc/stat`, `/proc/meminfo`, `/proc/diskstats`, `/proc/net/*`, `ss`, `ip -s link`, and kernel logs.
- If `dmesg` is restricted, try `journalctl -k`; on non-systemd systems, check `/var/log/messages`, `/var/log/kern.log`, or `/var/log/syslog`.
- If cgroup evidence is needed, identify v1/v2/hybrid first with `stat -fc %T /sys/fs/cgroup` and `mount | grep cgroup`.
- If permissions are limited in a container, collect cgroup files and pod metrics, then inspect from the node if needed.

## Snapshot Interpretation

Use this table to route the investigation.

| Evidence | Interpretation | Next reference |
|---|---|---|
| `uptime` load average exceeds CPU core count | System has runnable or uninterruptible backlog | CPU first; IO if `D` tasks or iowait |
| `vmstat r` high, `%us` high, top process CPU high | User-space CPU saturation | `references/cpu.md` |
| `%sy` high, high `cs`, `pidstat -w` high | Kernel/syscall/scheduler pressure | `references/cpu.md` |
| `%si` high or one CPU softirq hot | Usually network interrupt or packet pressure | `references/network.md`, then CPU softirq branch |
| `%wa` high, `vmstat b` high, `iostat await/aqu-sz/%util` high | IO wait or storage latency | `references/io.md` |
| `free` shows low `MemAvailable`, active `si/so`, or OOM logs | Real memory pressure, swap, or OOM | `references/memory.md` |
| High `buff/cache` but healthy `MemAvailable` | Usually cache, not a leak | `references/memory.md` |
| `sar -n TCP,ETCP` retransmits or resets are high | TCP/network issue or downstream pressure | `references/network.md` |
| `sar -n DEV` packet rate high and `%si` high | NIC/softirq path | `references/network.md` |
| `dmesg` has OOM, blocked tasks, I/O errors, reset, or timeout | Jump to the matching error branch | Memory or IO |
| Container is slow while host looks okay | Check cgroup CPU, memory, and IO throttling | CPU, memory, or IO container branches |

## Domain Branches

### CPU Branch

Read `references/cpu.md` when CPU, load, scheduler, softirq, context switching, or container CPU throttling appears likely.

Fast path:

```bash
uptime
top -bn1 | head -40
vmstat 1 5
mpstat -P ALL 1 3
pidstat -u -t 1 5
pidstat -w 1 5
```

Branch:

- `%us` high: locate process/thread, then profile with `perf`, runtime profiler, or flame graph.
- `%sy` high: inspect syscall frequency with `strace -c` and kernel hot functions with `perf top -U`.
- `%si` high: inspect network packet rate, softirq distribution, NIC queues, and drops.
- `%wa` high: switch to IO. CPU is mostly waiting.
- `%st` high: virtualized host is losing CPU to the hypervisor.
- High context switches: inspect thread count, locks, IO waits, and scheduler churn.
- Container latency with low apparent CPU: inspect cgroup CPU throttling.

### Memory Branch

Read `references/memory.md` when memory alerts, `free`, `MemAvailable`, Page Cache, RSS growth, swap, slab, OOM, or container limits are relevant.

Fast path:

```bash
free -h
vmstat 1 5
cat /proc/meminfo
ps aux --sort=-rss | head -20
dmesg -T | grep -Ei 'oom|out of memory|killed process'
```

Branch:

- `MemAvailable` healthy and cache high: explain Linux caching; inspect Page Cache only if it causes reclaim or IO impact.
- `MemAvailable` low and RSS high: process memory pressure or leak.
- Process memory attribution with shared pages: prefer PSS, `smem`, or `smaps_rollup` over raw RSS.
- Container memory alert with healthy host memory: inspect cgroup `memory.stat`; distinguish `anon`/RSS from file cache charged to the cgroup.
- `si/so` active: active swap pressure; correlate with latency and IO.
- `Dirty` or `Writeback` high: storage writeback issue; switch to IO.
- `Slab` or `SUnreclaim` high: kernel object/cache pressure.
- OOM/OOMKilled: determine host OOM vs cgroup OOM before restart-only advice.

### IO Branch

Read `references/io.md` when iowait, `D` state, slow disk, high latency, filesystem, inode, database/Redis persistence, logs, NFS, cloud disk, or PVC symptoms appear.

Fast path:

```bash
top
vmstat 1 5
iostat -d -x 1 5
pidstat -d 1 5
iotop -a -o
df -h
df -i
dmesg -T | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O'
```

Branch:

- High `%util` + high `await` + high `aqu-sz`: disk/backend bottleneck.
- High `await` with low `%util`: latency below the guest, network storage, cloud throttling, or queueing elsewhere.
- One process dominates writes: inspect files and syscalls with `lsof`, `/proc/<pid>/io`, `strace`, or eBPF.
- Inode/space errors: inspect `df -h`, `df -i`, deleted files, small-file storms.
- `dm-*`, NFS, overlayfs, PVC, cloud disks: map logical device to backend before concluding.
- Advanced block tracing, storage environment mapping, and optimization details live in the IO deep references listed in Source Model.

### Network Branch

Read `references/network.md` when latency, timeout, packet loss, retransmission, DNS, TLS, HTTP status, NAT/conntrack, softirq, NIC drops, routing, MTU, firewall, or Kubernetes networking appears likely.

Fast path:

```bash
ip addr
ip route
ss -s
ss -antpi
nstat -az
ip -s link
sar -n DEV,TCP,ETCP 1 5
curl -v -w '%{time_namelookup} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}\n' <url>
```

Branch:

- Connect timeout: split DNS, SYN, SYN+ACK, final ACK, TLS/app handshake.
- Retransmission: identify affected flow and whether loss, reordering, timeout, or receiver drops are present.
- High packet rate with softirq: inspect NIC queues, softnet, interrupts, drops, and RSS/RPS.
- DNS slow: compare resolver, retries, UDP loss, TCP fallback, search domain expansion.
- NAT/conntrack: inspect count/max, drops, table pressure, and gateway packet rate.
- HTTP/TLS symptoms: translate status or TLS alert into packet/application phase.
- Packet analysis, Kubernetes networking, TCP queue semantics, routing/MTU, and performance-path details live in the network deep references listed in Source Model.

## Cross-Domain Traps

- High load can be CPU runnable tasks or IO-blocked `D` tasks. Check `vmstat r` and `b`.
- High iowait can be caused by memory reclaim or swap, not only disks.
- High memory `used` can be normal Page Cache. Use `MemAvailable`, pressure, and `si/so`.
- Network latency can be server CPU queueing, softirq delay, DNS, retransmission, or application backlog.
- Disk latency in a VM or container can be backend throttling. Guest metrics are only one layer.
- Low container CPU percentage does not rule out cgroup CPU quota throttling.
- Kernel threads such as `jbd2`, `kworker`, or `kswapd` are often symptoms; find the upstream workload.

## Final Report Template

Use this when the user wants a diagnosis summary.

```markdown
## Conclusion
[Root cause or leading hypothesis, with confidence.]

## Evidence
- CPU:
- Memory:
- IO:
- Network:
- Logs / dmesg:

## Ruled Out
- [What the evidence makes unlikely.]

## Next Validation
```bash
[focused commands]
```

## Recommendations
- Immediate mitigation:
- Durable fix:
- Monitoring to add:
```

## Safety Notes

- Avoid `echo 3 > /proc/sys/vm/drop_caches` as a normal fix; use it only for controlled experiments.
- Avoid broad full-payload packet captures unless authorized; packet data can contain credentials and user data.
- Avoid `tcp_tw_recycle`; it is unsafe and removed from modern kernels.
- Avoid blindly raising buffers, queues, backlog, or conntrack limits before proving saturation.
- Capture evidence before restarting a process when OOM, deadlock, or intermittent latency is under investigation.
