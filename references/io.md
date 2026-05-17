# IO Troubleshooting Reference

Use this reference after the 60-second snapshot points to iowait, blocked tasks, disk latency, filesystem issues, inode exhaustion, database or Redis persistence stalls, noisy logging, NFS, cloud disks, overlayfs, or PVC-backed volumes.

## Deep References

Load these only when the broad IO path points there:

- `io-storage-environments.md`: logical devices, LVM/RAID, cloud block storage, NFS, overlayfs, Kubernetes PVCs, backup and batch interference.
- `io-advanced-tools.md`: `perf trace`, BCC/eBPF tools, `blktrace`, `blkparse`, `btt`, `iowatcher`, and focused `strace`.
- `io-optimization.md`: application IO options, scheduler checks, dirty page writeback, mount options, backup scheduling, and tuning discipline.

## Core Model

Diagnose IO with the USE frame:

- **Utilization**: how busy the disk, filesystem, or storage backend is.
- **Saturation**: whether requests are queued or tasks are blocked.
- **Errors**: whether the device, driver, filesystem, or provider reports failures.

Move from broad to narrow:

```text
top/vmstat -> iostat -> pidstat/iotop -> process files/syscalls -> block/filesystem tracing -> backend/provider metrics
```

## First Commands

```bash
top
vmstat 1 5
(command -v iostat >/dev/null && iostat -d -x 1 5) || cat /proc/diskstats
(command -v pidstat >/dev/null && pidstat -d 1 5) || for p in /proc/[0-9]*/io; do pid=${p#/proc/}; pid=${pid%/io}; comm=$(cat /proc/$pid/comm 2>/dev/null); awk -v pid="$pid" -v comm="$comm" '/read_bytes|write_bytes|cancelled_write_bytes/{printf "%s=%s ",$1,$2} END{print pid,comm}' "$p" 2>/dev/null; done | head -40
command -v iotop >/dev/null && iotop -a -o
df -h
df -i
(command -v findmnt >/dev/null && findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS) || cat /proc/mounts
(command -v lsblk >/dev/null && lsblk -o NAME,MAJ:MIN,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL) || cat /proc/partitions
(dmesg -T | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O|blocked') 2>/dev/null || (journalctl -k --no-pager -n 200 | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O|blocked') 2>/dev/null || true
```

Fallback impact: `/proc/diskstats` does not directly provide `await` or `%util`; use it to preserve direction, then lower confidence or sample counter deltas before blaming a disk/backend.

Process and file attribution:

```bash
cat /proc/<pid>/io
lsof -p <pid>
strace -p <pid> -e trace=read,write,open,openat,pread64,pwrite64,fsync,fdatasync
```

## Decision Table

| Symptom | Evidence | Meaning | Next step |
|---|---|---|---|
| Disk saturated | `iostat` high `%util`, high `await`, high `aqu-sz` | Device/backend bottleneck | Attribute process and inspect backend limits |
| High latency, low util | high `await`, low `%util` | Network/cloud storage, queueing elsewhere, burst throttling | Compare provider/storage metrics |
| Blocked tasks | `vmstat b`, `top` D state, dmesg blocked task | Tasks stuck in IO | Map process and file/device |
| Write storm | `pidstat -d`, `iotop`, dirty/writeback high | Logs, DB flush, sync writes, writeback | `lsof`, `/proc/<pid>/io`, `strace` |
| Space/inode issue | `df -h`, `df -i` | Full filesystem or small-file storm | Find path, deleted files, inode consumers |
| Device errors | `dmesg` reset/timeout/I/O error | Driver, disk, controller, cloud backend | SMART/NVMe/provider health |
| Logical device ambiguity | `dm-*`, LVM, RAID, NFS, overlayfs, PVC | Guest metric is not enough | Map stack with `lsblk`, `findmnt`, backend metrics |

## `iostat -x` Interpretation

Important fields:

- `%util`: time the device had at least one request in service. High values are a signal, not proof of saturation by themselves.
- `r/s`, `w/s`: read and write IOPS.
- `rkB/s`, `wkB/s`: throughput.
- `r_await`, `w_await`, `await`: average time per request including queue time.
- `aqu-sz`: average queue depth.
- `rareq-sz`, `wareq-sz`: request size; helps distinguish small random IO from large sequential IO.

Branches:

