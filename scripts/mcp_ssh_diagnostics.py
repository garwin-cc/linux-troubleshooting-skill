#!/usr/bin/env python3
"""Read-only SSH diagnostics MCP server for linux-troubleshooting.

This server intentionally exposes a small surface:
- list configured hosts
- run predefined read-only diagnostic bundles over SSH

It does not provide arbitrary remote command execution.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SERVER_NAME = "linux-ssh-diagnostics"
SERVER_VERSION = "0.1.0"

DEFAULT_CONFIG = "~/.config/linux-troubleshooting/ssh-hosts.json"


CommandSpec = dict[str, Any]


def cmd(
    command_id: str,
    command: str,
    fallbacks: list[str] | None = None,
    allowed_exit_codes: list[int] | None = None,
) -> CommandSpec:
    return {
        "id": command_id,
        "command": command,
        "fallbacks": fallbacks or [],
        "allowed_exit_codes": allowed_exit_codes or [0],
    }


BUNDLES: dict[str, list[CommandSpec]] = {
    "snapshot_60s": [
        cmd("identity_uptime", "date; hostname; uptime"),
        cmd("kernel_recent", "dmesg -T | tail -80", ["journalctl -k --no-pager -n 80 2>/dev/null || true"]),
        cmd("vmstat", "vmstat 1 5", ["cat /proc/loadavg; head -5 /proc/stat; grep -E 'pgscan|pgsteal|pswp|pgmajfault' /proc/vmstat"]),
        cmd("mpstat", "mpstat -P ALL 1 3", ["grep '^cpu' /proc/stat | head -40"]),
        cmd(
            "pidstat_all",
            "pidstat -u -d -r -w 1 5",
            ["ps -eo pid,ppid,state,comm,pcpu,pmem,rss,vsz,wchan:24 --sort=-pcpu | head -40"],
        ),
        cmd("iostat", "iostat -xz 1 5", ["cat /proc/diskstats"]),
        cmd("free", "free -h", ["grep -E 'MemTotal|MemAvailable|MemFree|Cached|Buffers|SwapTotal|SwapFree' /proc/meminfo"]),
        cmd("sar_network", "sar -n DEV,TCP,ETCP 1 5", ["cat /proc/net/dev; cat /proc/net/snmp; cat /proc/net/netstat"]),
        cmd("top", "top -bn1 | head -40"),
    ],
    "cpu_basic": [
        cmd("uptime", "uptime"),
        cmd("top", "top -bn1 | head -40"),
        cmd("vmstat", "vmstat 1 5", ["cat /proc/loadavg; head -5 /proc/stat"]),
        cmd("mpstat", "mpstat -P ALL 1 3", ["grep '^cpu' /proc/stat | head -40"]),
        cmd(
            "pidstat_cpu",
            "pidstat -u -t 1 5",
            ["ps -eLo pid,tid,state,comm,pcpu,pmem,wchan:24 --sort=-pcpu | head -40"],
        ),
        cmd(
            "pidstat_switch",
            "pidstat -w 1 5",
            ["ps -eLo pid,tid,state,comm,pcpu,wchan:24 --sort=-pcpu | head -40"],
        ),
        cmd("loadavg", "cat /proc/loadavg"),
        cmd("cpuinfo", "grep -E 'processor|cpu cores|siblings|model name' /proc/cpuinfo | head -120"),
    ],
    "memory_basic": [
        cmd("free", "free -h", ["grep -E 'MemTotal|MemAvailable|MemFree|Cached|Buffers|SwapTotal|SwapFree' /proc/meminfo"]),
        cmd("vmstat", "vmstat 1 5", ["grep -E 'pgscan|pgsteal|pswp|pgmajfault|oom_kill' /proc/vmstat"]),
        cmd(
            "meminfo",
            "grep -E 'MemAvailable|MemFree|Buffers|Cached|SReclaimable|SUnreclaim|Slab|Dirty|Writeback|Shmem|SwapTotal|SwapFree|SwapCached|Active\\(file\\)|Inactive\\(file\\)|Active\\(anon\\)|Inactive\\(anon\\)' /proc/meminfo",
        ),
        cmd("top_rss", "ps -eo pid,ppid,cmd,%mem,rss,vsz --sort=-rss | head -30"),
        cmd("slabtop", "slabtop -o | head -30", ["grep -E '^Slab|^SReclaimable|^SUnreclaim' /proc/meminfo"]),
        cmd(
            "oom_logs",
            "dmesg -T | grep -Ei 'oom|out of memory|killed process' | tail -80",
            ["journalctl -k --no-pager -n 200 2>/dev/null | grep -Ei 'oom|out of memory|killed process' | tail -80 || true"],
            allowed_exit_codes=[0, 1],
        ),
        cmd("memory_psi", "cat /proc/pressure/memory 2>/dev/null || true"),
    ],
    "io_basic": [
        cmd("top", "top -bn1 | head -40"),
        cmd("vmstat", "vmstat 1 5", ["cat /proc/loadavg; grep -E 'pgpg|pswp|pgmajfault' /proc/vmstat"]),
        cmd("iostat", "iostat -d -x 1 5", ["cat /proc/diskstats"]),
        cmd(
            "pidstat_io",
            "pidstat -d 1 5",
            ["for p in /proc/[0-9]*/io; do pid=${p#/proc/}; pid=${pid%/io}; comm=$(cat /proc/$pid/comm 2>/dev/null); awk -v pid=\"$pid\" -v comm=\"$comm\" '/read_bytes|write_bytes|cancelled_write_bytes/{printf \"%s=%s \",$1,$2} END{print pid,comm}' \"$p\" 2>/dev/null; done | head -40"],
        ),
        cmd("df_space", "df -h"),
        cmd("df_inode", "df -i"),
        cmd("lsblk", "lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL", ["cat /proc/partitions"]),
        cmd("findmnt", "findmnt", ["mount"]),
        cmd(
            "io_logs",
            "dmesg -T | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O' | tail -100",
            ["journalctl -k --no-pager -n 200 2>/dev/null | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O' | tail -100 || true"],
            allowed_exit_codes=[0, 1],
        ),
    ],
    "network_basic": [
        cmd("ip_addr", "ip addr", ["ifconfig -a 2>/dev/null || cat /proc/net/dev"]),
        cmd("ip_route", "ip route", ["netstat -rn 2>/dev/null || route -n 2>/dev/null || cat /proc/net/route"]),
        cmd("ss_summary", "ss -s", ["netstat -s 2>/dev/null || cat /proc/net/sockstat"]),
        cmd("ss_tcp", "ss -antpi | head -120", ["netstat -antp 2>/dev/null | head -120 || cat /proc/net/tcp"]),
        cmd("nstat", "nstat -az", ["cat /proc/net/snmp; cat /proc/net/netstat"]),
        cmd("link_stats", "ip -s link", ["cat /proc/net/dev"]),
        cmd("sar_network", "sar -n DEV,TCP,ETCP 1 5", ["cat /proc/net/dev; cat /proc/net/snmp; cat /proc/net/netstat"]),
        cmd("softnet", "cat /proc/net/softnet_stat | head -20"),
    ],
    "container_cgroup_basic": [
        cmd("self_cgroup", "cat /proc/self/cgroup"),
        cmd("memory_current_v2", "cat /sys/fs/cgroup/memory.current 2>/dev/null || true"),
        cmd("memory_max_v2", "cat /sys/fs/cgroup/memory.max 2>/dev/null || true"),
        cmd("memory_stat_v2", "cat /sys/fs/cgroup/memory.stat 2>/dev/null || true"),
        cmd("memory_events_v2", "cat /sys/fs/cgroup/memory.events 2>/dev/null || true"),
        cmd("cpu_stat_v2", "cat /sys/fs/cgroup/cpu.stat 2>/dev/null || true"),
        cmd("cpu_max_v2", "cat /sys/fs/cgroup/cpu.max 2>/dev/null || true"),
        cmd("io_stat_v2", "cat /sys/fs/cgroup/io.stat 2>/dev/null || true"),
        cmd("memory_usage_v1", "cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || true"),
        cmd("memory_limit_v1", "cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || true"),
        cmd("memory_stat_v1", "cat /sys/fs/cgroup/memory/memory.stat 2>/dev/null || true"),
        cmd("memory_failcnt_v1", "cat /sys/fs/cgroup/memory/memory.failcnt 2>/dev/null || true"),
    ],
    "logs_oom_io_network": [
        cmd("kernel_tail", "dmesg -T | tail -160", ["journalctl -k --no-pager -n 160 2>/dev/null || true"]),
        cmd("journal_kernel", "journalctl -k --no-pager -n 200 2>/dev/null || true"),
        cmd(
            "kernel_filtered",
            "dmesg -T | grep -Ei 'oom|out of memory|killed process|blocked for more than|I/O error|reset|timeout|link is down|link is up|nf_conntrack|martian|segfault' | tail -160",
            ["journalctl -k --no-pager -n 300 2>/dev/null | grep -Ei 'oom|out of memory|killed process|blocked for more than|I/O error|reset|timeout|link is down|link is up|nf_conntrack|martian|segfault' | tail -160 || true"],
            allowed_exit_codes=[0, 1],
        ),
    ],
}


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(os.path.expanduser(path))
    if not config_path.exists():
        return {"hosts": {}}
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("hosts", {}), dict):
        raise ValueError("config must be a JSON object with a hosts object")
    return data


def host_entry(config: dict[str, Any], host: str) -> dict[str, Any]:
    hosts = config.get("hosts", {})
    if host not in hosts:
        raise ValueError(f"host is not configured or allowed: {host}")
    entry = hosts[host]
    if not isinstance(entry, dict):
        raise ValueError(f"host entry must be an object: {host}")
    return entry


def ssh_target(name: str, entry: dict[str, Any]) -> str:
    hostname = entry.get("hostname", name)
    user = entry.get("user")
    return f"{user}@{hostname}" if user else str(hostname)


def ssh_args(name: str, entry: dict[str, Any]) -> list[str]:
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={int(entry.get('connect_timeout', 8))}",
    ]
    if entry.get("port"):
        args.extend(["-p", str(entry["port"])])
    if entry.get("identity_file"):
        args.extend(["-i", os.path.expanduser(str(entry["identity_file"]))])
    for option in entry.get("ssh_options", []):
        args.extend(["-o", str(option)])
    args.append(ssh_target(name, entry))
    return args


def is_fallback_worthy(result: dict[str, Any]) -> bool:
    if result["timed_out"]:
        return True
    stderr = result.get("stderr", "").lower()
    stdout = result.get("stdout", "").lower()
    text = stderr + "\n" + stdout
    missing_patterns = [
        "command not found",
        "no such file or directory",
        "permission denied",
        "operation not permitted",
    ]
    return result["exit_code"] in {126, 127} or any(pattern in text for pattern in missing_patterns)


def normalize_process_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def run_remote(name: str, entry: dict[str, Any], command: str, timeout: int) -> dict[str, Any]:
    started = time.time()
    remote = "export LC_ALL=C; " + command
    proc_args = ssh_args(name, entry) + [remote]
    try:
        proc = subprocess.run(
            proc_args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        timed_out = False
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = normalize_process_output(exc.stdout)
        stderr = normalize_process_output(exc.stderr)
        exit_code = 124

    max_bytes = int(entry.get("max_output_bytes", 200_000))
    stdout_bytes = len(stdout.encode("utf-8", "replace"))
    stderr_bytes = len(stderr.encode("utf-8", "replace"))
    stdout_truncated = stdout_bytes > max_bytes
    stderr_truncated = stderr_bytes > max_bytes
    if stdout_truncated:
        stdout = stdout.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "replace")
    if stderr_truncated:
        stderr = stderr.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "replace")

    return {
        "command": command,
        "argv_preview": " ".join(shlex.quote(x) for x in proc_args[:-1]) + " <remote-command>",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "duration_ms": int((time.time() - started) * 1000),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "max_output_bytes": max_bytes,
    }


def run_command_spec(name: str, entry: dict[str, Any], spec: CommandSpec, timeout: int) -> dict[str, Any]:
    attempts = []
    allowed_exit_codes = spec.get("allowed_exit_codes", [0])
    primary = run_remote(name, entry, str(spec["command"]), timeout)
    primary["attempt"] = "primary"
    attempts.append(primary)

    selected = primary
    fallback_used = False
    fallback_reason = None
    if is_fallback_worthy(primary):
        fallback_reason = "primary command timed out, failed, or was unavailable"
        for fallback_command in spec.get("fallbacks", []):
            fallback = run_remote(name, entry, str(fallback_command), timeout)
            fallback["attempt"] = "fallback"
            attempts.append(fallback)
            selected = fallback
            fallback_used = True
            if not is_fallback_worthy(fallback):
                break

    return {
        "id": spec["id"],
        "command": selected["command"],
        "primary_command": spec["command"],
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "allowed_exit_codes": allowed_exit_codes,
        "exit_ok": selected["exit_code"] in allowed_exit_codes,
        "attempts": attempts,
        "exit_code": selected["exit_code"],
        "stdout": selected["stdout"],
        "stderr": selected["stderr"],
        "timed_out": selected["timed_out"],
        "duration_ms": selected["duration_ms"],
        "stdout_truncated": selected["stdout_truncated"],
        "stderr_truncated": selected["stderr_truncated"],
        "stdout_bytes": selected["stdout_bytes"],
        "stderr_bytes": selected["stderr_bytes"],
        "max_output_bytes": selected["max_output_bytes"],
    }


def list_hosts(config: dict[str, Any]) -> dict[str, Any]:
    result = []
    for name, entry in sorted(config.get("hosts", {}).items()):
        result.append(
            {
                "name": name,
                "hostname": entry.get("hostname", name),
                "user": entry.get("user"),
                "port": entry.get("port", 22),
                "labels": entry.get("labels", []),
            }
        )
    return {"hosts": result, "bundles": sorted(BUNDLES)}


def command_by_id(commands: list[dict[str, Any]], command_id: str) -> dict[str, Any] | None:
    return next((command for command in commands if command.get("id") == command_id), None)


def output_for(commands: list[dict[str, Any]], command_id: str) -> str:
    command = command_by_id(commands, command_id)
    return command.get("stdout", "") if command else ""


def add_signal(signals: list[dict[str, Any]], severity: str, category: str, evidence: str, interpretation: str) -> None:
    signals.append(
        {
            "severity": severity,
            "category": category,
            "evidence": evidence,
            "interpretation": interpretation,
        }
    )


def parse_loadavg(text: str) -> tuple[float | None, float | None, float | None]:
    match = re.search(r"load average[s]?:\s*([0-9.]+),?\s+([0-9.]+),?\s+([0-9.]+)", text)
    if not match:
        match = re.search(r"^([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", text.strip())
    if not match:
        return None, None, None
    return float(match.group(1)), float(match.group(2)), float(match.group(3))


def parse_cpu_count(text: str) -> int | None:
    count = len(re.findall(r"^processor\s*:", text, flags=re.MULTILINE))
    return count or None


def parse_vmstat_last(text: str) -> dict[str, float]:
    headers: list[str] = []
    values: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[:2] == ["r", "b"]:
            headers = parts
        elif headers and len(parts) == len(headers) and parts[0].lstrip("-").isdigit():
            values = parts
    result = {}
    for key, value in zip(headers, values):
        try:
            result[key] = float(value)
        except ValueError:
            pass
    return result


def parse_free_available_ratio(text: str) -> float | None:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("Mem:") and len(parts) >= 7:
            total = parse_size_to_mib(parts[1])
            available = parse_size_to_mib(parts[6])
            if total and available is not None:
                return available / total
    total_kib = parse_meminfo_kib(text, "MemTotal")
    available_kib = parse_meminfo_kib(text, "MemAvailable")
    if total_kib and available_kib is not None:
        return available_kib / total_kib
    return None


def parse_size_to_mib(value: str) -> float | None:
    match = re.match(r"([0-9.]+)([kmgtp]?i?)?", value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    multipliers = {
        "b": 1 / 1024 / 1024,
        "k": 1 / 1024,
        "ki": 1 / 1024,
        "m": 1,
        "mi": 1,
        "g": 1024,
        "gi": 1024,
        "t": 1024 * 1024,
        "ti": 1024 * 1024,
        "p": 1024 * 1024 * 1024,
        "pi": 1024 * 1024 * 1024,
    }
    return number * multipliers.get(unit, 1)


def parse_meminfo_kib(text: str, key: str) -> int | None:
    match = re.search(rf"^{re.escape(key)}:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def parse_iostat_hot_devices(text: str) -> list[str]:
    hot = []
    headers: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0].lower().startswith("device"):
            headers = parts
            continue
        if len(parts) < 5 or parts[0].lower() == "linux" or not headers:
            continue
        values: dict[str, float] = {}
        for key, value in zip(headers[1:], parts[1:]):
            if re.match(r"^-?[0-9.]+$", value):
                values[key] = float(value)
        await_value = max(
            values.get("await", 0),
            values.get("r_await", 0),
            values.get("w_await", 0),
        )
        util_value = values.get("%util", 0)
        if await_value >= 20 or util_value >= 80:
            hot.append(f"{parts[0]} await≈{await_value:g}ms util≈{util_value:g}%")
    return hot[:5]


def parse_tcp_retrans(text: str) -> int | None:
    keys = {"RetransSegs", "TCPLostRetransmit", "TCPFastRetrans", "TCPSlowStartRetrans"}
    total = 0
    found = False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in keys:
            try:
                total += int(float(parts[1]))
                found = True
            except ValueError:
                pass
    return total if found else None


def parse_softnet_drops(text: str) -> int:
    drops = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                drops += int(parts[1], 16)
            except ValueError:
                pass
    return drops


def build_command_health(commands: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [cmd["id"] for cmd in commands if not cmd.get("exit_ok", cmd["exit_code"] == 0)]
    timed_out = []
    truncated = []
    for command in commands:
        attempts = command.get("attempts") or [command]
        if any(attempt.get("timed_out") for attempt in attempts):
            timed_out.append(command["id"])
        if any(attempt.get("stdout_truncated") or attempt.get("stderr_truncated") for attempt in attempts):
            truncated.append(command["id"])
    fallback_used = [cmd["id"] for cmd in commands if cmd.get("fallback_used")]
    return {
        "total": len(commands),
        "failed": failed,
        "selected_failed": failed,
        "timed_out": timed_out,
        "truncated": truncated,
        "fallback_used": fallback_used,
        "healthy": not failed and not timed_out and not truncated,
    }


def analyze_cpu(commands: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[str]:
    next_bundles: list[str] = []
    uptime = output_for(commands, "uptime") or output_for(commands, "identity_uptime") or output_for(commands, "loadavg")
    cpuinfo = output_for(commands, "cpuinfo")
    load1, _, _ = parse_loadavg(uptime)
    cpu_count = parse_cpu_count(cpuinfo)
    if load1 is not None and cpu_count and load1 > cpu_count:
        add_signal(signals, "warning", "cpu", f"load1={load1:g}, cpus={cpu_count}", "Load exceeds CPU count; inspect runnable queue versus blocked IO.")
        next_bundles.extend(["cpu_basic", "io_basic"])
    vm = parse_vmstat_last(output_for(commands, "vmstat"))
    if vm.get("r", 0) >= max(2, cpu_count or 1):
        add_signal(signals, "warning", "cpu", f"vmstat r={vm.get('r'):g}", "Runnable queue is elevated; CPU saturation or lock contention is plausible.")
    if vm.get("b", 0) > 0 or vm.get("wa", 0) >= 10:
        add_signal(signals, "warning", "io", f"vmstat b={vm.get('b', 0):g}, wa={vm.get('wa', 0):g}", "Blocked tasks or iowait suggest IO or reclaim pressure.")
        next_bundles.append("io_basic")
    if vm.get("si", 0) >= 5:
        add_signal(signals, "warning", "network", f"vmstat si={vm.get('si'):g}", "Softirq CPU is elevated; network packet processing may be involved.")
        next_bundles.append("network_basic")
    return next_bundles


def analyze_memory(commands: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[str]:
    next_bundles: list[str] = []
    free_text = output_for(commands, "free") or output_for(commands, "meminfo")
    ratio = parse_free_available_ratio(free_text)
    if ratio is not None and ratio < 0.10:
        add_signal(signals, "warning", "memory", f"MemAvailable ratio≈{ratio:.1%}", "Available memory is low; check RSS/PSS, cgroup file cache, swap, and OOM evidence.")
    vm = parse_vmstat_last(output_for(commands, "vmstat"))
    if vm.get("si", 0) > 0 or vm.get("so", 0) > 0:
        add_signal(signals, "warning", "memory", f"swap in/out si={vm.get('si', 0):g}, so={vm.get('so', 0):g}", "Active swap is present and can explain latency spikes.")
        next_bundles.append("io_basic")
    oom_text = output_for(commands, "oom_logs").lower()
    if "out of memory" in oom_text or "killed process" in oom_text or re.search(r"\boom\b", oom_text):
        add_signal(signals, "critical", "memory", "OOM strings found in kernel logs", "OOM or cgroup OOM evidence exists; determine host versus cgroup before restart-only action.")
        next_bundles.append("container_cgroup_basic")
    slab_kib = parse_meminfo_kib(output_for(commands, "meminfo") + "\n" + output_for(commands, "slabtop"), "SUnreclaim")
    total_kib = parse_meminfo_kib(output_for(commands, "meminfo"), "MemTotal")
    if slab_kib and total_kib and slab_kib / total_kib > 0.10:
        add_signal(signals, "warning", "memory", f"SUnreclaim≈{slab_kib // 1024}MiB", "Unreclaimable slab is significant; kernel cache/object growth may be involved.")
    return next_bundles


def analyze_io(commands: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[str]:
    next_bundles: list[str] = []
    vm = parse_vmstat_last(output_for(commands, "vmstat"))
    if vm.get("b", 0) > 0 or vm.get("wa", 0) >= 10:
        add_signal(signals, "warning", "io", f"vmstat b={vm.get('b', 0):g}, wa={vm.get('wa', 0):g}", "IO wait or blocked tasks are visible.")
    hot_devices = parse_iostat_hot_devices(output_for(commands, "iostat"))
    for device in hot_devices:
        add_signal(signals, "warning", "io", device, "Device latency/utilization is elevated; map logical device to storage backend.")
    logs = output_for(commands, "io_logs").lower()
    if re.search(r"i/o error|reset|timeout|blk|nvme|scsi", logs):
        add_signal(signals, "critical", "io", "kernel IO error/reset/timeout strings found", "Kernel logs contain storage-path errors; correlate with device and provider/backend metrics.")
        next_bundles.append("logs_oom_io_network")
    return next_bundles


def analyze_network(commands: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[str]:
    next_bundles: list[str] = []
    nstat = output_for(commands, "nstat") + "\n" + output_for(commands, "sar_network")
    retrans = parse_tcp_retrans(nstat)
    if retrans is not None and retrans > 0:
        add_signal(signals, "warning", "network", f"TCP retrans-related counters total≈{retrans}", "Retransmission counters are nonzero; inspect affected flows, drops, MTU, and downstream pressure.")
    softnet_drops = parse_softnet_drops(output_for(commands, "softnet"))
    if softnet_drops > 0:
        add_signal(signals, "warning", "network", f"softnet dropped≈{softnet_drops}", "Kernel softnet drops suggest packet processing backlog or CPU/IRQ pressure.")
        next_bundles.append("cpu_basic")
    ss_summary = output_for(commands, "ss_summary").lower()
    if "timewait" in ss_summary or "orphaned" in ss_summary:
        add_signal(signals, "info", "network", "socket state summary includes TIME_WAIT/orphaned sockets", "Short connections or socket lifecycle pressure may be relevant.")
    return next_bundles


def build_diagnostic_report(bundle: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    next_bundles: list[str] = []
    health = build_command_health(commands)

    if health["timed_out"]:
        add_signal(signals, "warning", "collection", f"timed out: {', '.join(health['timed_out'])}", "Some evidence is incomplete because commands hit the per-command timeout.")
    if health["truncated"]:
        add_signal(signals, "warning", "collection", f"truncated: {', '.join(health['truncated'])}", "Some output was capped by max_output_bytes; rerun focused bundles or increase the cap if needed.")
    if health["fallback_used"]:
        add_signal(signals, "info", "collection", f"fallback used: {', '.join(health['fallback_used'])}", "Primary tools were missing, denied, or timed out; fallback evidence was collected automatically.")

    analyzers = {
        "snapshot_60s": [analyze_cpu, analyze_memory, analyze_io, analyze_network],
        "cpu_basic": [analyze_cpu],
        "memory_basic": [analyze_memory],
        "io_basic": [analyze_io],
        "network_basic": [analyze_network],
    }
    for analyzer in analyzers.get(bundle, []):
        next_bundles.extend(analyzer(commands, signals))

    ordered_next = []
    for item in next_bundles:
        if item != bundle and item not in ordered_next:
            ordered_next.append(item)

    critical = any(signal["severity"] == "critical" for signal in signals)
    warnings = [signal for signal in signals if signal["severity"] == "warning"]
    if critical:
        summary = "Critical evidence found; review signals before collecting the next read-only bundle."
        confidence = "medium"
    elif warnings:
        summary = "Potential bottleneck signals found; use next_read_only_bundles to narrow the branch."
        confidence = "medium"
    elif not health["healthy"]:
        summary = "Collection was incomplete; inspect command_health before drawing a conclusion."
        confidence = "low"
    else:
        summary = "No strong bottleneck signal was extracted from this bundle."
        confidence = "low"

    return {
        "summary": summary,
        "confidence": confidence,
        "signals": signals,
        "next_read_only_bundles": ordered_next,
        "command_health": health,
        "safety": {
            "automatic_fixes_run": False,
            "remote_commands_are_predefined": True,
        },
    }


def run_bundle(config: dict[str, Any], host: str, bundle: str, timeout: int | None = None) -> dict[str, Any]:
    if bundle not in BUNDLES:
        raise ValueError(f"unknown bundle: {bundle}")
    entry = host_entry(config, host)
    per_command_timeout = int(timeout or entry.get("command_timeout", 20))
    started = time.time()
    commands = [run_command_spec(host, entry, spec, per_command_timeout) for spec in BUNDLES[bundle]]
    report = build_diagnostic_report(bundle, commands)
    return {
        "host": host,
        "bundle": bundle,
        "started_at_unix": int(started),
        "duration_ms": int((time.time() - started) * 1000),
        "per_command_timeout_seconds": per_command_timeout,
        "diagnostic_report": report,
        "commands": commands,
    }


def content_text(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "name": "ssh_list_hosts",
            "description": "List configured SSH hosts and available read-only diagnostic bundles.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "ssh_run_bundle",
            "description": "Run a predefined read-only Linux diagnostic bundle on an allowed SSH host.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Configured host alias."},
                    "bundle": {"type": "string", "enum": sorted(BUNDLES)},
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "description": "Per-command timeout in seconds.",
                    },
                },
                "required": ["host", "bundle"],
                "additionalProperties": False,
            },
        },
    ]


class McpServer:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "notifications/initialized":
                return None
            elif method == "tools/list":
                result = {"tools": tool_schema()}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                if name == "ssh_list_hosts":
                    result = content_text(list_hosts(self.config))
                elif name == "ssh_run_bundle":
                    result = content_text(
                        run_bundle(
                            self.config,
                            host=str(args["host"]),
                            bundle=str(args["bundle"]),
                            timeout=args.get("timeout"),
                        )
                    )
                else:
                    raise ValueError(f"unknown tool: {name}")
            else:
                raise ValueError(f"unsupported method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # Keep MCP errors structured.
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def serve(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            response = self.handle(json.loads(line))
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get("LINUX_TROUBLESHOOTING_SSH_CONFIG", DEFAULT_CONFIG),
        help="Path to ssh-hosts.json config file.",
    )
    parser.add_argument("--list-bundles", action="store_true", help="Print available bundle names and exit.")
    args = parser.parse_args()

    if args.list_bundles:
        print(json.dumps(sorted(BUNDLES), indent=2))
        return 0

    McpServer(args.config).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
