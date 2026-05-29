import argparse
import socket
import subprocess
import sys
from typing import Optional


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


def _windows_kill_process_on_port(port: int) -> bool:
    # Uses netstat -> findstr -> taskkill.
    # Note: requires Windows tools (netstat, findstr, taskkill).
    try:
        cmd = (
            f'netstat -ano | findstr :{port}'
        )
        netstat_out = subprocess.check_output(cmd, shell=True, text=True, errors='ignore')
    except subprocess.CalledProcessError:
        return False

    pids = set()
    for line in netstat_out.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            pid = parts[-1]
            if pid.isdigit():
                pids.add(int(pid))

    if not pids:
        return False

    for pid in sorted(pids):
        subprocess.run(
            f'taskkill /F /PID {pid}',
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return True


def kill_port(port: int, host: str = '127.0.0.1') -> bool:
    if not _port_in_use(host, port):
        return False

    if sys.platform.startswith('win'):
        return _windows_kill_process_on_port(port)

    # Non-Windows fallback (best-effort): use lsof if available.
    try:
        out = subprocess.check_output(['lsof', '-t', f'-i:{port}']).decode('utf-8', errors='ignore')
        pids = [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
        for pid in pids:
            subprocess.run(['kill', '-9', str(pid)], check=False)
        return bool(pids)
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description='Kill whatever is listening on a TCP port.')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', type=str, default='127.0.0.1')
    args = ap.parse_args()

    killed = kill_port(args.port, args.host)
    print('killed' if killed else 'nothing_to_kill')


if __name__ == '__main__':
    main()