| Evidence | Interpretation | Next step |
|---|---|---|
| High `%util`, high `await`, high `aqu-sz` | Strong device/backend bottleneck | Find process and compare backend limits |
| High IOPS, small request size | Random or tiny IO pressure | Find workload, batch, cache, or change layout |
| High throughput, large request size | Sequential workload | Compare against device bandwidth and SLO |
| High write await, `jbd2` visible | Journal/writeback is suffering | Find upstream writer; inspect `fsync` behavior |
| High await on `dm-*` only | Logical device ambiguity | Map to physical/cloud/NFS/PVC backend |

## Process Attribution Branch

Use when disk pressure exists but the actor is unknown.

```bash
pidstat -d 1 5
iotop -a -o
cat /proc/<pid>/io
lsof -p <pid>
systemd-cgtop
```

Interpretation:

- `kB_rd/s` and `kB_wr/s`: live read/write throughput.
- `iodelay`: time the task waited for block IO.
- `write_bytes` in `/proc/<pid>/io`: bytes that reached storage.
- `cancelled_write_bytes`: writes that were dirtied but later avoided, often from overwritten or deleted data.

Common patterns:

- A logging process dominates writes: reduce log volume, batch, rotate, or move logs.
- Database process dominates reads: inspect query plan, cache hit rate, table/index access.
- Database process dominates writes or `fsync`: inspect commit frequency, WAL, checkpointing, and storage latency.
- Redis stalls: inspect AOF/RDB persistence, fork copy-on-write pressure, disk latency, and client command patterns.
- Kernel threads dominate: find upstream user process or memory/writeback pressure.

## Filesystem and Space Branch

Use for path-level errors, full filesystems, inode exhaustion, deleted-file leaks, or small-file storms.

```bash
df -h
df -i
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
lsof | grep deleted
du -xhd1 <mountpoint> | sort -h
```

Interpretation:

- `df -h` full: data blocks are exhausted.
- `df -i` full: inode exhaustion, often from many small files.
- Deleted files held open: disk space will not return until the process closes the file descriptor or restarts.
- Metadata-heavy trees: dentry/inode cache can grow and path operations become expensive.

## Errors and Device Health Branch

Use when latency spikes suddenly, devices disappear, or `dmesg` contains storage errors.

```bash
dmesg -T | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O|ext4|xfs'
smartctl -a /dev/<disk>
nvme smart-log /dev/<nvme-device>
```

Interpretation:

- Reset/timeout messages: driver, controller, device, cable, firmware, or provider issue.
- Filesystem errors: protect data first; avoid tuning as the first response.
- Cloud disk errors or throttling: compare guest metrics with provider metrics.

## Virtual, Cloud, NFS, and Kubernetes Storage

Use when the device is `dm-*`, LVM, RAID, SAN, NAS, NFS, EBS-like block storage, overlayfs, or Kubernetes PVC.

For details, read `io-storage-environments.md`.

```bash
lsblk -t
lsblk -o NAME,KNAME,MAJ:MIN,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL
findmnt -R <path>
cat /proc/mounts
```

Interpretation:

- Guest `%util` may reflect a virtual device queue, not the physical backend.
- Cloud disks can throttle on IOPS, throughput, burst credits, instance bandwidth, or backend health.
- NFS latency can be network or server-side storage, not local disk.
- Kubernetes path must be mapped: Pod -> container mount -> node path -> PVC -> PV -> StorageClass -> backend volume.
- Overlayfs can amplify writes and metadata operations.

## Advanced Tracing

Use only after basic metrics prove IO latency but do not explain where time is spent.

For details, read `io-advanced-tools.md`.

```bash
perf trace -e block:block_rq_issue,block:block_rq_complete -a sleep 5
biosnoop
biolatency
biotop
ext4slower 10
xfsslower 10
```

Use full `blktrace` only when necessary and for short windows:

```bash
blktrace /dev/<disk>
blkparse <trace>
btt <trace>
```

Explain the risk: full block tracing can be heavy on busy systems and produces large files quickly.

## Fix Directions

For deeper optimization notes, read `io-optimization.md`.

- Reduce IO volume before tuning queues: batch, cache, debounce, compress, or remove noisy writes.
- Reduce sync frequency if `fsync` dominates and durability requirements allow it.
- Move hot data to faster storage or split random and sequential workloads.
- Increase provisioned IOPS/throughput or instance bandwidth when backend limits are proven.
- Avoid changing scheduler, dirty ratios, or readahead until workload shape is known.
