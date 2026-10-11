#!/usr/bin/env python3
"""Package tracked module payload only, with a marked experimental build."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = [
    "META-INF", "bin", "etc", "scripts", "webroot", "LICENSE", "README.md",
    "action.sh", "changelog.md", "customize.sh", "module.prop", "service.sh",
    "setting.conf", "uninstall.sh", "docs/flyme-package-compat.md",
]
REQUIRED = {
    "module.prop", "customize.sh", "service.sh", "setting.conf", "bin/mihomo",
    "bin/AdGuardHome", "etc/SmartDNS/smartdns", "etc/SmartDNS/smartdns_ui.so",
    "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script",
    "scripts/service/mihomo-launch.sh", "scripts/compat/packages.sh",
    "scripts/compat/packages-list.awk",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flyme-compat", action="store_true", help="Default compatibility mode to true in the ZIP")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    commit = git("rev-parse", "HEAD").decode().strip()
    timestamp = time.gmtime(int(git("show", "-s", "--format=%ct", "HEAD")))[:6]
    files = sorted(p.decode() for p in git("ls-files", "-z", "--", *PAYLOAD).split(b"\0") if p)
    if not REQUIRED.issubset(files):
        raise RuntimeError(f"Missing payload: {sorted(REQUIRED.difference(files))}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    variant = "flyme-test" if args.flyme_compat else "test"
    archive = args.output_dir / f"SAM-{variant}-{commit[:8]}.zip"

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name in files:
            data = (ROOT / name).read_bytes()
            if name == "module.prop":
                lines = []
                for line in data.decode().splitlines():
                    if line.startswith("updateJson="):
                        continue  # A test module must not advertise the upstream auto-update feed.
                    if line.startswith("version="):
                        line += f"-{variant}.{commit[:8]}"
                    if line.startswith("name="):
                        line += " (Flyme test)" if args.flyme_compat else " (test)"
                    lines.append(line)
                data = ("\n".join(lines) + "\n").encode()
            elif name == "setting.conf" and args.flyme_compat:
                old = b"MIHOMO_PACKAGE_COMPAT=false"
                if data.count(old) != 1:
                    raise RuntimeError("Unexpected source compatibility preset")
                data = data.replace(old, b"MIHOMO_PACKAGE_COMPAT=true")
            executable = (name.endswith(".sh") or name.startswith("bin/") or
                          name in {"META-INF/com/google/android/update-binary", "etc/SmartDNS/smartdns"})
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 3
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, data)

        for directory in ("etc/SmartDNS/data/", "etc/mihomo/proxy_provider/"):
            info = zipfile.ZipInfo(directory, timestamp)
            info.create_system = 3
            info.external_attr = (0o40755 << 16) | 0x10
            output.writestr(info, b"")
        output.writestr("BUILD-INFO.txt", f"Source: https://github.com/maotsuai/SAM/commit/{commit}\n"
                        f"Variant: {variant}\nCompatibility preset: {str(args.flyme_compat).lower()}\n"
                        "Existing settings are preserved during upgrade.\n"
                        "Experimental module; Android device validation required.\n")

    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        if not REQUIRED.issubset(check.namelist()):
            raise RuntimeError("ZIP root layout is invalid")
        if b"id=SAM\n" not in check.read("module.prop"):
            raise RuntimeError("Module ID must stay SAM for the existing runtime paths")
        for binary in ("bin/mihomo", "bin/AdGuardHome", "etc/SmartDNS/smartdns"):
            with check.open(binary) as content:
                if content.read(4) != b"\x7fELF":
                    raise RuntimeError(f"Invalid bundled executable: {binary}")
        if args.flyme_compat and b"MIHOMO_PACKAGE_COMPAT=true\n" not in check.read("setting.conf"):
            raise RuntimeError("Compatibility mode was not enabled in the test package")

    digest = hashlib.sha256()
    with archive.open("rb") as content:
        for chunk in iter(lambda: content.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    checksum_path = archive.with_suffix(".zip.sha256")
    checksum_path.write_text(f"{checksum}  {archive.name}\n")
    print(f"Built {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)\nSHA-256: {checksum}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"zip_path={archive.as_posix()}\nsha256_path={checksum_path.as_posix()}\n"
                         f"filename={archive.name}\nsha256={checksum}\n")


if __name__ == "__main__":
    main()
