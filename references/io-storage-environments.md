# IO Storage Environments

Use this reference when IO evidence involves logical devices, cloud block storage, NFS, network storage, overlayfs, Kubernetes PVCs, or backup/batch interference. The main goal is to avoid blaming the wrong layer.

## Logical, Mapped, and Virtual Devices

When `iostat` reports `dm-*`, `md*`, `loop*`, `nvme*n*`, or a device name that does not clearly map to a physical disk, map the storage stack before concluding.

```bash
lsblk -o NAME,KNAME,MAJ:MIN,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL
lsblk -t
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
dmsetup ls --tree 2>/dev/null
cat /proc/mdstat 2>/dev/null
```

Interpretation:

- `dm-*` can be LVM, device mapper, encrypted disks, multipath, or Kubernetes volume layers.
- `%util=100%` on a logical device may mean the virtual queue is busy, not that the physical backend is saturated.
- RAID and multipath can hide individual-device latency. Compare all members if possible.
- Loop devices and overlayfs can amplify metadata and copy-up operations.

## Cloud Block Storage

Use this branch for EBS-like volumes, cloud persistent disks, managed block storage, and virtualized disks.

Check both guest and provider metrics:

```bash
iostat -xz 1 5
lsblk -t
dmesg -T | grep -iE 'blk|nvme|reset|timeout|I/O|thrott'
```

Provider-side metrics to compare:

- Provisioned IOPS and actual IOPS.
- Provisioned throughput and actual throughput.
- Latency and queue depth.
- Burst credits or balance.
- Volume health events.
- Instance-level storage/network bandwidth limits.

Common patterns:

- High guest `await` with provider throttling: raise provisioned IOPS/throughput or reduce IO volume.
- Latency after creating/restoring a volume: lazy initialization or cold blocks may be involved.
- Multiple volumes on one instance: instance-level bandwidth can be the bottleneck even if each volume is below its limit.
- Sudden reset/timeout in guest logs: provider or driver health should be checked before application tuning.

## NFS and Network Storage

Use this branch when the filesystem type is `nfs`, `nfs4`, `cifs`, `ceph`, `glusterfs`, or another remote filesystem.

```bash
findmnt -t nfs,nfs4,cifs,ceph,glusterfs
nfsstat -c 2>/dev/null
nfsiostat 1 5 2>/dev/null
mount | grep -E 'nfs|cifs|ceph|gluster'
sar -n DEV,TCP,ETCP 1 5
```

Interpretation:

- High application IO latency may be network latency, server-side storage latency, metadata operation latency, or client mount options.
- NFS metadata-heavy workloads can be slow even when throughput looks low.
- Packet loss or retransmission on the storage network can surface as IO wait.
- Local SMART/NVMe checks do not explain remote storage latency unless the host is also the backend.

Fix direction:

- Compare client, network, and storage-server metrics.
- Reduce metadata operations, batch small files, and avoid directory hot spots.
- Review mount options such as `hard/soft`, `timeo`, `retrans`, `rsize`, `wsize`, and `noatime`.
- Avoid `soft` mounts for workloads that cannot tolerate silent data corruption or partial writes.

## Containers and Kubernetes Volumes

Use this branch when the workload runs in Docker, containerd, Kubernetes, or another cgroup/namespace environment.

Map the path:

```bash
findmnt -R <path>
cat /proc/self/mountinfo
df -h <path>
df -i <path>
```

Kubernetes mapping:

```bash
kubectl describe pod <pod>
kubectl get pvc,pv
kubectl describe pvc <pvc>
kubectl describe pv <pv>
kubectl get storageclass
```

Interpretation:

- Container path latency may come from overlayfs, node disk, PVC backend, network storage, or provider throttling.
- Overlayfs copy-up can make first writes to existing image files unexpectedly expensive.
- PVC performance depends on StorageClass, access mode, backend type, volume size, and provider limits.
- Node-level `iostat` may mix multiple pods; use cgroups and process attribution to isolate.

## Backup and Batch Interference

Use this branch when IO spikes align with backups, compaction, analytics jobs, log rotation, scans, or antivirus/security agents.

```bash
pidstat -d 1 10
iotop -a -o
systemd-cgtop
ps -eo pid,ppid,cmd,stat --sort=pid | grep -Ei 'backup|rsync|tar|gzip|logrotate|compact|scan'
```

Patterns:

- Sequential backup reads can evict Page Cache and degrade latency-sensitive services.
- Compression jobs can combine CPU pressure with IO pressure.
- Database compaction/checkpointing can create bursts of writes and `fsync`.
- Log rotation can briefly spike reads/writes and metadata operations.

Fix direction:

- Schedule batch work away from peak traffic.
- Use cgroup IO limits or `ionice` where appropriate.
- Separate latency-sensitive and batch workloads onto different devices or nodes.
- Add monitoring for IO latency, queue depth, and workload-specific batch windows.
