"""Offline checks: no Android database, namespace or mount is modified."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CONVERTER = ROOT / "scripts/compat/packages-list.awk"
HELPER = ROOT / "scripts/compat/packages.sh"


class PackageListTests(unittest.TestCase):
    def convert(self, content):
        return subprocess.run(["awk", "-f", str(CONVERTER)], input=content,
                              text=True, capture_output=True)

    def test_packages_shared_uids_and_duplicate_rows(self):
        result = self.convert(
            "android 1000 0 /data/system default 0\n"
            "com.example.one 10123 0 /data/user/0/com.example.one default 0\n"
            "com.example.two 10123 0 /data/user/0/com.example.two default 0\n"
            "com.example.one 10123 0 ignored extra columns\n\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        root = ET.fromstring(result.stdout)
        self.assertEqual(root.tag, "packages")
        self.assertEqual([(p.get("name"), p.get("userId")) for p in root], [
            ("android", "1000"), ("com.example.one", "10123"),
            ("com.example.two", "10123")])

    def test_invalid_or_empty_input_fails(self):
        for content in ["", "\n \n", "com.example.app\n", "com.example.app nope\n",
                        "com.example.app 0\n", "com.example.app -1\n",
                        "com.example.app 110123\n", 'bad&name 10123\n',
                        'bad\"name 10123\n', "com.example.app 1.5\n",
                        "com.example.app 10123\ncom.example.app 10124\n"]:
            with self.subTest(content=content):
                self.assertNotEqual(self.convert(content).returncode, 0)


class NamespaceGuardTests(unittest.TestCase):
    def run_helper(self, *, same_namespace=False, shared=False, fail_mount="", invalid=False,
                   empty_mountinfo=False):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "packages.list").write_text("invalid\n" if invalid else "com.example.app 10123 0 extra\n")
            target = work / "packages.xml"
            target.write_bytes(b"ABX\x00original")
            (work / "mountinfo").write_text("" if empty_mountinfo else
                "1 0 0:1 / / rw " + ("shared:1 " if shared else "") + "- rootfs rootfs rw\n")
            trace = work / "trace"
            script = r'''
. "$HELPER"
readlink() { printf '%s\n' "$CURRENT_NS"; }
busybox() {
    case "$1" in
        mount)
            printf '%s\n' "$*" >> "$TRACE"
            case "$2" in
                --make-rprivate) [ "$FAIL_MOUNT" != private ] ;;
                --bind) [ "$FAIL_MOUNT" != bind ] ;;
                -o) [ "$FAIL_MOUNT" != readonly ] ;;
                *) return 99 ;;
            esac
            ;;
        umount) printf '%s\n' "$*" >> "$TRACE" ;;
        *) command "$@" ;;
    esac
}
sam_mount_packages_view 'mnt:[1]' "$WORK/packages.list" "$WORK/packages.xml" \
    /unused/namespace "$WORK/mountinfo"
'''
            env = dict(os.environ, HELPER=str(HELPER), WORK=str(work), TRACE=str(trace),
                       TMP_PATH=str(work), SCRIPTS_PATH=str(ROOT / "scripts"),
                       CURRENT_NS="mnt:[1]" if same_namespace else "mnt:[2]",
                       FAIL_MOUNT=fail_mount)
            result = subprocess.run(["sh", "-c", script], env=env, text=True, capture_output=True)
            calls = trace.read_text().splitlines() if trace.exists() else []
            self.assertEqual(target.read_bytes(), b"ABX\x00original")
            snapshots = list(work.glob("mihomo-packages.*/packages.xml"))
            if result.returncode == 0:
                self.assertEqual(len(snapshots), 1)
                ET.fromstring(snapshots[0].read_text())
            else:
                self.assertEqual(snapshots, [])
            return result, calls

    def test_same_namespace_rejected_before_any_mount(self):
        result, calls = self.run_helper(same_namespace=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_shared_mount_rejected_before_bind(self):
        result, calls = self.run_helper(shared=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["mount --make-rprivate /"])

    def test_empty_mountinfo_rejected_before_bind(self):
        result, calls = self.run_helper(empty_mountinfo=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["mount --make-rprivate /"])

    def test_private_then_bind_then_readonly(self):
        result, calls = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], "mount --make-rprivate /")
        self.assertTrue(calls[1].startswith("mount --bind "))
        self.assertTrue(calls[2].startswith("mount -o remount,bind,ro "))

    def test_failures_do_not_continue(self):
        for stage in ["private", "bind", "readonly"]:
            with self.subTest(stage=stage):
                result, calls = self.run_helper(fail_mount=stage)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), {"private": 1, "bind": 2, "readonly": 4}[stage])
                if stage == "readonly":
                    self.assertTrue(calls[-1].startswith("umount "))

    def test_invalid_snapshot_never_bound(self):
        result, calls = self.run_helper(invalid=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["mount --make-rprivate /"])


class LauncherTests(unittest.TestCase):
    def run_launcher(self, mode, *, fail_unshare=False, isolated=False):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            trace = work / "trace"
            shim = work / "busybox"
            shim.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\n'
                            '[ "$FAIL_UNSHARE" != true ]\n')
            shim.chmod(0o755)
            readlink = work / "readlink"
            readlink.write_text("#!/bin/sh\nprintf 'mnt:[1]\\n'\n")
            readlink.chmod(0o755)
            launcher = work / "launch.sh"
            launcher.write_text((ROOT / "scripts/service/mihomo-launch.sh").read_text().replace(
                '. "/data/adb/modules/SAM/scripts/base.sh"',
                'log() { printf "%s\\n" "$2"; }', 1))
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ["PATH"],
                       TRACE=str(trace), FAIL_UNSHARE="true" if fail_unshare else "false",
                       MIHOMO_PACKAGE_COMPAT=mode, SAM_USER="root", TUN_GROUP="net_admin",
                       MIHOMO_BIN="mihomo", MIHOMO_PATH="/unused/config",
                       TMP_PATH=str(work), SCRIPTS_PATH=str(ROOT / "scripts"))
            args = ["sh", str(launcher)] + (["--packages-namespace", "mnt:[1]"] if isolated else [])
            result = subprocess.run(args, env=env, text=True, capture_output=True)
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_disabled_mode_executes_original_core_command(self):
        result, calls = self.run_launcher("false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["setuidgid root:net_admin mihomo -d /unused/config"])

    def test_enabled_mode_only_requests_mount_namespace(self):
        result, calls = self.run_launcher("true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("unshare -m /system/bin/sh "))
        self.assertTrue(calls[0].endswith("--packages-namespace mnt:[1]"))

    def test_unshare_failure_never_starts_unisolated_core(self):
        result, calls = self.run_launcher("true", fail_unshare=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("unshare "))

    def test_invalid_mode_and_direct_isolated_entry_fail(self):
        for mode, isolated in [("invalid", False), ("true", True)]:
            with self.subTest(mode=mode, isolated=isolated):
                result, calls = self.run_launcher(mode, isolated=isolated)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])


class ServiceReadinessTests(unittest.TestCase):
    def run_service(self, tun_ready):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name in ["mihomo.sh", "proxy.sh"]:
                path = work / name
                path.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\n')
                path.chmod(0o755)
            launcher = work / "mihomo-launch.sh"
            launcher.write_text('#!/bin/sh\n: > "$READY"\nexec /bin/sleep 30\n')
            launcher.chmod(0o755)
            # Only Android setup is replaced; execute the actual start function.
            service = (ROOT / "scripts/service/mihomo.sh").read_text().replace(
                'source "/data/adb/modules/SAM/scripts/base.sh"', ":", 1)
            prelude = r'''
MIHOMO_ENABLE=true
SUB_URL=(unused)
MIHOMO_BIN=mihomo
TUN_DEVICE=Meta
UPDATE_PATH="$WORK"
SERVICE_PATH="$WORK"
IPTABLES_PATH="$WORK"
TMP_PATH="$WORK"
isRun() { [ -e "$READY" ]; }
log() { printf '%s\n' "$2"; }
sleep() { /bin/sleep 0.02; }
ip() { [ "$TUN_READY" = true ]; }
'''
            env = dict(os.environ, WORK=str(work), READY=str(work / "ready"),
                       TRACE=str(work / "trace"), TUN_READY="true" if tun_ready else "false")
            result = subprocess.run(["bash", "-c", prelude + service +
                                     '\nstart\nresult=$?\nprintf "RESULT:%s PID:%s\\n" "$result" "$launch_pid"\n'],
                                    env=env, text=True, capture_output=True, timeout=5)
            status = next(line for line in result.stdout.splitlines() if line.startswith("RESULT:"))
            pid = int(status.split("PID:")[1])
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return result, status, (work / "trace").read_text().splitlines()

    def test_running_core_without_tun_is_failure(self):
        result, status, calls = self.run_service(False)
        self.assertIn("RESULT:1", status, result.stderr)
        self.assertNotIn("add", calls)

    def test_tun_ready_adds_forward_rules(self):
        result, status, calls = self.run_service(True)
        self.assertIn("RESULT:0", status, result.stderr)
        self.assertIn("add", calls)


if __name__ == "__main__":
    unittest.main()
