# Memory Troubleshooting Reference

Use this reference after the 60-second snapshot points to memory pressure, Page Cache, RSS growth, swap activity, slab growth, OOM, or container memory limits.

## Core Model

Do not equate "high used memory" with a memory leak. First classify where memory went:

- **Anonymous RSS**: process heap, stacks, mmap, runtime memory, native allocations.
- **PSS / Proportional Set Size**: private memory plus a proportional share of shared pages; prefer it over raw RSS when shared libraries, mmap, worker processes, or shared memory may cause double-counting.
- **Page Cache**: file-backed cache; often reclaimable, but can create pressure under churn.
- **Buffers and Dirty/Writeback**: block IO metadata and dirty pages waiting for storage.
- **Slab**: kernel object caches such as dentry, inode, socket, kmalloc, ext4/XFS objects.
- **Shared memory/tmpfs**: `/dev/shm`, tmpfs, IPC, container shared memory.
- **Swap**: historical usage can be harmless; active swap-in/out is a pressure signal.
- **Cgroup memory**: container limit pressure can exist while host memory is healthy.

## First Commands

```bash
free -h
vmstat 1 5
cat /proc/meminfo
ps aux --sort=-rss | head -20
pidstat -r 1 5
slabtop -o | head -30
dmesg -T | grep -Ei 'oom|out of memory|killed process'
```

For a suspicious process:

```bash
cat /proc/<pid>/status
cat /proc/<pid>/smaps_rollup
smem -tk 2>/dev/null
smem -P '<service-name>' 2>/dev/null
pmap -x <pid> | tail -20
cat /proc/<pid>/limits
```

For containers:

```bash
cat /sys/fs/cgroup/memory.current 2>/dev/null
cat /sys/fs/cgroup/memory.max 2>/dev/null
cat /sys/fs/cgroup/memory.stat 2>/dev/null
cat /sys/fs/cgroup/memory.events 2>/dev/null
cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null
cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null
cat /sys/fs/cgroup/memory/memory.failcnt 2>/dev/null
```

## Decision Table

| Symptom | Evidence | Meaning | Next step |
|---|---|---|---|
| High `used`, healthy `MemAvailable` | `free -h`, `/proc/meminfo` | Usually normal cache | Explain cache; inspect only if latency/reclaim exists |
| Real pressure | Low `MemAvailable`, active reclaim/swap, latency | Memory shortage or leak/cache churn | Classify RSS, cache, slab, swap |
| Process memory high | `ps --sort=-rss`, `pidstat -r`, RSS trend | App heap/native memory/unbounded cache | Runtime heap tools, `/proc/<pid>/smaps_rollup` |
| RSS high but many shared pages | `smem`, `Pss`, `/proc/<pid>/smaps_rollup` | RSS may over-count shared memory | Use PSS/private memory before blaming a process |
| Active swap | `vmstat si/so` nonzero | Memory pressure causing IO | Find RSS/cache source; correlate with iowait |
| Page Cache pressure | `Cached`, `Dirty`, `Writeback`, file IO | File cache or dirty writeback | Find readers/writers, switch to IO for writeback |
| Container memory alert, host healthy | cgroup `memory.stat` `anon`/`file`, `memory.events` | Cgroup limit pressure; file cache may be charged | Separate anon leak from cgroup file cache |
| Slab pressure | `Slab`, `SReclaimable`, `SUnreclaim`, `slabtop` | Kernel object cache | Inspect dentry/inode/tcp/kmalloc families |
| OOM | `dmesg`, cgroup events, Kubernetes OOMKilled | Limit exceeded or host OOM | Determine host vs cgroup OOM, preserve logs |

## Pressure Classification

Use this order:

1. Check `MemAvailable`, not just `free`.
2. Check `vmstat 1 5` for `si`, `so`, `r`, `b`, and `wa`.
3. Check whether RSS, Page Cache, slab, tmpfs, or cgroup usage explains the missing memory.
4. Check OOM and cgroup events.
5. Correlate with deploys, traffic, batch jobs, file scans, backups, and storage latency.

Interpretation:

- `MemAvailable` healthy: the host likely has enough reclaimable memory.
- `MemAvailable` low + `si/so` active: urgent memory pressure with swap IO.
- `MemAvailable` low + high RSS: process memory is the leading branch.
- `MemAvailable` low + high `Cached`: Page Cache churn may be pressuring memory.
- `Dirty` or `Writeback` high: memory pressure may be coupled to slow storage.
- `SUnreclaim` high: unreclaimable kernel memory needs slab inspection.

## Process RSS/PSS and Leak Branch

Use when one or more processes explain memory pressure or RSS grows over time.

```bash
ps -eo pid,ppid,cmd,%mem,rss,vsz --sort=-rss | head -30
pidstat -r -p <pid> 1 10
smem -tk 2>/dev/null
smem -P '<service-name>' 2>/dev/null
cat /proc/<pid>/smaps_rollup
grep -E 'VmRSS|RssAnon|RssFile|RssShmem|VmSwap|Threads' /proc/<pid>/status
grep -E 'Pss|Rss|Private|Shared|Swap' /proc/<pid>/smaps 2>/dev/null | head -80
```

Interpretation:

- RSS includes shared pages mapped into the process and can over-count memory across processes.
- `Pss` divides shared pages across sharers, so it is better for "which process really owns memory?" questions.
- `RssAnon` high: heap, stacks, anonymous mmap, or native memory.
- `RssFile` high: mapped files and libraries, often reclaimable.
- `RssShmem` high: shared memory, tmpfs, or IPC.
- `VmSwap` high: this process has swapped pages.
- Rising `Private_Dirty`, `RssAnon`, or PSS across multiple samples and not falling after traffic drops: leak or unbounded cache becomes plausible.

