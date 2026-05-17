# Network Packet Analysis

Use this reference when counters and logs are ambiguous, when retransmission/loss/reset behavior must be proven, or when an intermittent incident needs capture strategy.

## Mental Model

Packet capture answers:

- Did the packet leave this host?
- Did the packet arrive at the peer or next hop?
- Which side sent retransmissions, FIN, RST, or TLS alerts?
- Which protocol phase consumed time?
- Is the problem consistent across successful and failed flows?

Use packet capture to confirm, not to replace, application logs and counters.

## Privacy and Capture Safety

Packet captures can contain credentials, cookies, tokens, request bodies, headers, customer data, and internal topology.

Default safety:

- Use tight host/port filters.
- Use short capture windows.
- Use snap length `-s 96` or `-s 128` for TCP behavior when payload is not needed.
- Use full snap length only when HTTP/TLS details or payload inspection is explicitly authorized.
- Store pcaps in restricted paths and redact before sharing.

## tcpdump Capture Patterns

Header-only TCP behavior:

```bash
tcpdump -i <iface> -nn -s 96 -w /tmp/headers.pcap 'host <peer> and port <port>'
```

Live readable summary:

```bash
tcpdump -i <iface> -nn -tttt 'host <peer> and port <port>'
```

Full packets when authorized:

```bash
tcpdump -i <iface> -nn -s 0 -w /tmp/full.pcap 'host <peer> and port <port>'
```

Rotating capture for intermittent incidents:

```bash
tcpdump -i <iface> -nn -s 128 -w /tmp/issue-%Y%m%d%H%M%S.pcap \
  -G 60 -W 20 'host <peer> and port <port>'
```

DNS:

```bash
tcpdump -i any -nn -s 128 'port 53'
```

## Wireshark Workflow

Use display filters:

```text
tcp.stream eq <n>
tcp.analysis.retransmission
tcp.analysis.fast_retransmission
tcp.analysis.duplicate_ack
tcp.analysis.zero_window
tcp.flags.reset == 1
tls.alert_message
dns
http
```

Questions to answer:

- Which side starts the flow?
- Is the three-way handshake complete?
- Are retransmissions after duplicate ACKs or after timeout?
- Does a zero window or tiny receive window appear?
- Which side sends FIN or RST?
- Does DNS retry before success?
- Does TLS fail before or after certificate exchange?

## tshark Extraction

HTTP responses:

```bash
tshark -r /tmp/issue.pcap -Y 'http.response' \
  -T fields -e frame.time_relative -e ip.src -e ip.dst -e http.response.code
```

TCP resets:

```bash
tshark -r /tmp/issue.pcap -Y 'tcp.flags.reset == 1' \
  -T fields -e frame.time_relative -e ip.src -e ip.dst -e tcp.srcport -e tcp.dstport
```

TLS alerts:

```bash
tshark -r /tmp/issue.pcap -Y 'tls.alert_message' \
  -T fields -e frame.number -e frame.time_relative -e ip.src -e tls.alert_message.desc
```

DNS timing:

```bash
tshark -r /tmp/dns.pcap -Y 'dns' \
  -T fields -e frame.time_relative -e ip.src -e ip.dst -e dns.qry.name -e dns.flags.response -e dns.time
```

## Dual-Ended Comparison

Use when the issue may be between hosts or across a load balancer.

Capture at both ends using synchronized time if possible:

```bash
tcpdump -i any -nn -s 96 -w /tmp/client.pcap 'host <server> and port <port>'
tcpdump -i any -nn -s 96 -w /tmp/server.pcap 'host <client> and port <port>'
```

Interpretation:

- Packet appears at client but not server: forward path, firewall, LB, NAT, route, or intermediate drop.
- Packet appears at server but not client: return path, asymmetric route, firewall, LB, NAT, or rp_filter.
- Packet appears at both ends but timing differs: queueing in path, LB/proxy processing, or clock skew if clocks are not synchronized.
- Server responds slowly after request arrives: move to application/server resource branch.

## TCP Retransmission Judgment

Retransmission is a symptom. Determine the pattern:

| Pattern | Meaning | Next step |
|---|---|---|
| Fast retransmission after duplicate ACKs | Receiver saw later packets but missed one segment | Search for path loss, reordering, receiver drops |
| Retransmission after RTO | No ACK arrived before timeout | Check path loss, firewall, severe queueing |
| Duplicate ACKs without actual loss | Reordering may be present | Check ECMP, NIC offload, path changes |
| Zero window | Receiver is not reading or receive buffer is full | Inspect receiver application and socket buffers |
| Out-of-order packets | Path reordering or capture artifact | Compare both ends, ECMP, LRO/GRO offload |

## Intermittent Incidents

For low-frequency failures:

- Estimate failure rate first.
- Capture only the affected tuple if possible.
- Use rotating captures and application timestamps.
- Preserve success and failure samples.
- Correlate with logs, request IDs, and host counters.

Trigger examples:

- Start capture during a synthetic probe window.
- Rotate every minute and keep enough history.
- Use application logs to identify the pcap file covering the failure.
