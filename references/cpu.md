# CPU Troubleshooting Reference

Use this reference after the 60-second snapshot points to CPU saturation, scheduler pressure, softirq load, high context switching, virtualization steal time, or cgroup CPU throttling.

## Core Model

CPU incidents are not only "a process uses 100% CPU". Separate these classes first:

- **User CPU (`%us`)**: application code, runtime, serialization, regex, compression, crypto, GC, algorithms.
- **System CPU (`%sy`)**: syscalls, kernel locks, memory allocation, network stack, filesystem work.
- **Softirq CPU (`%si`)**: usually packet processing, timers, or block completions.
- **I/O wait (`%wa`)**: CPU is mostly idle but blocked work waits on I/O.
- **Steal (`%st`)**: a virtual CPU is runnable but the hypervisor did not schedule it.
- **Scheduler churn**: many context switches, too many runnable threads, lock contention, or thread-pool oversubscription.
- **Cgroup throttling**: a container has CPU demand but is paused by CFS quota.

## First Commands

```bash
uptime
top -bn1 | head -40
vmstat 1 5
mpstat -P ALL 1 5
pidstat -u 1 5
pidstat -w 1 5
```

If a process is suspicious:

```bash
top -H -p <pid>
pidstat -t -p <pid> 1 5
cat /proc/<pid>/status
ls /proc/<pid>/task | wc -l
```

## Decision Table

| Symptom | Evidence | Meaning | Next step |
|---|---|---|---|
| High user CPU | `%us` high, one process/thread high | Application hot path | Profile the process and runtime |
| High system CPU | `%sy` high, syscall-heavy process | Kernel work caused by application or driver path | Summarize syscalls, inspect kernel stacks |
| High softirq | `%si` high, one CPU hot, `NET_RX` grows | Packet processing, timers, or IRQ imbalance | Inspect network counters and softirq distribution |
| High iowait | `%wa` high, `vmstat b` high | CPU is waiting for I/O | Switch to IO or memory reclaim branch |
| High steal | `%st` persistent | Hypervisor contention | Check cloud/VM metrics and migrate or resize |
| Context switch storm | `vmstat cs`, `pidstat -w` high | Too many threads, locks, IO waits, scheduler churn | Find process/thread source |
| Container latency with low CPU | `nr_throttled` grows | CPU quota throttling | Adjust CPU limits or concurrency |

## User CPU Branch

Use when `%us` dominates and one process or thread is hot.

```bash
pidstat -u -t -p <pid> 1 5
perf top -p <pid>
perf record -F 99 -g -p <pid> -- sleep 30
perf report
```

Runtime-specific alternatives:

| Runtime | Preferred tool | Why |
|---|---|---|
| Java/Kotlin/Scala | `async-profiler`, JFR, `jstat -gcutil` | Better JIT and GC visibility than raw `perf` |
| Go | `go tool pprof` against `/debug/pprof/profile` | Preserves Go symbols and goroutine context |
| Python | `py-spy top`, `py-spy record` | Can attach without modifying the process in many cases |
| Node.js | `clinic`, `0x`, built-in inspector profiles | Better JavaScript stack visibility |
| Native C/C++ | `perf`, `gperf`, `bcc` profile tools | Kernel/user stack sampling works well |

Interpretation:

- Business functions dominate: optimize algorithm, caching, batching, serialization, compression, or query planning.
- GC functions dominate: reduce allocation rate, tune heap, inspect object lifetime, or change traffic/concurrency.
- Regex or parser functions dominate: check catastrophic backtracking and input size.
- Crypto/compression dominate: check configuration, hardware acceleration, and batching.

## System CPU Branch

Use when `%sy` is high or `pidstat` shows high `%system`.

Low-overhead first:

```bash
pidstat -u -p <pid> 1 5
strace -c -p <pid>
perf top -p <pid> -U
```

Only use short targeted tracing on production:

```bash
strace -tt -T -p <pid> -e trace=read,write,openat,close,futex,epoll_wait,recvfrom,sendto --summary-only
```

Common patterns:

| Evidence | Meaning | Fix direction |
|---|---|---|
| Many tiny `read`/`write` calls | Unbatched IO or logging | Buffer, batch, reduce flushes |
| Heavy `futex` | Lock contention | Reduce critical section, shard locks, tune thread pool |
| Many `clone`/`fork`/`execve` | Process/thread churn | Reuse workers, pool tasks |
| Many `mmap`/`brk` | Allocation churn | Reduce allocation rate, use pools carefully |
| Kernel networking functions hot | Packet path pressure | Inspect network branch |
| Filesystem/block functions hot | Storage path pressure | Inspect IO branch |

## Softirq Branch

Use when `%si` is high, a single core is much hotter than others, or network packet rate is high.

```bash
mpstat -P ALL 1 5
cat /proc/softirqs
cat /proc/interrupts
sar -n DEV 1 5
ss -s
ip -s link
ethtool -S <iface>
cat /proc/net/softnet_stat
```

Interpretation:

- `NET_RX` grows rapidly: receive packet processing dominates.
- One CPU gets most interrupts: check RSS queue count, IRQ affinity, RPS/XPS, and NIC multiqueue settings.
- `softnet_stat` drops or time_squeeze increments: kernel packet backlog could not keep up.
- Packet rate is high with small packets: packet-per-second capacity, not bandwidth, may be the bottleneck.

Fix direction:

- Enable or rebalance RSS/RPS/XPS after proving CPU imbalance.
- Scale out or reduce packet rate at the load balancer.
- Tune application connection reuse and batching.
- Inspect NIC drops and driver counters before raising buffers.

## Context Switch Branch

Use when `vmstat cs` is unusually high or `pidstat -w` identifies a process.

```bash
pidstat -w 1 5
pidstat -wt -p <pid> 1 5
cat /proc/<pid>/status | grep -E 'Threads|voluntary|nonvoluntary'
```

Interpretation:

- High voluntary switches (`cswch/s`): threads are waiting voluntarily, often for locks, IO, epoll, sleep, or condition variables.
- High involuntary switches (`nvcswch/s`): too many runnable threads or CPU time slices are expiring.
- Thread count much larger than CPU count: thread-pool oversubscription can add latency even when average CPU is moderate.

## Cgroup CPU Throttling Branch

Use when a container or Kubernetes service has latency spikes but apparent CPU usage is not high.

```bash
cat /sys/fs/cgroup/cpu.stat 2>/dev/null
cat /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null
cat /sys/fs/cgroup/cpu.max 2>/dev/null
cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null
cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null
kubectl top pod <pod>
kubectl describe pod <pod> | grep -A8 -E 'Limits|Requests'
```

Interpretation:

- `nr_throttled / nr_periods` above 5% is worth investigating; much higher ratios often explain P99 spikes.
- `throttled_usec` or `throttled_time` increasing quickly means the process is being paused.
- `nproc` inside a container can show host CPUs even when the quota is far lower; do not size thread pools from `nproc` blindly.

Fix direction:

- Raise or remove CPU limits for latency-sensitive services.
- Add replicas and reduce per-pod concurrency.
- Tune runtime worker counts to CPU quota, not host core count.
- Reduce CPU hot paths after confirming quota is not the only problem.
