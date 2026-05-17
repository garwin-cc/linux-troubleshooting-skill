# Routing, ARP, Firewall, MTU, and Fragmentation

Use this reference for path-specific failures, asymmetric routing, ARP/neighbor issues, firewall drops, reverse path filtering, MTU blackholes, MSS problems, or fragmentation.

## Route and Source Selection

```bash
ip route
ip rule
ip route get <peer-ip>
ip addr
```

Interpretation:

- `ip route get` shows selected source IP, interface, gateway, and route.
- Wrong source IP can break security groups, ACLs, return routing, or application allowlists.
- Policy routing (`ip rule`) can make route behavior differ by source, mark, or table.

## Reverse Path Filtering

```bash
sysctl net.ipv4.conf.all.rp_filter
sysctl net.ipv4.conf.default.rp_filter
sysctl net.ipv4.conf.<iface>.rp_filter
```

Interpretation:

- Strict `rp_filter` can drop packets when return path differs from receive path.
- Asymmetric routing through load balancers, multiple NICs, VPNs, or ECMP can trigger this.

Fix direction:

- Prove asymmetric route first with captures and `ip route get`.
- Prefer fixing routing symmetry when possible.
- If changing `rp_filter`, document scope, persistence, and rollback.

## Firewall Counters

```bash
iptables -L -n -v
iptables -t nat -L -n -v
nft list ruleset
```

Interpretation:

- Increasing drop/reject counters on the relevant chain can prove policy drop.
- NAT rules can rewrite source or destination and change where replies go.
- Rule order matters.

Caution:

- Do not change firewall rules as a diagnostic shortcut. Capture counters and propose a reversible change.

## ARP and Neighbor

```bash
ip neigh show
ip -s neigh show
arping -I <iface> <peer-ip>
```

Interpretation:

- `FAILED` or repeated `INCOMPLETE`: neighbor resolution problem.
- Rapid neighbor churn: L2 instability, duplicate IP, ARP flux, or gateway issue.
- Stale neighbor entries can create intermittent failures.

## MTU, MSS, and Fragmentation

```bash
tracepath <peer-ip>
ping -M do -s <size> <peer-ip>
ip link show <iface>
ip route get <peer-ip>
```

Interpretation:

- Small packets work but large transfers stall: MTU/MSS blackhole is plausible.
- `ping -M do` fails at sizes below expected path MTU: path cannot carry that packet size without fragmentation.
- Overlay networks reduce effective MTU.
- Firewalls that drop ICMP fragmentation-needed messages can break PMTUD.

Fix direction:

- Set correct MTU on overlay/underlay interfaces.
- Clamp TCP MSS at the edge when needed.
- Allow ICMP fragmentation-needed messages where possible.
- Validate with both packet capture and application transfer test.

## Fragmentation and Offload Caveat

NIC offloads can make captures on the sending host look like large packets even though the NIC segments them later.

Check offloads:

```bash
ethtool -k <iface>
```

Interpretation:

- TSO/GSO/GRO/LRO can change how packet sizes appear in local capture.
- Capture on both sides when MTU behavior is ambiguous.
