#!/usr/bin/env python3
"""Read-only SSH diagnostics MCP server for linux-troubleshooting.

This server intentionally exposes a small surface:
- list configured hosts
- run predefined read-only diagnostic bundles over SSH

It does not provide arbitrary remote command execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
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
    sudo_allowed: bool = False,
) -> CommandSpec:
    return {
        "id": command_id,
        "command": command,
        "fallbacks": fallbacks or [],
        "allowed_exit_codes": allowed_exit_codes or [0],
        "sudo_allowed": sudo_allowed,
    }


BUNDLES: dict[str, list[CommandSpec]] = {
    "platform_probe": [
        cmd("os_release", "cat /etc/os-release 2>/dev/null || true"),
        cmd("kernel_uname", "uname -a"),
        cmd(
            "tool_inventory",
            "for t in systemctl journalctl dmesg ss ip nstat sar mpstat pidstat iostat vmstat top ps awk grep findmnt lsblk crictl ctr kubectl netstat ifconfig route; do command -v \"$t\" >/dev/null 2>&1 && echo \"$t=$(command -v \"$t\")\" || true; done",
        ),
        cmd("cgroup_fs", "stat -fc %T /sys/fs/cgroup 2>/dev/null || true"),
        cmd("cgroup_mounts", "mount | grep cgroup || true", ["cat /proc/mounts | grep cgroup || true"]),
        cmd("init_comm", "cat /proc/1/comm 2>/dev/null || true"),
        cmd("container_markers", "test -f /.dockerenv && echo docker-container || true; grep -qa container=lxc /proc/1/environ 2>/dev/null && echo lxc-container || true"),
    ],
    "snapshot_60s": [
        cmd("identity_uptime", "date; hostname; uptime"),
        cmd("kernel_recent", "dmesg -T | tail -80", ["journalctl -k --no-pager -n 80 2>/dev/null || true"], sudo_allowed=True),
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
            sudo_allowed=True,
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
            sudo_allowed=True,
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
        cmd("memory_current_v2", "cat /sys/fs/cgroup/memory.current 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_max_v2", "cat /sys/fs/cgroup/memory.max 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_stat_v2", "cat /sys/fs/cgroup/memory.stat 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_events_v2", "cat /sys/fs/cgroup/memory.events 2>/dev/null || true", sudo_allowed=True),
        cmd("cpu_stat_v2", "cat /sys/fs/cgroup/cpu.stat 2>/dev/null || true", sudo_allowed=True),
        cmd("cpu_max_v2", "cat /sys/fs/cgroup/cpu.max 2>/dev/null || true", sudo_allowed=True),
        cmd("io_stat_v2", "cat /sys/fs/cgroup/io.stat 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_usage_v1", "cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_limit_v1", "cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_stat_v1", "cat /sys/fs/cgroup/memory/memory.stat 2>/dev/null || true", sudo_allowed=True),
        cmd("memory_failcnt_v1", "cat /sys/fs/cgroup/memory/memory.failcnt 2>/dev/null || true", sudo_allowed=True),
    ],
    "logs_oom_io_network": [
        cmd("kernel_tail", "dmesg -T | tail -160", ["journalctl -k --no-pager -n 160 2>/dev/null || true"], sudo_allowed=True),
        cmd("journal_kernel", "journalctl -k --no-pager -n 200 2>/dev/null || true", sudo_allowed=True),
        cmd(
            "kernel_filtered",
            "dmesg -T | grep -Ei 'oom|out of memory|killed process|blocked for more than|I/O error|reset|timeout|link is down|link is up|nf_conntrack|martian|segfault' | tail -160",
            ["journalctl -k --no-pager -n 300 2>/dev/null | grep -Ei 'oom|out of memory|killed process|blocked for more than|I/O error|reset|timeout|link is down|link is up|nf_conntrack|martian|segfault' | tail -160 || true"],
            allowed_exit_codes=[0, 1],
            sudo_allowed=True,
        ),
    ],
    "k8s_node_pod_cgroup": [
        cmd("k8s_identity", "hostname; cat /proc/self/cgroup"),
        cmd("kubelet_pods", "find /var/lib/kubelet/pods -maxdepth 2 -type d 2>/dev/null | head -120", sudo_allowed=True),
        cmd("crictl_ps", "crictl ps -a 2>/dev/null | head -80", ["ctr -n k8s.io containers list 2>/dev/null | head -80 || true"], sudo_allowed=True),
        cmd("cgroup_pods_v2", "find /sys/fs/cgroup -maxdepth 5 -type d -name '*pod*' 2>/dev/null | head -120", sudo_allowed=True),
        cmd("kubelet_logs", "journalctl -u kubelet --no-pager -n 120 2>/dev/null || true", sudo_allowed=True),
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


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def merged_labels(entry: dict[str, Any]) -> dict[str, Any]:
    labels = entry.get("labels", [])
    if isinstance(labels, dict):
        return {str(k): v for k, v in labels.items()}
    return {str(label): True for label in as_list(labels)}


def label_list(entry: dict[str, Any]) -> list[str]:
    labels = entry.get("labels", [])
    if isinstance(labels, dict):
        return [f"{key}={value}" for key, value in sorted(labels.items())]
    return as_list(labels)


def host_entry(config: dict[str, Any], host: str) -> dict[str, Any]:
    hosts = config.get("hosts", {})
    if host not in hosts:
        raise ValueError(f"host is not configured or allowed: {host}")
    entry = hosts[host]
    if not isinstance(entry, dict):
        raise ValueError(f"host entry must be an object: {host}")
    return entry


def host_matches_labels(entry: dict[str, Any], labels: list[str]) -> bool:
    if not labels:
        return True
    host_labels = merged_labels(entry)
    for label in labels:
        if "=" in label:
            key, expected = label.split("=", 1)
            if str(host_labels.get(key)) != expected:
                return False
        elif label not in host_labels:
            return False
    return True


def sudo_config(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    global_cfg = config.get("sudo", {}) if isinstance(config.get("sudo", {}), dict) else {}
    host_cfg = entry.get("sudo", {}) if isinstance(entry.get("sudo", {}), dict) else {}
    return {
        "enabled": bool(host_cfg.get("enabled", global_cfg.get("enabled", False))),
        "command_ids": set(as_list(global_cfg.get("command_ids")) + as_list(host_cfg.get("command_ids"))),
        "bundles": set(as_list(global_cfg.get("bundles")) + as_list(host_cfg.get("bundles"))),
    }


def sudo_allowed(config: dict[str, Any], entry: dict[str, Any], bundle: str, command_id: str, spec: CommandSpec) -> bool:
    if not spec.get("sudo_allowed"):
        return False
    cfg = sudo_config(config, entry)
    if not cfg["enabled"]:
        return False
    return command_id in cfg["command_ids"] or bundle in cfg["bundles"]


def audit_config(config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("audit", {}) if isinstance(config.get("audit", {}), dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "path": os.path.expanduser(str(cfg.get("path", "~/.config/linux-troubleshooting/audit.jsonl"))),
    }


def redaction_config(config: dict[str, Any], entry: dict[str, Any] | None = None) -> dict[str, Any]:
    global_cfg = config.get("redaction", {}) if isinstance(config.get("redaction", {}), dict) else {}
    host_cfg = entry.get("redaction", {}) if entry and isinstance(entry.get("redaction", {}), dict) else {}
    enabled = host_cfg.get("enabled", global_cfg.get("enabled", True))
    return {
        "enabled": bool(enabled),
        "redact_ips": bool(host_cfg.get("redact_ips", global_cfg.get("redact_ips", True))),
        "redact_domains": bool(host_cfg.get("redact_domains", global_cfg.get("redact_domains", False))),
        "redact_paths": bool(host_cfg.get("redact_paths", global_cfg.get("redact_paths", False))),
    }


class Redactor:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.values: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def token(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self.values:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.values[key] = f"<redacted:{kind}:{self.counts[kind]}>"
        return self.values[key]

    def text(self, value: Any) -> Any:
        if not self.cfg.get("enabled") or not isinstance(value, str) or not value:
            return value
        text = value
        text = re.sub(
            r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+",
            lambda m: m.group(1) + "<redacted:secret>",
            text,
        )
        text = re.sub(
            r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)=([^\s;&]+)",
            lambda m: f"{m.group(1)}=<redacted:secret>",
            text,
        )
        text = re.sub(
            r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|KEY))=([^\s;&]+)",
            lambda m: f"{m.group(1)}=<redacted:secret>",
            text,
        )
        if self.cfg.get("redact_ips"):
            text = re.sub(
                r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                lambda m: self.token("ip", m.group(0)),
                text,
            )
        if self.cfg.get("redact_domains"):
            text = re.sub(
                r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b",
                lambda m: self.token("domain", m.group(0)),
                text,
            )
        if self.cfg.get("redact_paths"):
            text = re.sub(
                r"(?<![\w.-])/(?:[A-Za-z0-9._@+-]+/)+[A-Za-z0-9._@+-]*",
                lambda m: self.token("path", m.group(0)),
                text,
            )
        return text

    def object(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.object(item) for item in value]
        if isinstance(value, dict):
            return {key: self.object(item) for key, item in value.items()}
        return value


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


def primary_tool_for(command_id: str) -> str | None:
    mapping = {
        "mpstat": "mpstat",
        "pidstat_all": "pidstat",
        "pidstat_cpu": "pidstat",
        "pidstat_switch": "pidstat",
        "pidstat_io": "pidstat",
        "iostat": "iostat",
        "sar_network": "sar",
        "ss_summary": "ss",
        "ss_tcp": "ss",
        "nstat": "nstat",
        "ip_addr": "ip",
        "ip_route": "ip",
        "link_stats": "ip",
        "findmnt": "findmnt",
        "lsblk": "lsblk",
        "kernel_recent": "dmesg",
        "kernel_tail": "dmesg",
        "kernel_filtered": "dmesg",
        "journal_kernel": "journalctl",
        "slabtop": "slabtop",
    }
    return mapping.get(command_id)


def tool_missing(platform_profile: dict[str, Any] | None, command_id: str) -> bool:
    if not platform_profile:
        return False
    tool = primary_tool_for(command_id)
    return bool(tool and tool in set(platform_profile.get("missing_core_tools", [])))


def normalize_process_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def sudo_wrap(command: str) -> str:
    return "sudo -n sh -lc " + shlex.quote("export LC_ALL=C; " + command)


def run_remote(name: str, entry: dict[str, Any], command: str, timeout: int, use_sudo: bool = False) -> dict[str, Any]:
    started = time.time()
    remote = "export LC_ALL=C; " + (sudo_wrap(command) if use_sudo else command)
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
        "sudo": use_sudo,
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


def run_command_spec(
    config: dict[str, Any],
    name: str,
    entry: dict[str, Any],
    bundle: str,
    spec: CommandSpec,
    timeout: int,
    platform_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts = []
    allowed_exit_codes = spec.get("allowed_exit_codes", [0])
    selected = None
    fallback_used = False
    fallback_reason = None
    sudo_used = False
    can_sudo = sudo_allowed(config, entry, bundle, str(spec["id"]), spec)

    if tool_missing(platform_profile, str(spec["id"])) and spec.get("fallbacks"):
        fallback_reason = "primary tool missing in platform_profile"
        fallback = run_remote(name, entry, str(spec["fallbacks"][0]), timeout)
        fallback["attempt"] = "platform-fallback"
        attempts.append(fallback)
        selected = fallback
        fallback_used = True
        if can_sudo and is_fallback_worthy(fallback):
            sudo_fallback = run_remote(name, entry, str(spec["fallbacks"][0]), timeout, use_sudo=True)
            sudo_fallback["attempt"] = "sudo-platform-fallback"
            attempts.append(sudo_fallback)
            selected = sudo_fallback
            sudo_used = True
        return {
            "id": spec["id"],
            "command": selected["command"],
            "primary_command": spec["command"],
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "sudo_allowed": can_sudo,
            "sudo_used": sudo_used,
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

    primary = run_remote(name, entry, str(spec["command"]), timeout)
    primary["attempt"] = "primary"
    attempts.append(primary)

    selected = primary
    if is_fallback_worthy(primary):
        fallback_reason = "primary command timed out, failed, or was unavailable"
        if can_sudo and ("permission denied" in (primary.get("stderr", "") + primary.get("stdout", "")).lower() or primary["exit_code"] in {126, 127}):
            sudo_attempt = run_remote(name, entry, str(spec["command"]), timeout, use_sudo=True)
            sudo_attempt["attempt"] = "sudo-primary"
            attempts.append(sudo_attempt)
            selected = sudo_attempt
            sudo_used = True
            if not is_fallback_worthy(sudo_attempt):
                return {
                    "id": spec["id"],
                    "command": selected["command"],
                    "primary_command": spec["command"],
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "sudo_allowed": can_sudo,
                    "sudo_used": sudo_used,
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
        for fallback_command in spec.get("fallbacks", []):
            fallback = run_remote(name, entry, str(fallback_command), timeout)
            fallback["attempt"] = "fallback"
            attempts.append(fallback)
            selected = fallback
            fallback_used = True
            if not is_fallback_worthy(fallback):
                break
            if can_sudo:
                sudo_fallback = run_remote(name, entry, str(fallback_command), timeout, use_sudo=True)
                sudo_fallback["attempt"] = "sudo-fallback"
                attempts.append(sudo_fallback)
                selected = sudo_fallback
                sudo_used = True
                if not is_fallback_worthy(sudo_fallback):
                    break

    return {
        "id": spec["id"],
        "command": selected["command"],
        "primary_command": spec["command"],
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "sudo_allowed": can_sudo,
        "sudo_used": sudo_used,
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
                "labels": label_list(entry),
                "label_map": merged_labels(entry),
            }
        )
    return {"hosts": result, "bundles": sorted(BUNDLES)}


def select_hosts(config: dict[str, Any], hosts: list[str] | None = None, labels: list[str] | None = None) -> list[str]:
    configured = config.get("hosts", {})
    selected = []
    if hosts:
        for host in hosts:
            host_entry(config, host)
            selected.append(host)
    else:
        selected = sorted(configured)
    labels = labels or []
    return [host for host in selected if host_matches_labels(configured[host], labels)]


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


def parse_os_release(text: str) -> dict[str, str]:
    data = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def distro_family(os_id: str, id_like: str) -> str:
    haystack = " ".join([os_id, id_like]).lower()
    if any(item in haystack for item in ["ubuntu", "debian"]):
        return "debian"
    if any(item in haystack for item in ["rhel", "fedora", "centos", "rocky", "alma", "amzn"]):
        return "rhel"
    if any(item in haystack for item in ["sles", "suse", "opensuse"]):
        return "suse"
    if "alpine" in haystack:
        return "alpine"
    return "unknown"


def parse_available_tools(text: str) -> list[str]:
    tools = []
    for line in text.splitlines():
        if "=" in line:
            tools.append(line.split("=", 1)[0].strip())
    return sorted(set(tool for tool in tools if tool))


def parse_cgroup_mode(fs_type: str, mounts: str) -> str:
    if "cgroup2fs" in fs_type:
        return "v2"
    has_v1 = "type cgroup " in mounts or " cgroup " in mounts
    has_v2 = "type cgroup2 " in mounts or " cgroup2 " in mounts
    if has_v1 and has_v2:
        return "hybrid"
    if has_v2:
        return "v2"
    if has_v1:
        return "v1"
    return "unknown"


def build_platform_profile(commands: list[dict[str, Any]]) -> dict[str, Any]:
    os_release = parse_os_release(output_for(commands, "os_release"))
    tools = parse_available_tools(output_for(commands, "tool_inventory"))
    core_tools = ["mpstat", "pidstat", "iostat", "sar", "ss", "ip", "nstat", "journalctl", "dmesg", "findmnt", "lsblk"]
    init_comm = output_for(commands, "init_comm").strip()
    init_system = "systemd" if init_comm == "systemd" or "systemctl" in tools else "unknown"
    if init_comm in {"init", "busybox", "openrc-init"}:
        init_system = init_comm
    profile = {
        "distro": os_release.get("ID", "unknown"),
        "distro_family": distro_family(os_release.get("ID", ""), os_release.get("ID_LIKE", "")),
        "version": os_release.get("VERSION_ID", ""),
        "pretty_name": os_release.get("PRETTY_NAME", ""),
        "kernel": output_for(commands, "kernel_uname").strip(),
        "init": init_system,
        "cgroup": parse_cgroup_mode(output_for(commands, "cgroup_fs"), output_for(commands, "cgroup_mounts")),
        "containerized": bool(output_for(commands, "container_markers").strip()),
        "available_tools": tools,
        "missing_core_tools": [tool for tool in core_tools if tool not in tools],
    }
    return profile


def evidence_gap(command_id: str, missing_tool: str, fallback: str, impact: str) -> dict[str, str]:
    return {
        "command_id": command_id,
        "missing_tool": missing_tool,
        "fallback_used": fallback,
        "impact": impact,
    }


def build_evidence_gaps(commands: list[dict[str, Any]], platform_profile: dict[str, Any] | None) -> list[dict[str, str]]:
    gaps = []
    fallback_map = {
        "mpstat": evidence_gap("mpstat", "mpstat", "/proc/stat", "Per-CPU percentage columns are weaker; compare /proc/stat deltas if precision is needed."),
        "pidstat_all": evidence_gap("pidstat_all", "pidstat", "ps", "Per-interval CPU/IO/memory attribution is reduced to a point-in-time process view."),
        "pidstat_cpu": evidence_gap("pidstat_cpu", "pidstat", "ps -eLo", "Thread CPU attribution is point-in-time rather than sampled over an interval."),
        "pidstat_switch": evidence_gap("pidstat_switch", "pidstat", "ps -eLo", "Context switch rate is unavailable; lock/scheduler conclusions have lower confidence."),
        "pidstat_io": evidence_gap("pidstat_io", "pidstat", "/proc/<pid>/io", "Per-process IO rate is unavailable without sampling counters over time."),
        "iostat": evidence_gap("iostat", "iostat", "/proc/diskstats", "Diskstats lacks direct await/%util; storage latency conclusions have lower confidence."),
        "sar_network": evidence_gap("sar_network", "sar", "/proc/net/dev,/proc/net/snmp,/proc/net/netstat", "Network rates are counters rather than sampled sar summaries."),
        "ss_summary": evidence_gap("ss_summary", "ss", "netstat or /proc/net/sockstat", "Socket state detail may be reduced."),
        "ss_tcp": evidence_gap("ss_tcp", "ss", "netstat or /proc/net/tcp", "TCP queue/process detail may be reduced."),
        "nstat": evidence_gap("nstat", "nstat", "/proc/net/snmp,/proc/net/netstat", "TCP extended counter names vary and may need manual interpretation."),
        "ip_addr": evidence_gap("ip_addr", "ip", "ifconfig or /proc/net/dev", "Interface metadata and modern link attributes may be missing."),
        "ip_route": evidence_gap("ip_route", "ip", "netstat/route or /proc/net/route", "Policy routing and advanced route attributes may be missing."),
        "link_stats": evidence_gap("link_stats", "ip", "/proc/net/dev", "Detailed link error/drop attribution may be reduced."),
        "kernel_recent": evidence_gap("kernel_recent", "dmesg", "journalctl -k or log files", "Kernel log visibility depends on journald/syslog retention and permissions."),
        "kernel_tail": evidence_gap("kernel_tail", "dmesg", "journalctl -k or log files", "Kernel log visibility depends on journald/syslog retention and permissions."),
        "kernel_filtered": evidence_gap("kernel_filtered", "dmesg", "journalctl -k or log files", "Filtered kernel log visibility depends on journald/syslog retention and permissions."),
        "journal_kernel": evidence_gap("journal_kernel", "journalctl", "dmesg or syslog files", "Kernel log retention and timestamps may differ from journald output."),
        "slabtop": evidence_gap("slabtop", "slabtop", "/proc/meminfo", "Slab totals are available, but cache-family attribution is reduced."),
        "findmnt": evidence_gap("findmnt", "findmnt", "/proc/mounts", "Mount source/options are available but less structured."),
        "lsblk": evidence_gap("lsblk", "lsblk", "/proc/partitions", "Block device topology and filesystem metadata are reduced."),
    }
    for command in commands:
        if command.get("fallback_used") and command["id"] in fallback_map:
            gaps.append(fallback_map[command["id"]])
    return gaps


def build_interpretation_notes(platform_profile: dict[str, Any] | None, evidence_gaps: list[dict[str, str]]) -> list[str]:
    profile = platform_profile or {}
    notes = []
    cgroup = profile.get("cgroup")
    if cgroup == "v2":
        notes.append("cgroup v2 detected; memory.current, memory.events, cpu.stat, and io.stat are the primary cgroup files.")
    elif cgroup == "v1":
        notes.append("cgroup v1 detected; inspect memory.*, cpuacct/cpu, and blkio controllers rather than v2 unified files.")
    elif cgroup == "hybrid":
        notes.append("Hybrid cgroup layout detected; verify whether the workload is charged in v1 or v2 before interpreting limits.")
    if profile.get("containerized"):
        notes.append("Containerized environment detected; host-level IO, network, and kernel log evidence may be incomplete from inside the container.")
    if profile.get("distro_family") == "alpine":
        notes.append("Alpine/BusyBox environment detected; ps/top/netstat options and output fields may differ from GNU/procps tools.")
    if "journalctl" in profile.get("missing_core_tools", []) and "dmesg" in profile.get("missing_core_tools", []):
        notes.append("Both journalctl and dmesg are missing or unavailable; kernel-log based OOM/IO/network evidence may be absent.")
    if evidence_gaps:
        notes.append("Fallback evidence was used; conclusions depending on missing tools should be treated with lower confidence.")
    return notes


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
    sudo_used = [cmd["id"] for cmd in commands if cmd.get("sudo_used")]
    return {
        "total": len(commands),
        "failed": failed,
        "selected_failed": failed,
        "timed_out": timed_out,
        "truncated": truncated,
        "fallback_used": fallback_used,
        "sudo_used": sudo_used,
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


def build_diagnostic_report(
    bundle: str,
    commands: list[dict[str, Any]],
    platform_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    next_bundles: list[str] = []
    health = build_command_health(commands)
    evidence_gaps = build_evidence_gaps(commands, platform_profile)
    interpretation_notes = build_interpretation_notes(platform_profile, evidence_gaps)

    if health["timed_out"]:
        add_signal(signals, "warning", "collection", f"timed out: {', '.join(health['timed_out'])}", "Some evidence is incomplete because commands hit the per-command timeout.")
    if health["truncated"]:
        add_signal(signals, "warning", "collection", f"truncated: {', '.join(health['truncated'])}", "Some output was capped by max_output_bytes; rerun focused bundles or increase the cap if needed.")
    if health["fallback_used"]:
        add_signal(signals, "info", "collection", f"fallback used: {', '.join(health['fallback_used'])}", "Primary tools were missing, denied, or timed out; fallback evidence was collected automatically.")
    if health["sudo_used"]:
        add_signal(signals, "info", "collection", f"sudo used: {', '.join(health['sudo_used'])}", "Configured non-interactive sudo was used for read-only evidence collection.")

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
        "platform_profile": platform_profile or {},
        "evidence_gaps": evidence_gaps,
        "interpretation_notes": interpretation_notes,
        "signals": signals,
        "next_read_only_bundles": ordered_next,
        "command_health": health,
        "safety": {
            "automatic_fixes_run": False,
            "remote_commands_are_predefined": True,
            "sudo_is_config_gated": True,
        },
    }


def summarize_for_compare(result: dict[str, Any]) -> dict[str, Any]:
    report = result["diagnostic_report"]
    categories: dict[str, int] = {}
    severities: dict[str, int] = {}
    for signal in report.get("signals", []):
        categories[signal["category"]] = categories.get(signal["category"], 0) + 1
        severities[signal["severity"]] = severities.get(signal["severity"], 0) + 1
    return {
        "host": result["host"],
        "labels": result.get("host_metadata", {}).get("labels", []),
        "platform_profile": report.get("platform_profile", {}),
        "summary": report.get("summary"),
        "confidence": report.get("confidence"),
        "signal_categories": categories,
        "signal_severities": severities,
        "next_read_only_bundles": report.get("next_read_only_bundles", []),
        "command_health": report.get("command_health", {}),
    }


def compare_hosts(
    config: dict[str, Any],
    bundle: str,
    hosts: list[str] | None = None,
    labels: list[str] | None = None,
    timeout: int | None = None,
    max_hosts: int = 10,
    include_platform_probe: bool = True,
) -> dict[str, Any]:
    selected_hosts = select_hosts(config, hosts=hosts, labels=labels)
    if len(selected_hosts) > max_hosts:
        selected_hosts = selected_hosts[:max_hosts]
    run_id = str(uuid.uuid4())
    results = [
        run_bundle(config, host, bundle, timeout=timeout, include_platform_probe=include_platform_probe)
        for host in selected_hosts
    ]
    summaries = [summarize_for_compare(result) for result in results]
    category_counts: dict[str, int] = {}
    unhealthy_hosts = []
    for summary in summaries:
        if not summary.get("command_health", {}).get("healthy", False):
            unhealthy_hosts.append(summary["host"])
        for category, count in summary.get("signal_categories", {}).items():
            category_counts[category] = category_counts.get(category, 0) + count
    comparison = {
        "run_id": run_id,
        "bundle": bundle,
        "selected_hosts": selected_hosts,
        "labels_filter": labels or [],
        "summary": "Compared read-only diagnostic bundle results across hosts.",
        "dominant_signal_categories": sorted(category_counts.items(), key=lambda item: item[1], reverse=True),
        "unhealthy_collection_hosts": unhealthy_hosts,
        "host_summaries": summaries,
        "results": results,
    }
    audit_record(
        config,
        {
            "event": "ssh_compare_hosts",
            "run_id": run_id,
            "bundle": bundle,
            "selected_hosts": selected_hosts,
            "labels_filter": labels or [],
            "dominant_signal_categories": comparison["dominant_signal_categories"],
            "unhealthy_collection_hosts": unhealthy_hosts,
        },
    )
    return comparison


def parse_k8s_mapping(result: dict[str, Any], pod_name: str | None = None, pod_uid: str | None = None) -> dict[str, Any]:
    commands = result.get("commands", [])
    combined = "\n".join(command.get("stdout", "") for command in commands)
    candidates = []
    search_terms = [term for term in [pod_uid, pod_name] if term]
    for line in combined.splitlines():
        if not search_terms or any(term in line for term in search_terms):
            if "pod" in line.lower() or "/sys/fs/cgroup" in line or "/var/lib/kubelet/pods" in line:
                candidates.append(line[:500])
    cgroup_paths = sorted(set(re.findall(r"(/sys/fs/cgroup/[^\s]+pod[^\s]+)", combined)))[:20]
    kubelet_pod_dirs = sorted(set(re.findall(r"(/var/lib/kubelet/pods/[0-9a-fA-F-]+)", combined)))[:20]
    return {
        "node_host": result["host"],
        "pod_name_filter": pod_name,
        "pod_uid_filter": pod_uid,
        "candidate_lines": candidates[:40],
        "cgroup_paths": cgroup_paths,
        "kubelet_pod_dirs": kubelet_pod_dirs,
        "explanation": [
            "Use pod UID matches to connect Kubernetes API objects to /var/lib/kubelet/pods and cgroup paths.",
            "If no candidate appears, collect Kubernetes API pod metadata first, especially nodeName, pod UID, namespace, and container IDs.",
            "Compare cgroup CPU/memory/io evidence against node-level pressure before blaming the pod process.",
        ],
    }


def map_k8s_cgroup(
    config: dict[str, Any],
    host: str,
    pod_name: str | None = None,
    pod_uid: str | None = None,
    namespace: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    result = run_bundle(config, host, "k8s_node_pod_cgroup", timeout=timeout)
    mapping = parse_k8s_mapping(result, pod_name=pod_name, pod_uid=pod_uid)
    mapping["namespace_filter"] = namespace
    output = {
        "host": host,
        "bundle_result": result,
        "mapping": mapping,
        "safety": {
            "automatic_fixes_run": False,
            "remote_commands_are_predefined": True,
        },
    }
    audit_record(
        config,
        {
            "event": "ssh_k8s_map",
            "run_id": result.get("run_id"),
            "host": host,
            "pod_name_filter_hash": hashlib.sha256((pod_name or "").encode("utf-8")).hexdigest() if pod_name else None,
            "pod_uid_filter_hash": hashlib.sha256((pod_uid or "").encode("utf-8")).hexdigest() if pod_uid else None,
            "namespace_filter_hash": hashlib.sha256((namespace or "").encode("utf-8")).hexdigest() if namespace else None,
        },
    )
    return apply_redaction(config, host_entry(config, host), output)


def audit_record(config: dict[str, Any], record: dict[str, Any]) -> None:
    cfg = audit_config(config)
    if not cfg["enabled"]:
        return
    path = Path(cfg["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def command_audit_summary(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for command in commands:
        summaries.append(
            {
                "id": command["id"],
                "command_hash": hashlib.sha256(command["command"].encode("utf-8")).hexdigest(),
                "exit_code": command["exit_code"],
                "exit_ok": command.get("exit_ok"),
                "fallback_used": command.get("fallback_used", False),
                "sudo_used": command.get("sudo_used", False),
                "timed_out": command.get("timed_out", False),
                "stdout_bytes": command.get("stdout_bytes", 0),
                "stderr_bytes": command.get("stderr_bytes", 0),
                "stdout_truncated": command.get("stdout_truncated", False),
                "stderr_truncated": command.get("stderr_truncated", False),
            }
        )
    return summaries


def platform_audit_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "distro": profile.get("distro"),
        "distro_family": profile.get("distro_family"),
        "version": profile.get("version"),
        "init": profile.get("init"),
        "cgroup": profile.get("cgroup"),
        "containerized": profile.get("containerized"),
        "available_tools": profile.get("available_tools", []),
        "missing_core_tools": profile.get("missing_core_tools", []),
        "kernel_hash": hashlib.sha256(str(profile.get("kernel", "")).encode("utf-8")).hexdigest()
        if profile.get("kernel")
        else None,
    }


def apply_redaction(config: dict[str, Any], entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return Redactor(redaction_config(config, entry)).object(result)


def run_bundle(
    config: dict[str, Any],
    host: str,
    bundle: str,
    timeout: int | None = None,
    include_platform_probe: bool = True,
) -> dict[str, Any]:
    if bundle not in BUNDLES:
        raise ValueError(f"unknown bundle: {bundle}")
    entry = host_entry(config, host)
    run_id = str(uuid.uuid4())
    per_command_timeout = int(timeout or entry.get("command_timeout", 20))
    started = time.time()
    platform_commands: list[dict[str, Any]] = []
    platform_profile: dict[str, Any] | None = None
    if include_platform_probe and bundle != "platform_probe":
        platform_commands = [
            run_command_spec(config, host, entry, "platform_probe", spec, per_command_timeout)
            for spec in BUNDLES["platform_probe"]
        ]
        platform_profile = build_platform_profile(platform_commands)
    commands = [
        run_command_spec(config, host, entry, bundle, spec, per_command_timeout, platform_profile=platform_profile)
        for spec in BUNDLES[bundle]
    ]
    if bundle == "platform_probe":
        platform_profile = build_platform_profile(commands)
    report = build_diagnostic_report(bundle, commands, platform_profile=platform_profile)
    result = {
        "run_id": run_id,
        "host": host,
        "host_metadata": {
            "hostname": entry.get("hostname", host),
            "labels": label_list(entry),
            "label_map": merged_labels(entry),
        },
        "bundle": bundle,
        "started_at_unix": int(started),
        "duration_ms": int((time.time() - started) * 1000),
        "per_command_timeout_seconds": per_command_timeout,
        "platform_probe": {
            "included": bool(platform_commands) or bundle == "platform_probe",
            "commands": platform_commands if bundle != "platform_probe" else commands,
        },
        "diagnostic_report": report,
        "commands": commands,
    }
    audit_record(
        config,
        {
            "event": "ssh_run_bundle",
            "run_id": run_id,
            "host": host,
            "hostname": entry.get("hostname", host),
            "labels": label_list(entry),
            "bundle": bundle,
            "started_at_unix": result["started_at_unix"],
            "duration_ms": result["duration_ms"],
            "platform_profile": platform_audit_summary(report.get("platform_profile", {})),
            "command_health": report["command_health"],
            "commands": command_audit_summary(commands),
        },
    )
    return apply_redaction(config, entry, result)


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
            "description": "Run a predefined read-only Linux diagnostic bundle on an allowed SSH host. Includes platform_probe metadata by default.",
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
                    "include_platform_probe": {
                        "type": "boolean",
                        "description": "Whether to collect platform profile before the requested bundle. Defaults to true.",
                    },
                },
                "required": ["host", "bundle"],
                "additionalProperties": False,
            },
        },
        {
            "name": "ssh_compare_hosts",
            "description": "Run one predefined read-only diagnostic bundle across multiple configured hosts and compare structured signals.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bundle": {"type": "string", "enum": sorted(BUNDLES)},
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional configured host aliases. If omitted, hosts are selected by labels or all hosts.",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional labels such as prod, api, role=api, or az=sh-a.",
                    },
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
                    "max_hosts": {"type": "integer", "minimum": 1, "maximum": 50},
                    "include_platform_probe": {"type": "boolean"},
                },
                "required": ["bundle"],
                "additionalProperties": False,
            },
        },
        {
            "name": "ssh_k8s_map",
            "description": "Collect read-only Kubernetes node/pod/cgroup mapping evidence from one configured node host.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Configured Kubernetes node host alias."},
                    "pod_name": {"type": "string"},
                    "pod_uid": {"type": "string"},
                    "namespace": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["host"],
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
                    result = content_text(Redactor(redaction_config(self.config)).object(list_hosts(self.config)))
                elif name == "ssh_run_bundle":
                    result = content_text(
                        run_bundle(
                            self.config,
                            host=str(args["host"]),
                            bundle=str(args["bundle"]),
                            timeout=args.get("timeout"),
                            include_platform_probe=bool(args.get("include_platform_probe", True)),
                        )
                    )
                elif name == "ssh_compare_hosts":
                    result = content_text(
                        compare_hosts(
                            self.config,
                            bundle=str(args["bundle"]),
                            hosts=as_list(args.get("hosts")) if args.get("hosts") else None,
                            labels=as_list(args.get("labels")) if args.get("labels") else None,
                            timeout=args.get("timeout"),
                            max_hosts=int(args.get("max_hosts", 10)),
                            include_platform_probe=bool(args.get("include_platform_probe", True)),
                        )
                    )
                elif name == "ssh_k8s_map":
                    result = content_text(
                        map_k8s_cgroup(
                            self.config,
                            host=str(args["host"]),
                            pod_name=args.get("pod_name"),
                            pod_uid=args.get("pod_uid"),
                            namespace=args.get("namespace"),
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
