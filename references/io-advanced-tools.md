# IO Advanced Tools

Use this reference when basic IO metrics prove latency or saturation but cannot explain where time is spent. Prefer low-overhead and focused tools first.

## Tool Risk Ladder

| Level | Tools | Use when | Risk |
|---|---|---|---|
| Low | `iostat`, `pidstat`, `iotop`, `/proc/<pid>/io`, `lsof` | First attribution | Usually safe |
| Medium | focused `strace`, `perf trace`, BCC tools | Need syscall or block timing | Short windows on production |
| High | `blktrace`, broad eBPF, full filesystem tracing | Vendor or deep kernel evidence needed | Can produce high overhead and large output |

## Focused `strace`

Use when one process is suspected and you need to see file calls or sync behavior.

```bash
strace -p <pid> -tt -T \
  -e trace=read,write,open,openat,pread64,pwrite64,fsync,fdatasync,close \
  -o /tmp/io.strace
```

Safer summary mode:

```bash
strace -c -p <pid> \
  -e trace=read,write,open,openat,pread64,pwrite64,fsync,fdatasync,close
```

Interpretation:

- Frequent small writes: buffering or batching issue.
- Frequent `fsync`/`fdatasync`: durability path or database/logging behavior.
- Long `openat` or path operations: filesystem metadata, network storage, directory size, or lock contention.
- No relevant syscalls: IO may be in another process/thread or at kernel/block layer.

## Lightweight Block Tracing with `perf`

Use when you need issue/complete timing without full `blktrace`.

```bash
perf trace -e block:block_rq_issue,block:block_rq_complete -a -- sleep 5
```

Interpretation:

- Long gap before issue: queueing above block layer, filesystem, scheduler, or cgroup.
- Long gap between issue and complete: device or backend service time.
- Many tiny requests: application or filesystem fragmentation pattern.

## BCC/eBPF Tools

Use if available and permitted.

```bash
biosnoop
biolatency
biotop
filetop
opensnoop
ext4slower 10
xfsslower 10
```

Tool selection:

- `biosnoop`: per-IO latency and process attribution at block layer.
- `biolatency`: latency distribution; useful for proving tail latency.
- `biotop`: top block IO consumers.
- `filetop`: active file reads/writes.
- `opensnoop`: path-level open calls and failures.
- `ext4slower`/`xfsslower`: filesystem operations slower than a threshold.

Interpretation cautions:

- eBPF output may sample or miss very short-lived events depending on tool.
- Container PID namespaces can make process attribution confusing.
- Kernel version and BTF availability affect tool support.

## Full Block-Layer Tracing

Use `blktrace` only when simpler tools are insufficient or a storage vendor requests block-layer evidence.

```bash
blktrace -d /dev/<disk> -o /tmp/blk -w 10
blkparse -i /tmp/blk.blktrace.* > /tmp/blkparse.txt
btt -i /tmp/blk.blktrace.*
iowatcher -t /tmp/blk.blktrace.* -o /tmp/iowatcher.svg
```

What it can answer:

- Queueing time before dispatch.
- Service time after dispatch.
- Request merge/split behavior.
- Sequential vs random patterns.
- Scheduler or device latency shape.

Risks:

- Output can grow quickly on busy disks.
- Tracing can add overhead.
- Data can reveal workload patterns and paths. Store and share carefully.

## When Advanced Tools Do Not Help

If tracing shows low block latency but the application is slow:

- Re-check application locks, thread pools, GC, and network dependencies.
- Inspect filesystem metadata operations.
- Inspect remote storage or provider metrics.
- Check memory reclaim and swap.

If tracing shows high block latency but no process dominates:

- Look for kernel writeback, journal, swap, filesystem recovery, backup agents, or cgroup-level consumers.
- Compare node-level metrics with container/pod ownership.
