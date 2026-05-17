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
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SERVER_NAME = "linux-ssh-diagnostics"
SERVER_VERSION = "0.1.0"

DEFAULT_CONFIG = "~/.config/linux-troubleshooting/ssh-hosts.json"


BUNDLES: dict[str, list[str]] = {
    "snapshot_60s": [
        "date; hostname; uptime",
        "dmesg -T | tail -80",
        "vmstat 1 5",
        "mpstat -P ALL 1 3",
        "pidstat -u -d -r -w 1 5",
        "iostat -xz 1 5",
        "free -h",
        "sar -n DEV,TCP,ETCP 1 5",
        "top -bn1 | head -40",
    ],
    "cpu_basic": [
        "uptime",
        "top -bn1 | head -40",
        "vmstat 1 5",
        "mpstat -P ALL 1 3",
        "pidstat -u -t 1 5",
        "pidstat -w 1 5",
        "cat /proc/loadavg",
        "grep -E 'processor|cpu cores|siblings|model name' /proc/cpuinfo | head -80",
    ],
    "memory_basic": [
        "free -h",
        "vmstat 1 5",
        "grep -E 'MemAvailable|MemFree|Buffers|Cached|SReclaimable|SUnreclaim|Slab|Dirty|Writeback|Shmem|SwapTotal|SwapFree|SwapCached|Active\\(file\\)|Inactive\\(file\\)|Active\\(anon\\)|Inactive\\(anon\\)' /proc/meminfo",
        "ps -eo pid,ppid,cmd,%mem,rss,vsz --sort=-rss | head -30",
        "slabtop -o | head -30",
        "dmesg -T | grep -Ei 'oom|out of memory|killed process' | tail -80",
        "cat /proc/pressure/memory 2>/dev/null || true",
    ],
    "io_basic": [
        "top -bn1 | head -40",
        "vmstat 1 5",
        "iostat -d -x 1 5",
        "pidstat -d 1 5",
        "df -h",
        "df -i",
        "lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL",
        "findmnt",
        "dmesg -T | grep -iE 'error|fail|reset|timeout|nvme|scsi|blk|I/O' | tail -100",
    ],
    "network_basic": [
        "ip addr",
        "ip route",
        "ss -s",
        "ss -antpi | head -120",
        "nstat -az",
        "ip -s link",
        "sar -n DEV,TCP,ETCP 1 5",
        "cat /proc/net/softnet_stat | head -20",
    ],
    "container_cgroup_basic": [
        "cat /proc/self/cgroup",
        "cat /sys/fs/cgroup/memory.current 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory.max 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory.stat 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory.events 2>/dev/null || true",
        "cat /sys/fs/cgroup/cpu.stat 2>/dev/null || true",
        "cat /sys/fs/cgroup/cpu.max 2>/dev/null || true",
        "cat /sys/fs/cgroup/io.stat 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory/memory.stat 2>/dev/null || true",
        "cat /sys/fs/cgroup/memory/memory.failcnt 2>/dev/null || true",
    ],
    "logs_oom_io_network": [
        "dmesg -T | tail -160",
        "journalctl -k --no-pager -n 200 2>/dev/null || true",
        "dmesg -T | grep -Ei 'oom|out of memory|killed process|blocked for more than|I/O error|reset|timeout|link is down|link is up|nf_conntrack|martian|segfault' | tail -160",
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


def run_remote(name: str, entry: dict[str, Any], command: str, timeout: int) -> dict[str, Any]:
    started = time.time()
    remote = "LC_ALL=C " + command
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
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = 124

    max_bytes = int(entry.get("max_output_bytes", 200_000))
    stdout_truncated = len(stdout.encode("utf-8", "replace")) > max_bytes
    stderr_truncated = len(stderr.encode("utf-8", "replace")) > max_bytes
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


def run_bundle(config: dict[str, Any], host: str, bundle: str, timeout: int | None = None) -> dict[str, Any]:
    if bundle not in BUNDLES:
        raise ValueError(f"unknown bundle: {bundle}")
    entry = host_entry(config, host)
    per_command_timeout = int(timeout or entry.get("command_timeout", 20))
    started = time.time()
    commands = [run_remote(host, entry, cmd, per_command_timeout) for cmd in BUNDLES[bundle]]
    return {
        "host": host,
        "bundle": bundle,
        "started_at_unix": int(started),
        "duration_ms": int((time.time() - started) * 1000),
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
