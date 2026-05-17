# DNS, HTTP, Nginx, and TLS Troubleshooting

Use this reference when phase timing points to DNS, HTTP status codes, Nginx logs, connection resets, TLS handshake failures, certificate/SNI issues, or HTTPS decryption needs.

## DNS Checks

```bash
cat /etc/resolv.conf
dig <name>
dig @<dns-server> <name>
time getent hosts <name>
tcpdump -i any -nn -s 128 'port 53'
```

Interpretation:

- Slow `time_namelookup`: DNS path is implicated.
- Multiple search-domain queries: `search` and `ndots` expansion can multiply latency.
- UDP retries before success: packet loss, resolver overload, firewall, or truncation.
- TCP fallback: large DNS response, DNSSEC, truncation, or UDP path issue.
- Different answers from different resolvers: cache, split-horizon DNS, or stale records.

## Kubernetes/CoreDNS

```bash
cat /etc/resolv.conf
dig <svc>.<namespace>.svc.cluster.local
dig @<coredns-ip> <name>
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
kubectl -n kube-system logs deploy/coredns
```

Patterns:

- `ndots:5` causes short external names to be tried with cluster search suffixes first.
- CoreDNS CPU throttling can create DNS tail latency.
- Upstream resolver latency can make CoreDNS look slow.
- Node-local DNS can change failure domain and cache behavior.

## Translate Logs into Protocol Phases

Map symptoms:

| Symptom | Likely phase | Evidence |
|---|---|---|
| DNS timeout | Lookup | `curl -w`, `dig`, port 53 capture |
| Connect timeout | TCP handshake | SYN/SYN-ACK capture |
| TLS alert | TLS handshake | `openssl s_client`, TLS pcap |
| HTTP 499 | Client closed early | Nginx log timing, FIN/RST direction |
| HTTP 400 | Request parse, header, protocol mismatch | Nginx error log, capture |
| `connection reset by peer` | Peer sent RST | pcap RST source and timing |
| Slow TTFB | App/upstream/proxy processing | access logs, upstream timing |

## Nginx 499

Nginx `499` means the client closed the connection before Nginx sent the response.

Useful fields:

```nginx
$request_time
$upstream_response_time
$upstream_connect_time
$upstream_header_time
$status
$body_bytes_sent
```

Interpretation:

- Client timeout shorter than server processing time.
- Client/network closed during slow upstream response.
- Load balancer or gateway idle timeout closed connection.
- Packet capture should show whether client/LB sends FIN/RST before response.

Fix direction:

- Align client, gateway, Nginx, and upstream timeouts.
- Reduce upstream latency or queueing.
- Increase timeout only after confirming expected request duration.

## `connection reset by peer`

Use packet capture to identify the side that sent RST.

```bash
tcpdump -i any -nn -s 96 'tcp[tcpflags] & tcp-rst != 0 and host <peer>'
```

Interpretation:

- RST from client after response: client may abort or close pooled connection.
- RST from server immediately after SYN: no listener, firewall reject, or service reset.
- RST after idle period: LB/NAT/firewall idle timeout or stale pooled connection.
- RST during request body: server/proxy rejected upload or application closed.

## HTTP 400 and Browser Anomalies

Possible causes:

- Invalid Host header or SNI mismatch.
- Header too large.
- Plain HTTP sent to HTTPS port, or TLS sent to HTTP port.
- Proxy rewrites or malformed request line.
- Browser/plugin/frontend changed request shape.

Checks:

```bash
curl -v --http1.1 http://<host>:<port>/
curl -vk https://<host>/
openssl s_client -connect <host>:443 -servername <host>
```

## TLS Handshake Failures

```bash
openssl s_client -connect <host>:443 -servername <host> -showcerts
openssl s_client -connect <host>:443 -servername <host> -tls1_2
openssl s_client -connect <host>:443 -servername <host> -tls1_3
tshark -r /tmp/issue.pcap -Y 'tls.handshake or tls.alert' \
  -T fields -e frame.number -e tls.handshake.type -e tls.alert_message.desc
```

Interpretation:

- Failure before ServerHello: SNI, protocol version, cipher, or listener mismatch.
- Certificate error: chain, expiration, hostname, trust store.
- Alert from server: server policy, client auth, SNI, protocol mismatch.
- Alert from client: client trust, hostname validation, or app policy.

## HTTPS Decryption

Prefer metadata and endpoint logs. Decryption is sensitive and often unnecessary.

Options:

- Browser `SSLKEYLOGFILE` for client-side TLS key logging in controlled tests.
- Server-side TLS termination logs.
- Test environment with temporary keys.

Cautions:

- Do not request production private keys.
- Decrypted traffic can expose credentials and user data.
- Store decrypted pcaps with strict access control and delete when no longer needed.
