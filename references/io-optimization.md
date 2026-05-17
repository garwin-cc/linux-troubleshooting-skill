# IO Optimization Notes

Use this reference only after the IO bottleneck is identified. Optimization before attribution can hide the root cause.

## Tuning Discipline

For every proposed change, state:

- The evidence that proves this layer is the bottleneck.
- Whether the change is temporary or persistent.
- The rollback command or config rollback.
- The expected metric movement.
- The risk to durability, latency, or other workloads.

## Modern Application IO Options

Optimization often belongs in the application:

- Batch small reads and writes.
- Buffer logs and reduce synchronous flushes.
- Use connection and file descriptor pooling.
- Prefer sequential access when possible.
- Avoid unnecessary directory scans and metadata-heavy operations.
- Use async IO or `io_uring` only when the application model benefits from it and operational tooling can observe it.

Common changes:

| Pattern | Evidence | Fix direction |
|---|---|---|
| Tiny writes | `strace`, small `wareq-sz` | Buffer, batch, reduce flush frequency |
| Frequent `fsync` | `strace`, DB metrics | Group commits, tune durability settings carefully |
| Metadata storm | `opensnoop`, `slabtop`, inode/dentry growth | Reduce file count, shard directories, cache metadata |
| Large scans evict cache | Page Cache changes, batch job timing | Schedule, isolate, or use cgroup IO limits |

## Device and Scheduler Checks

```bash
lsblk -t
cat /sys/block/<dev>/queue/scheduler
cat /sys/block/<dev>/queue/read_ahead_kb
cat /sys/block/<dev>/queue/nr_requests
```

Guidance:

- Do not change scheduler based only on generic advice. Use workload evidence.
- Modern NVMe devices often use `none` or `mq-deadline`.
- Readahead can help sequential reads and hurt random IO or cache pollution.
- Queue depth changes can improve throughput but worsen tail latency for mixed workloads.

## Dirty Page Writeback

Use when `Dirty`, `Writeback`, `jbd2`, flush threads, or write stalls are visible.

```bash
grep -E 'Dirty|Writeback' /proc/meminfo
sysctl vm.dirty_ratio vm.dirty_background_ratio vm.dirty_bytes vm.dirty_background_bytes vm.dirty_expire_centisecs vm.dirty_writeback_centisecs
iostat -xz 1 5
pidstat -d 1 5
```

Interpretation:

- High `Dirty`: applications are generating writes faster than writeback begins or completes.
- High `Writeback`: kernel is actively flushing and storage may be slow.
- `jbd2` visible: journal writes are a symptom; find upstream writers and sync behavior.

Fix direction:

- Reduce write volume first.
- Improve storage throughput or latency if backend is proven saturated.
- Tune dirty ratios only with clear understanding of memory size and write burst behavior.
- Prefer byte-based dirty limits on very large-memory hosts when ratios are too coarse.

## Filesystem and Mount Options

```bash
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
tune2fs -l /dev/<dev> 2>/dev/null
xfs_info <mountpoint> 2>/dev/null
```

Potential options:

- `noatime` or `relatime`: reduce metadata writes from reads.
- ext4 journal mode: impacts durability and performance; do not change casually.
- XFS allocation and log behavior: workload-specific.
- NFS options: see `io-storage-environments.md`.

Do not recommend filesystem option changes without workload evidence and a maintenance plan.

## Backup and Background Work

For batch interference:

```bash
ionice -p <pid>
systemd-cgtop
cat /sys/fs/cgroup/io.stat 2>/dev/null
```

Mitigation options:

- Schedule work outside peak hours.
- Use `ionice` or cgroup IO controls.
- Separate batch jobs from latency-sensitive disks.
- Rate-limit backup, compression, log shipping, and scans.

## Monitoring to Add

Add metrics that distinguish IO cause from effect:

- Device latency and queue depth.
- Read/write IOPS and throughput.
- Filesystem usage and inode usage.
- Dirty/writeback pages.
- Top IO processes or cgroup IO.
- Cloud volume IOPS/throughput/credits/throttling.
- NFS/RPC latency if remote storage is used.
