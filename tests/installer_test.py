"""Installer staging tests; never probe hardware or change system files."""
# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts/install-desktop.sh").read_text()
GENERATOR = INSTALLER.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'checkout with spaces % and "quotes"'
        (self.project / "desktop").mkdir(parents=True)
        shutil.copy(ROOT / "desktop/esp32c5-airdrop@.service.in", self.project / "desktop")
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.config = self.root / "config"
        self.registry = self.root / "devices.json"
        self.verified = {"devices": [{"interface": "ttyACM2", "serial": "TEST_C5_002",
                                      "firmware": "cachydrop-c5-radio-test"}]}

    def generate(self, overrides=None):
        (self.staging / "verified.json").write_text(json.dumps(self.verified))
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"REG", "C5_CHANNEL"}}
        environment.update(overrides or {})
        return subprocess.run([sys.executable, "-", str(self.staging), str(self.project),
                               "testuser", "testgroup", "/home/testuser", "1000",
                               str(self.config), str(self.registry)], input=GENERATOR,
                              text=True, capture_output=True, env=environment)

    def test_exact_identity_rules_default_config_and_unprivileged_unit(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("TEST_C5_002", result.stdout + result.stderr)
        rules = (self.staging / "70-esp32c5-airdrop.rules").read_text()
        for expected in ('ATTRS{idVendor}=="303a"', 'ATTRS{idProduct}=="1001"',
                         'ATTRS{serial}=="TEST_C5_002"', 'TAG+="systemd"', 'TAG+="uaccess"',
                         'ENV{SYSTEMD_WANTS}+="esp32c5-airdrop@%k.service"',
                         'SYMLINK+="CachyDrop-C5-Reciever"'):
            self.assertIn(expected, rules)
        active_rules = [line for line in rules.splitlines() if not line.startswith("#")]
        self.assertEqual(len(active_rules), 1)
        self.assertIn('ATTRS{serial}=="TEST_C5_002"', active_rules[0])
        self.assertEqual((self.staging / "regdomain").read_text(), "00\n")
        self.assertEqual((self.staging / "channel").read_text(), "6\n")
        self.assertEqual((self.staging / "interfaces").read_text(), "ttyACM2\n")
        unit = (self.staging / "esp32c5-airdrop@.service").read_text()
        for expected in ('User=testuser', 'Group=testgroup', 'Environment="C5_WINDOW=600"',
                         'BindsTo=dev-%i.device', 'Restart=no', 'KillMode=control-group',
                         'checkout with spaces %% and \\"quotes\\"'):
            self.assertIn(expected, unit)
        self.assertNotIn("@PROJECT@", unit)

    def test_config_and_explicit_overrides(self):
        self.config.write_text('# Local settings\nREG="au"\nC5_CHANNEL=149 # 5 GHz\n')
        self.assertEqual(self.generate().returncode, 0)
        self.assertEqual((self.staging / "regdomain").read_text(), "AU\n")
        self.assertEqual((self.staging / "channel").read_text(), "149\n")
        self.assertEqual(self.generate({"REG": "00", "C5_CHANNEL": "6"}).returncode, 0)
        self.assertEqual((self.staging / "channel").read_text(), "6\n")

    def test_accepts_only_empty_or_same_single_board_registration(self):
        for serials in ([], ["TEST_C5_002"]):
            with self.subTest(serials=serials):
                self.registry.write_text(json.dumps({"serials": serials}))
                self.assertEqual(self.generate().returncode, 0)
                registered = json.loads((self.staging / "devices.json").read_text())
                self.assertEqual(registered, {"serials": ["TEST_C5_002"]})

    def test_refuses_to_replace_or_extend_an_existing_registration(self):
        for serials in (["TEST_C5_001"], ["TEST_C5_001", "TEST_C5_002"],
                        ["TEST_C5_002", "TEST_C5_002"]):
            with self.subTest(serials=serials):
                original = json.dumps({"serials": serials})
                self.registry.write_text(original)
                result = self.generate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("different board or multiple boards", result.stderr)
                self.assertNotIn("TEST_C5", result.stderr + result.stdout)
                self.assertEqual(self.registry.read_text(), original)

    def test_rejects_unsafe_configuration_without_evaluating_shell(self):
        marker = self.root / "should-not-exist"
        for content in ("REG=00\nC5_CHANNEL=149", "C5_CHANNEL=165", "REG=BAD",
                        f'REG=$(touch {marker})', "REG=AU;reboot", "OTHER=value"):
            with self.subTest(content=content):
                self.config.write_text(content)
                result = self.generate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no system files changed", result.stderr)
                self.assertFalse(marker.exists())

    def test_rejects_malformed_registry_and_probe_data(self):
        for content in ('[]', '{', '{"serials":"test"}', '{"serials":["bad/serial"]}'):
            with self.subTest(content=content):
                self.registry.write_text(content)
                self.assertNotEqual(self.generate().returncode, 0)
                self.assertEqual(self.registry.read_text(), content)
        self.registry.unlink()
        for devices in ([], self.verified["devices"] * 2,
                        [{"interface": "ttyACM2;reboot", "serial": "TEST", "firmware": "bad"}],
                        [{"interface": "ttyACM2", "serial": 'bad"serial', "firmware": "bad"}]):
            self.verified = {"devices": devices}
            self.assertNotEqual(self.generate().returncode, 0)

    def test_no_privileged_action_when_firmware_probe_fails(self):
        (self.project / "scripts").mkdir()
        script = self.project / "scripts/install-desktop.sh"
        script.write_text(INSTALLER)
        shutil.copytree(ROOT / "desktop", self.project / "desktop", dirs_exist_ok=True)
        for name in ("c5/host/build/cachydrop-c5", ".venv-opendrop/bin/python"):
            target = self.project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("#!/bin/sh\nexit 0\n")
            target.chmod(0o755)
        commands = self.root / "commands"
        commands.mkdir()
        log = self.root / "sudo-called"
        probe_log = self.root / "probe-called"
        mocks = {"id": '#!/bin/sh\ncase "$1" in -u) echo 1000;; -un) echo testuser;; -gn) echo testgroup;; esac\n',
                 "python3": f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{probe_log}"\nexit 3\n',
                 "sudo": f'#!/bin/sh\ntouch "{log}"\nexit 99\n'}
        for name in ("systemctl", "udevadm", "kpackagetool6", "qdbus6", "notify-send", "dolphin", "getent"):
            mocks[name] = "#!/bin/sh\nexit 0\n"
        for name, content in mocks.items():
            (commands / name).write_text(content)
            (commands / name).chmod(0o755)
        environment = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"}
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Connect the intended flashed C5", result.stderr)
        self.assertFalse(log.exists())
        self.assertNotIn("--interface", probe_log.read_text())
        result = subprocess.run(["bash", str(script), "--device", "/dev/ttyACM2"],
                                capture_output=True, text=True, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--interface\nttyACM2\n", probe_log.read_text())
        self.assertFalse(log.exists())
        probe_log.unlink()
        for arguments in (["--device", "/dev/ttyUSB0"], ["--device", "ttyACM2"],
                          ["--device", "/dev/ttyACM2;reboot"], ["--all"], ["--device"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(["bash", str(script), *arguments],
                                        capture_output=True, text=True, env=environment)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(probe_log.exists())
                self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