Runtime directions:

- Java: inspect heap vs native memory with `jcmd`, NMT, heap dump, GC logs, and thread count.
- Go: use pprof heap and goroutine profiles.
- Python: use `tracemalloc`, `memray`, or process-level sampling.
- Node.js: heap snapshots and `--inspect`; distinguish V8 heap from native buffers.
- Native services: use allocator stats, `pmap`, `smaps`, ASAN/LSAN in staging, or allocator profiling.

## Page Cache and Writeback Branch

Use when `Cached`, `Buffers`, `Dirty`, or `Writeback` is large.

```bash
grep -E 'MemAvailable|Cached|Buffers|Dirty|Writeback|Mapped|Shmem' /proc/meminfo
sar -B 1 5
grep -E 'pgscan|pgsteal|pgfault|pgmajfault|drop_pagecache|drop_slab|pginodesteal|kswapd_inodesteal|compact' /proc/vmstat
grep -E 'Active\(file\)|Inactive\(file\)|Active\(anon\)|Inactive\(anon\)|Unevictable|Mlocked' /proc/meminfo
cat /proc/pressure/memory 2>/dev/null
vmtouch -v <path> 2>/dev/null
pidstat -d 1 5
iostat -xz 1 5
lsof +D <path>
```

Interpretation:

- High Page Cache with healthy `MemAvailable`: usually useful Linux behavior.
- High Page Cache with low `MemAvailable`: file access churn may be competing with applications.
- High `Dirty` or `Writeback`: storage writeback cannot keep up or dirty limits are being hit.
- Large `Mapped`: applications may be mapping large files.
- `pgscand` or direct reclaim rising with latency/load: application threads may be reclaiming memory synchronously.
- `drop_pagecache` or `drop_slab` changed: someone or something likely dropped cache; `drop_slab` can also evict file cache through inode reclaim.
- `pginodesteal` or `kswapd_inodesteal` rising: inode/slab reclaim may be evicting file Page Cache.
- High memory PSI `some` or `full`: memory pressure is affecting runnable work, often visible as load or latency.

Fix direction:

- Do not use `drop_caches` as a routine fix. It hides evidence and can worsen latency.
- Reduce file scan churn, batch reads, adjust cache behavior at the application level, or isolate workloads.
- For important file cache working sets, consider application-level `mlock`/`madvise` or cgroup memory protection only after proving cache eviction is the issue.
- If writeback is involved, switch to IO branch and inspect writer processes and storage latency.

## Cgroup File Cache Branch

Use when a container or pod has a memory alert, limit pressure, or OOMKilled while host memory still looks healthy.

```bash
cat /sys/fs/cgroup/memory.current 2>/dev/null
cat /sys/fs/cgroup/memory.max 2>/dev/null
cat /sys/fs/cgroup/memory.stat 2>/dev/null
cat /sys/fs/cgroup/memory.events 2>/dev/null

# cgroup v1
cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null
cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null
cat /sys/fs/cgroup/memory/memory.stat 2>/dev/null
cat /sys/fs/cgroup/memory/memory.failcnt 2>/dev/null
```

Interpretation:

- cgroup v2: compare `anon` and `file`; `file`, `active_file`, and `inactive_file` are file cache charged to the cgroup.
- cgroup v1: compare `total_rss` and `total_cache`.
- A container can hit its memory limit because file cache is charged to the cgroup even when host `MemAvailable` is healthy.
- If `anon` grows, suspect heap, native allocations, or process private memory.
- If `file` grows with scans, backups, logs, image pulls, or database reads, suspect cgroup file cache pressure.

## Swap Branch

Use when swap is nonzero or latency correlates with reclaim.

```bash
swapon --show
vmstat 1 10
sar -W 1 10
grep -E 'SwapTotal|SwapFree|SwapCached' /proc/meminfo
```

Interpretation:

- Swap used + `si/so` zero: often historical and not urgent.
- `si/so` continuously nonzero: active swapping and likely latency impact.
- Swap with high iowait: memory pressure is creating disk pressure.

Fix direction:

- Identify the memory consumer first.
- Add memory or reduce working set if active swapping is sustained.
- Tune `swappiness` only after the source of pressure is known.

## Slab Branch

Use when process RSS does not explain memory and `Slab`, `SReclaimable`, or `SUnreclaim` is large.

```bash
grep -E 'Slab|SReclaimable|SUnreclaim|KReclaimable' /proc/meminfo
slabtop -o | head -40
cat /proc/slabinfo | head
```

Interpretation:

- `dentry`/`inode_cache` high: path traversal, small-file churn, or filesystem metadata pressure.
- socket-related caches high: connection churn or network pressure.
- `SReclaimable` high: usually less urgent than `SUnreclaim`, but can still add reclaim cost.
- `SUnreclaim` high: less reclaimable kernel memory; inspect workload and kernel version.

## OOM and Cgroup Branch

Use when OOM, OOMKilled, or cgroup events appear.

```bash
dmesg -T | grep -Ei 'oom|out of memory|killed process'
cat /sys/fs/cgroup/memory.events 2>/dev/null
cat /sys/fs/cgroup/memory.stat 2>/dev/null
kubectl describe pod <pod> | grep -A20 -Ei 'oom|killed|limits|requests|last state'
```

Interpretation:

- Host OOM: kernel log shows global memory context and killed process.
- Cgroup OOM: container memory events or Kubernetes OOMKilled may occur while host memory is healthy.
- The killed process is not always the root cause; it can be the selected victim.

Fix direction:

- Preserve OOM logs and memory snapshots before restart when possible.
- Raise memory limit only if working set justifies it.
- Reduce heap, cache, concurrency, batch size, tmpfs use, or file cache churn depending on the classified memory type.
