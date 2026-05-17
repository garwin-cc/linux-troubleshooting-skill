# Network Troubleshooting Reference

Use this reference after the 60-second snapshot points to latency, timeout, packet loss, retransmission, DNS, TLS, HTTP status codes, NAT/conntrack, NIC drops, routing, MTU, firewall, Kubernetes networking, or softirq packet pressure.

## Deep References

Load these only when the broad network path points there:

- `network-packet-analysis.md`: tcpdump/Wireshark/tshark workflows, dual-ended captures, retransmission judgment, intermittent capture strategy, and privacy controls.
- `network-tcp-queues-timeouts.md`: listen backlog, SYN drops, accept queue overflow, TIME_WAIT, port exhaustion, idle connection failures, and fd exhaustion.
- `network-routing-arp-mtu.md`: route/source selection, rp_filter, firewall counters, ARP/neighbor, MTU/MSS, fragmentation.
- `network-container-k8s.md`: namespace/veth/bridge/overlay checks, pod and service routing, kube-proxy, CoreDNS, CNI-specific checks.
- `network-http-tls-dns.md`: DNS search domains, CoreDNS, Nginx 499, connection reset by peer, HTTP 400, TLS alerts, HTTPS decryption.
- `network-nic-conntrack-performance.md`: NIC/driver drops, softnet backlog, drop tracing, conntrack/NAT, long-fat networks, LB/ECMP, DDoS, pressure-test ceilings.

## Core Model

Classify every network incident by:

- **Where**: client, client kernel/NIC, DNS, proxy/load balancer, NAT gateway, server kernel/NIC, server application, or intermediate network.
- **Protocol phase**: DNS lookup, TCP handshake, TLS handshake, request send, server processing, response return, client read.
- **Resource**: CPU, softirq, socket queues, connection backlog, conntrack table, ephemeral ports, file descriptors, bandwidth, MTU, NIC queue, application workers.

Do not jump straight to tuning. First prove the phase and segment.

## First Commands

```bash
(command -v ip >/dev/null && ip addr) || ifconfig -a 2>/dev/null || cat /proc/net/dev
(command -v ip >/dev/null && ip route) || netstat -rn 2>/dev/null || route -n 2>/dev/null || cat /proc/net/route
(command -v ip >/dev/null && ip route get <peer-ip>) || true
(command -v ss >/dev/null && ss -s) || netstat -s 2>/dev/null || cat /proc/net/sockstat
(command -v ss >/dev/null && ss -antpi) || netstat -antp 2>/dev/null || cat /proc/net/tcp
(command -v nstat >/dev/null && nstat -az) || (cat /proc/net/snmp; cat /proc/net/netstat)
cat /proc/net/snmp
(command -v ip >/dev/null && ip -s link) || cat /proc/net/dev
(command -v sar >/dev/null && sar -n DEV,TCP,ETCP 1 5) || (cat /proc/net/dev; cat /proc/net/snmp; cat /proc/net/netstat)
curl -v -w '%{time_namelookup} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}\n' <url>
```

Fallback impact: `/proc/net/*` preserves TCP and interface counters, but socket ownership, queue detail, and policy route context may be weaker than `ss` and `ip`.

Packet truth:

```bash
tcpdump -i any -nn -s 96 'host <peer> and port <port>'
tcpdump -i any -nn -s 96 -w /tmp/issue.pcap 'host <peer> and port <port>'
```

Use full packet capture only when needed:

```bash
tcpdump -i <iface> -nn -s 0 -w /tmp/full.pcap 'host <peer> and port <port>'
```

## Decision Table

