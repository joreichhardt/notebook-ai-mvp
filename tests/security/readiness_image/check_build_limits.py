"""Optional native dependency-build gate; never copied into the runtime image.

Legacy Docker build has no cgroup PidsLimit flag. Here PID containment is the
nonroot real-UID RLIMIT_NPROC, shared with other processes of UID10001, not a
container-specific cgroup limit. Root preparation/stamping is fixed trusted code.
"""

import os
import resource
from pathlib import Path

MARKER = "NOTEBOOK_BUILD_LIMITS_OK"


def check(cgroup=Path("/sys/fs/cgroup"), status=Path("/proc/self/status")):
    quota, period = map(int, (cgroup / "cpu.max").read_text().split())
    flags = dict(line.split(":", 1) for line in status.read_text().splitlines() if ":" in line)
    valid = (
        os.geteuid() == 10001
        and os.getegid() == 10001
        and quota == period
        and quota > 0
        and (cgroup / "memory.max").read_text().strip() == "1073741824"
        and (cgroup / "memory.swap.max").read_text().strip() == "0"
        and resource.getrlimit(resource.RLIMIT_NPROC) == (128, 128)
        and resource.getrlimit(resource.RLIMIT_NOFILE) == (1024, 1024)
        and int(flags["CapEff"].strip(), 16) == 0
        and flags["Seccomp"].strip() == "2"
    )
    if not valid:
        raise RuntimeError("build_limits_failed")


if __name__ == "__main__":
    check()
    print(MARKER, flush=True)
