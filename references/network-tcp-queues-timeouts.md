# TCP Queues and Timeouts

Use this reference for connection timeout, backlog overflow, SYN drops, accept queue saturation, TIME_WAIT pressure, idle connection failures, port exhaustion, or file descriptor exhaustion.

## LISTEN Queue Semantics

```bash
ss -lnt
ss -lntp
```

For LISTEN sockets:

- `Recv-Q`: completed connections waiting for the application to call `accept()`.
- `Send-Q`: effective listen backlog.

If `Recv-Q` approaches `Send-Q`, the application accept loop or workers may be saturated. Do not only raise `somaxconn`; first check application capacity.

## SYN Drops and Handshake Problems

```bash
ss -tan state syn-recv
netstat -s | grep -Ei 'listen|syn|reset|retrans|overflow'
nstat -az | grep -Ei 'Listen|Syncookies|Retrans|Timeout'
sysctl net.ipv4.tcp_max_syn_backlog net.ipv4.tcp_syncookies net.core.somaxconn
```

Interpretation:

- SYN_RECV grows: incomplete handshakes, SYN flood, return-path loss, or backlog pressure.
- Syncookies increasing: SYN backlog pressure or attack-like pattern.
- Listen overflows/drops: accept queue or application accept loop problem.
- Server sends SYN-ACK but final ACK does not arrive: return path or client-side issue.

## TCP Connect Timeout Branch

| Evidence | Interpretation | Next step |
|---|---|---|
| Client does not emit SYN | App, DNS, route, local port, or fd issue | `curl -w`, `ip route get`, `ss -s`, fd limits |
| Client emits SYN; server/LB never sees it | Forward path or policy drop | Capture at LB/server, inspect firewall/LB counters |
| Server sees SYN but sends no SYN-ACK | Listener, SYN backlog, host firewall, softirq/NIC drop | `ss -lnt`, SYN_RECV, `netstat -s`, NIC drops |
| Server sends SYN-ACK; client never sees it | Return path, asymmetric route, NAT, firewall, rp_filter | Capture both sides, route and policy checks |
| Handshake completes, timeout remains | TLS/app phase or wrong timeout classification | Use `curl -w`, proxy and application logs |

## Accept Queue Overflow

Evidence:

```bash
ss -lnt
nstat -az | grep -Ei 'ListenOverflows|ListenDrops'
netstat -s | grep -Ei 'listen|overflow|dropped'
```

Common causes:

- Application does not call `accept()` fast enough.
- Worker pool is saturated after accept.
- CPU throttling or GC pauses delay accept.
- Backlog argument in application is too low.
- `somaxconn` limits the effective backlog.

Fix direction:

- Fix application worker saturation or accept loop first.
- Increase application backlog and `net.core.somaxconn` only when the application can drain the queue.
- Add replicas or load-balance away from saturated nodes.

## TIME_WAIT and Port Exhaustion

```bash
ss -s
ss -tan state time-wait | wc -l
sysctl net.ipv4.ip_local_port_range net.ipv4.tcp_tw_reuse
```

Interpretation:

- Many TIME_WAIT sockets are normal for active connection closers.
- Port exhaustion happens when the client side creates many short outbound connections to the same tuple.
- TIME_WAIT count alone is not a root cause; correlate with connection failures and port range.

Fix direction:

- Reuse connections through keepalive or pooling.
- Increase client-side ephemeral port range if exhaustion is proven.
- Spread connections across more source IPs or destination tuples.
- Do not recommend `tcp_tw_recycle`.

## Idle Connection Failures

Use when long-lived connections reset after being idle.

```bash
ss -ti dst <peer-ip>
sysctl net.ipv4.tcp_keepalive_time net.ipv4.tcp_keepalive_intvl net.ipv4.tcp_keepalive_probes
```

Likely causes:

- Load balancer idle timeout.
- NAT/firewall idle state expiry.
- Application heartbeat interval longer than network idle timeout.
- Half-open connections after peer restart or path change.

Fix direction:

- Align application heartbeat and TCP keepalive with the shortest idle timeout in the path.
- Prefer application-level heartbeats when protocol semantics need fast failure detection.
- Avoid overly aggressive keepalive that creates unnecessary packet load.

## File Descriptor Exhaustion

```bash
ulimit -n
cat /proc/<pid>/limits
ls /proc/<pid>/fd | wc -l
lsof -p <pid> | wc -l
ss -s
```

Interpretation:

- `EMFILE` or `Too many open files`: per-process fd limit.
- Many sockets in `CLOSE_WAIT`: application is not closing sockets after peer close.
- Many open files: leak or workload growth.

Fix direction:

- Fix fd/socket leaks before raising limits.
- Raise process and system limits only after expected concurrency is calculated.
- Add monitoring for fd count and socket states.