| Symptom | Evidence | Meaning | Next step |
|---|---|---|---|
| Connect timeout | `curl -v`, packet capture, SYN behavior | DNS, route, firewall, backlog, NAT, return path | Split SYN/SYN-ACK/final ACK/app phase |
| Slow request | `curl -w` phase timings | DNS/connect/TLS/server/response phase | Focus on slow phase |
| Retransmission | `sar -n TCP,ETCP`, `nstat`, pcap | Loss, reordering, receiver drop, congestion | Identify affected flow and packet pattern |
| High packet rate | `sar -n DEV`, `%si`, `softnet_stat` | NIC/softirq pressure | Check queues, drops, RSS/RPS, interrupts |
| DNS slow/failing | `dig`, tcpdump port 53 | Resolver latency/retry/search-domain/UDP loss | Compare resolvers and query path |
| NAT/conntrack pressure | conntrack count near max, drops | Gateway state table pressure | Inspect counts, drops, short connection storm |
| MTU/path failure | PMTU blackhole, fragmentation | MTU/MSS/routing/firewall issue | `tracepath`, `ping -M do`, captures |
| HTTP/TLS symptom | 499/400/5xx/TLS alert | App/proxy/TLS phase issue | Translate status to stream timing and close direction |

## Latency Phase Branch

Use `curl -w` or application timings to split the request.

For HTTP, TLS, and DNS details, read `network-http-tls-dns.md`.

```bash
curl -o /dev/null -sS -v \
  -w 'dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  https://<host>/<path>
```

Interpretation:

- `time_namelookup` high: DNS branch.
- `time_connect` high: TCP handshake, route, firewall, backlog, SYN loss.
- `time_appconnect` high: TLS handshake, certificate, crypto, server/proxy delay.
- `time_starttransfer` high: server processing, upstream dependency, proxy queueing, request body upload, application worker saturation.
- `time_total` high after TTFB: response body transfer, bandwidth, receive window, client read, or packet loss.

## Connect Timeout Branch

For deeper TCP queue, timeout, TIME_WAIT, and fd-limit details, read `network-tcp-queues-timeouts.md`.

| Evidence | Interpretation | Next step |
|---|---|---|
| Client does not send SYN | DNS, route, local port, file descriptor, or app issue | Check `curl -w`, `ip route get`, `ss -s`, fd limits |
| Client sends SYN; server never sees it | Forward path, firewall, LB, NAT, security policy | Capture at LB/server and inspect route/firewall counters |
| Server sees SYN but no SYN-ACK | Listener, SYN backlog, firewall, softirq/NIC drop, SYN flood | Check `ss -lnt`, SYN_RECV, dmesg, NIC drops |
| Server sends SYN-ACK; client never sees it | Return path, asymmetric route, NAT/firewall/rp_filter | Capture both ends; check route and policy |
| Handshake completes but timeout remains | TLS/app handshake or request latency | Use `curl -w` and app/proxy logs |

Commands:

```bash
ss -lntp
ss -tan state syn-recv
ss -tan state time-wait
ss -tan state close-wait
cat /proc/sys/net/ipv4/ip_local_port_range
ulimit -n
```

Listen backlog interpretation:

- In `ss -lnt`, `Recv-Q` for a listening socket is completed connections waiting for `accept()`.
- `Send-Q` is the effective backlog limit.
- If `Recv-Q` approaches `Send-Q`, inspect application accept loop and worker saturation before raising backlog.

## Retransmission and Packet Loss Branch

For packet workflow details, read `network-packet-analysis.md`.

```bash
sar -n TCP,ETCP 1 5
nstat -az | grep -Ei 'Retrans|Listen|Timeout|InErr|OutRst'
netstat -s | grep -Ei 'retrans|timeout|listen|reset'
tcpdump -i any -nn -s 96 -w /tmp/retrans.pcap 'host <peer> and port <port>'
```

Interpretation:

- Retransmission after duplicate ACKs: likely packet loss or reordering on the path.
- Retransmission after RTO: packet or ACK was missing long enough to hit timeout.
- Receiver window zero or small: receiver/application is not reading fast enough.
- Many resets: determine which side sends RST and at which phase.
- Retransmission is flow-specific: identify whether it affects all peers, one AZ, one client, one server, or one route.

Next steps:

- Capture near both endpoints if path ownership is unclear.
- Compare successful and failed flows.
- Inspect NIC drops, softnet drops, firewall/LB drops, and provider metrics.

## NIC, Softirq, and Kernel Drop Branch

Use when packet rate is high, `%si` is high, one CPU is hot, or interface drops/errors are increasing.

