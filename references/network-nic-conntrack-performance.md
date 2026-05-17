# NIC, Conntrack, NAT, and Network Performance

Use this reference for NIC drops, softnet backlog, kernel queue pressure, NAT/conntrack pressure, long-fat network throughput, load balancing anomalies, ECMP path issues, pressure-test ceilings, or DDoS packet-shape analysis.

## NIC and Driver Checks

```bash
sar -n DEV 1 5
ip -s link
ethtool -S <iface>
ethtool -g <iface>
ethtool -k <iface>
cat /proc/interrupts
cat /proc/softirqs
cat /proc/net/softnet_stat
mpstat -P ALL 1 5
```

Interpretation:

- Interface RX/TX drops: packets dropped at device, driver, qdisc, or kernel path.
- NIC error counters: physical/link/driver issue or offload problem.
- Ring full or missed errors: ring buffer or interrupt processing cannot keep up.
- `softnet_stat` drops/time_squeeze: kernel backlog processing fell behind.
- One CPU handles most interrupts: queue/IRQ imbalance.

Fix direction:

- Rebalance RSS/RPS/XPS after proving imbalance.
- Increase ring buffers only when drops show ring pressure.
- Reduce packet rate or scale out when packets per second exceed host capacity.
- Review offloads only when packet shape or checksum/segmentation behavior is implicated.

## Precise Drop Location

Use only for hard cases where counters do not locate drops.

Tools may include:

```bash
dropwatch
perf trace
bpftrace
tc -s qdisc show
```

Interpretation:

- qdisc drops: egress queue or shaping.
- driver/NIC drops: ring, interrupt, or hardware pressure.
- TCP memory drops: socket buffer or kernel memory pressure.
- Firewall drops: policy or conntrack state.

Use short windows and document overhead/permission requirements.

## TCP and Kernel Memory Pressure

```bash
ss -m
cat /proc/net/sockstat
cat /proc/net/sockstat6
sysctl net.ipv4.tcp_mem net.ipv4.tcp_rmem net.ipv4.tcp_wmem
```

Interpretation:

- Many sockets with large buffers can create memory pressure.
- TCP memory pressure can cause drops or poor throughput.
- Socket buffer tuning should follow evidence, not generic throughput advice.

## NAT and Conntrack

```bash
cat /proc/sys/net/netfilter/nf_conntrack_count
cat /proc/sys/net/netfilter/nf_conntrack_max
cat /proc/net/stat/nf_conntrack
conntrack -S
ss -s
iptables -t nat -vnL
nft list ruleset
```

Interpretation:

- Count near max: new flows may fail.
- Insert failed/drop counters increasing: conntrack pressure.
- Short-connection storms: connection churn can dominate CPU and table pressure.
- NAT gateway path can become a shared bottleneck for unrelated services.

Mitigation after evidence:

- Reuse connections through keepalive or pooling.
- Scale NAT gateway capacity or distribute egress.
- Increase conntrack max only after memory impact is understood.
- Tune timeouts only when flow lifetime and protocol behavior are known.

Warning:

- NAT timestamp behavior and old TCP timestamp assumptions can break clients behind NAT. Do not recommend obsolete `tcp_tw_recycle`.

## MTU, MSS, and Segmentation Performance

For MTU correctness, see `network-routing-arp-mtu.md`. For performance:

- Large segmentation offloads can reduce CPU, but may affect capture interpretation.
- Overlay networks reduce effective MTU and can create hidden fragmentation or blackholes.
- MSS clamping may be needed at boundaries when PMTUD is blocked.

## Window and Long-Fat Networks

Use when file transfer or replication is slow over high-latency high-bandwidth paths.

Check:

```bash
ss -ti dst <peer-ip>
sysctl net.ipv4.tcp_window_scaling
sysctl net.ipv4.tcp_rmem net.ipv4.tcp_wmem
```

Interpretation:

- Throughput ceiling may be limited by bandwidth-delay product.
- Receive window too small can cap throughput.
- Packet loss on long-fat paths drastically reduces TCP throughput.

Fix direction:

- Confirm window scaling and socket buffer behavior.
- Reduce loss first.
- Tune buffers only after BDP and application read/write behavior are understood.

## Pressure-Test TPS Ceiling

Use when load testing cannot reach expected TPS.

Check layers:

- Client generator CPU, ports, file descriptors, and connection reuse.
- Load balancer connection and backend distribution.
- Server CPU, accept queue, worker pool, GC, IO, and downstream dependencies.
- Network retransmission, softirq, NIC drops, and conntrack.

Commands:

```bash
ss -s
sar -n DEV,TCP,ETCP 1 5
mpstat -P ALL 1 5
pidstat -u -d -w 1 5
```

Interpretation:

- Client bottleneck can masquerade as server limit.
- Short-connection tests can hit ephemeral ports, TIME_WAIT, conntrack, or accept queues.
- Average latency can look acceptable while P99 reveals queueing.

## Load Balancing and ECMP Anomalies

Use when traffic distribution is uneven or failures are path-specific.

Evidence:

- Per-backend request counts and connection counts.
- Source IP/port distribution.
- LB algorithm and persistence/stickiness.
- ECMP hash fields and path counters.
- Success/failure grouped by client AZ, node, backend, or route.

Interpretation:

- Few client source IPs can make hash-based balancing uneven.
- Long-lived connections reduce rebalance effectiveness.
- ECMP can create path-specific loss or reordering.
- NAT can collapse many clients into few source tuples.

## DDoS and Unwanted Traffic Shape

Use when packet rate, SYN_RECV, conntrack, or NIC/softirq suddenly rises.

```bash
sar -n DEV,TCP,ETCP 1 5
ss -s
tcpdump -i <iface> -nn -s 96 -c 10000
nstat -az | grep -Ei 'Listen|Syncookies|Retrans|InErr|OutRst'
```

Analyze shape:

- SYN flood: SYN_RECV, syncookies, many sources or spoofed sources.
- UDP flood: high packet rate, non-application ports, NIC/softirq pressure.
- HTTP flood: normal TCP but application request rate or expensive endpoints.
- Reflection/amplification: unexpected source ports and response patterns.

Mitigation direction:

- Prefer upstream filtering, CDN/WAF, provider DDoS protection, or edge rate limits.
- Host-level tuning alone rarely solves large unwanted traffic.
- Preserve samples and counters for provider escalation.
