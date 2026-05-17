# Container and Kubernetes Networking

Use this reference for pod-to-pod, pod-to-service, node-to-pod, DNS in Kubernetes, overlay MTU, CNI, kube-proxy, veth, bridge, namespace, or container-specific networking issues.

## Diagnosis Order

1. Identify the failing tuple: source pod/node/IP, destination pod/service/IP, protocol, port, and time window.
2. Test direct pod IP, service IP, DNS name, and external LB separately.
3. Compare same-node pod-to-pod vs cross-node pod-to-pod.
4. Inspect DNS, kube-proxy/service routing, CNI overlay, node firewall, and host network counters.

## Namespace and Interface Mapping

On the node:

```bash
crictl ps | grep <pod>
crictl inspect <container-id> | grep -i pid
nsenter -t <pid> -n ip addr
nsenter -t <pid> -n ip route
nsenter -t <pid> -n ss -s
```

Docker fallback:

```bash
docker inspect <container> --format '{{.State.Pid}}'
nsenter -t <pid> -n ip addr
```

Interpretation:

- Pod network namespace can have different routes, DNS config, and socket state than the node.
- veth peer mapping is needed to connect pod counters to host interfaces.

## veth, Bridge, and Overlay Checks

```bash
ip link
bridge link
bridge fdb show
ip -s link
ethtool -S <iface>
```

Overlay and CNI:

```bash
ip route
ip rule
ip tunnel show
ip -d link show
```

Interpretation:

- veth drops can indicate pod or host-side queue pressure.
- Overlay encapsulation reduces effective MTU.
- Cross-node failures often point to overlay, routing, security group, or underlay path.

## Pod and Service Routing

```bash
kubectl get pod -o wide
kubectl get svc,endpoints,endpointslice
kubectl describe svc <service>
kubectl get nodes -o wide
```

Inside a pod:

```bash
ip route
cat /etc/resolv.conf
curl -v <service-name>.<namespace>.svc.cluster.local:<port>
curl -v <pod-ip>:<port>
```

Interpretation:

- Service has no endpoints: selector/readiness issue.
- Endpoint exists but pod IP fails: pod, CNI, network policy, or application listener.
- Pod IP works but service IP fails: kube-proxy, iptables/nft/IPVS, or service routing.
- Service name fails but service IP works: DNS/CoreDNS branch.

## kube-proxy and Service Dataplane

iptables mode:

```bash
iptables-save | grep -E 'KUBE-SVC|KUBE-SEP|<service-ip>'
```

IPVS mode:

```bash
ipvsadm -Ln 2>/dev/null
```

Interpretation:

- Missing service rules can indicate kube-proxy issue.
- Endpoint rules missing can indicate endpoint readiness or controller lag.
- Node-local behavior can differ if kube-proxy state diverges between nodes.

## CNI-Specific Checks

General:

```bash
kubectl -n kube-system get pods -o wide
kubectl -n kube-system logs <cni-pod>
kubectl get networkpolicy -A
```

Common patterns:

- Calico: check BGP peering, Felix logs, IPIP/VXLAN mode, network policy.
- Flannel: check VXLAN device, subnet leases, MTU.
- Cilium: check agent health, endpoint status, policy verdicts, Hubble if available.

## Overlay MTU

```bash
ip link show
tracepath <pod-or-node-ip>
ping -M do -s <size> <peer-ip>
```

Interpretation:

- Overlay encapsulation reduces payload MTU below underlay MTU.
- Symptoms often appear as large responses hanging, TLS stalls, or intermittent gRPC failures.
- MSS clamping or correct CNI MTU can fix proven MTU blackholes.

## Kubernetes DNS

```bash
cat /etc/resolv.conf
dig <service>.<namespace>.svc.cluster.local
dig @<coredns-ip> <name>
kubectl -n kube-system logs deploy/coredns
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
```

Interpretation:

- `ndots:5` can create multiple search-domain queries.
- CoreDNS latency can be upstream resolver, plugin, or node-local DNS issue.
- DNS failures can look like connect timeout unless phase timing separates lookup.