For NIC drops, conntrack, NAT, throughput, DDoS, and load-balancing details, read `network-nic-conntrack-performance.md`.

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

- RX/TX drops at interface: NIC, driver, ring, or qdisc may be dropping.
- `softnet_stat` drops or time_squeeze: kernel backlog processing is falling behind.
- Interrupts concentrated on one CPU: check RSS queue count and IRQ affinity.
- High packet rate with low bandwidth: packets per second, not bytes per second, is the bottleneck.

Fix direction:

- Rebalance queues with RSS/RPS/XPS only after proving imbalance.
- Increase ring buffers only after drops point to ring pressure and rollback is clear.
- Scale out or reduce small-packet traffic if packet rate exceeds host capacity.

## DNS Branch

For DNS, HTTP, and TLS details, read `network-http-tls-dns.md`.

```bash
dig <name>
dig @<dns-server> <name>
time getent hosts <name>
tcpdump -i any -nn -s 128 'port 53'
cat /etc/resolv.conf
```

Interpretation:

- Slow first lookup but fast repeated lookup: resolver or cache behavior.
- Multiple queries from search domains: `ndots` or search suffix expansion.
- UDP retries then TCP fallback: UDP loss, truncation, or firewall issue.
- Kubernetes DNS slow: inspect CoreDNS latency, pod DNS config, node-local DNS, and upstream resolver.

## NAT and Conntrack Branch

For NAT, conntrack, packet-shape, and gateway performance details, read `network-nic-conntrack-performance.md`.

```bash
cat /proc/sys/net/netfilter/nf_conntrack_count
cat /proc/sys/net/netfilter/nf_conntrack_max
cat /proc/net/stat/nf_conntrack
conntrack -S
ss -s
```

Interpretation:

- Count near max: new connections may be dropped.
- Insert/drop/fail counters increasing: conntrack is under pressure.
- Short-connection storms: ephemeral port and conntrack churn may dominate.
- NAT gateway pressure can affect many unrelated services.

Fix direction:

- Reduce short connections through keepalive or pooling.
- Scale NAT gateways or move NAT closer to workload.
- Raise conntrack only after memory impact and connection churn are understood.

## Routing, ARP, Firewall, and MTU Branch

For deeper routing, ARP, firewall, rp_filter, MTU, MSS, and fragmentation details, read `network-routing-arp-mtu.md`.

```bash
ip route get <peer-ip>
ip neigh show
ip -s neigh show
tracepath <peer-ip>
ping -M do -s <size> <peer-ip>
iptables -L -n -v
nft list ruleset
sysctl net.ipv4.conf.all.rp_filter
```

Interpretation:

- Asymmetric routing: SYN/SYN-ACK may take different paths and hit rp_filter or firewall state.
- Neighbor failures: ARP/ND resolution or L2 path issues.
- MTU blackhole: large packets fail while small packets work; TLS or large responses may hang.
- Firewall counters increasing: policy may explain drops better than host metrics.

## HTTP and TLS Branch

For DNS, HTTP, Nginx, and TLS details, read `network-http-tls-dns.md`.

```bash
curl -v --resolve <host>:<port>:<ip> https://<host>/
openssl s_client -connect <host>:443 -servername <host> -showcerts
tshark -r /tmp/issue.pcap -Y 'tls.handshake or tls.alert' -T fields -e frame.number -e tls.handshake.type -e tls.alert_message.desc
```

Interpretation:

- HTTP 499 often means the client closed before the server responded; confirm with timing and packet close direction.
- HTTP 400 may be malformed request, header size, proxy behavior, or TLS/plain HTTP mismatch.
- TLS alert phase matters: certificate, SNI, protocol/cipher mismatch, client auth, or backend close.
- `--resolve` separates DNS from server/TLS behavior.

## Safety Notes

- Packet captures can expose tokens, cookies, credentials, payloads, and user data. Use narrow filters, short duration, minimal snap length, secure storage, and redaction.
- Full `conntrack -L` can be expensive on busy gateways. Prefer counters first.
- Do not recommend `tcp_tw_recycle`.
- Do not tune backlog, buffer sizes, MTU, conntrack, or queue settings before the bottleneck is proven.
