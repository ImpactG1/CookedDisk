"""Build automation script for compiling CookedDisk with PyInstaller."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def build():
    print("=== Building CookedDisk for Windows ===")
    root_dir = Path(__file__).resolve().parent

    spec_file = root_dir / "CookedDisk.spec"
    if not spec_file.is_file():
        print(f"Error: Spec file not found at {spec_file}")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    print(f"Running command: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(root_dir))
    if res.returncode != 0:
        print(f"Build failed with exit code: {res.returncode}")
        sys.exit(res.returncode)

    output_exe = root_dir / "dist" / "CookedDisk" / "CookedDisk.exe"
    if output_exe.is_file():
        size_mb = output_exe.stat().st_size / (1024 * 1024)
        print(f"\nBuild successful!")
        print(f"Standalone executable created at: {output_exe} ({size_mb:.1f} MB)")
    else:
        print(f"\nWarning: Output exe not found at expected location: {output_exe}")


if __name__ == "__main__":
    build()

