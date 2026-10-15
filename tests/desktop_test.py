"""Desktop integration tests without hardware, systemd changes, or root."""
# SPDX-License-Identifier: GPL-3.0-only
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import termios
import unittest
from unittest.mock import patch

DESKTOP = Path(__file__).resolve().parents[1] / "desktop"
sys.path.insert(0, str(DESKTOP))
import c5_device


def load_script(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(DESKTOP / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


control = load_script("c5_control", "esp32c5-airdrop-control")
session = load_script("c5_session", "esp32c5-airdrop-session")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tty = self.root / "tty"
        self.tty.mkdir()
        self.registry = self.root / "devices.json"
        self.registry.write_text(json.dumps({"serials": ["TEST_C5_001"]}))

    def tearDown(self):
        self.temporary.cleanup()

    def board(self, interface="ttyACM0", vendor="303a", product="1001", serial="TEST_C5_001"):
        usb = self.root / "usb" / interface
        usb.mkdir(parents=True)
        for name, content in (("idVendor", vendor), ("idProduct", product), ("serial", serial)):
            (usb / name).write_text(content)
        child = usb / "interface"
        child.mkdir()
        tty = self.tty / interface
        tty.mkdir()
        (tty / "device").symlink_to(child, target_is_directory=True)

    def test_absent(self):
        self.assertEqual(c5_device.registered_devices(self.tty, self.registry), [])

    def test_exact_registered_device(self):
        self.board()
        self.assertTrue(c5_device.is_registered("ttyACM0", self.tty, self.registry))
        self.assertFalse(c5_device.is_registered("../ttyACM0", self.tty, self.registry))

    def test_same_usb_id_unregistered_device_is_not_selected(self):
        self.board(serial="OTHER_ESP_BOARD")
        self.assertEqual(len(c5_device.discover(self.tty)), 1)
        self.assertEqual(c5_device.registered_devices(self.tty, self.registry), [])

    def test_registered_serial_wrong_usb_id_is_not_selected(self):
        self.board(vendor="1234")
        self.assertEqual(c5_device.registered_devices(self.tty, self.registry), [])

    def test_unsafe_serial_is_rejected(self):
        self.board(serial='BAD";TAG+="systemd')
        self.assertEqual(c5_device.discover(self.tty), [])

    def test_malformed_registry_fails_closed(self):
        self.board()
        for content in ('[]', '{', '{"serials":"TEST_C5_001"}', '{"serials":[1]}',
                        '{"serials":["TEST_C5_001","bad/serial"]}'):
            with self.subTest(content=content):
                self.registry.write_text(content)
                self.assertEqual(c5_device.registered_devices(self.tty, self.registry), [])

    def test_private_output_refuses_symlinks_and_public_files(self):
        target = self.root / "target"
        target.write_text("unchanged")
        link = self.root / "output"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            c5_device.private_output(link, {"devices": []})
        self.assertEqual(target.read_text(), "unchanged")
        with self.assertRaises(ValueError):
            c5_device.private_output(target, {"devices": []})
        self.assertEqual(target.read_text(), "unchanged")


class VerificationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.first = c5_device.Device("ttyACM0", "TEST_C5_001")
        self.second = c5_device.Device("ttyACM1", "TEST_C5_002")

    def test_zero_or_ambiguous_candidates_never_open_ports(self):
        for devices in ([], [self.first, self.second]):
            with self.subTest(count=len(devices)), \
                    patch.object(c5_device, "discover", return_value=devices), \
                    patch.object(c5_device, "hello") as hello, \
                    patch.object(c5_device, "private_output") as output, \
                    patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(c5_device.main(["discover", "--verify", "--output", "unused"]), 3)
                hello.assert_not_called()
                output.assert_not_called()
                self.assertEqual(json.loads(stdout.getvalue())["candidates"], len(devices))
                self.assertNotIn("TEST_C5", stdout.getvalue())

    def test_explicit_interface_probes_only_selected_board(self):
        with patch.object(c5_device, "discover", return_value=[self.first, self.second]), \
                patch.object(c5_device, "hello", return_value="cachydrop-c5-radio-test") as hello, \
                patch.object(c5_device, "private_output") as output, \
                patch.object(sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(c5_device.main(["discover", "--verify", "--interface", "ttyACM1",
                                            "--output", "unused"]), 0)
            hello.assert_called_once_with("/dev/ttyACM1")
            self.assertEqual(output.call_args.args[1]["devices"],
                             [{"interface": "ttyACM1", "serial": "TEST_C5_002",
                               "firmware": "cachydrop-c5-radio-test"}])

    def test_single_candidate_can_be_verified_without_interface(self):
        with patch.object(c5_device, "discover", return_value=[self.first]), \
                patch.object(c5_device, "hello", return_value="cachydrop-c5-radio-test") as hello, \
                patch.object(sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(c5_device.main(["discover", "--verify"]), 0)
            hello.assert_called_once_with("/dev/ttyACM0")

    def test_missing_explicit_interface_does_not_probe_other_board(self):
        with patch.object(c5_device, "discover", return_value=[self.first]), \
                patch.object(c5_device, "hello") as hello, \
                patch.object(sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(c5_device.main(["discover", "--verify", "--interface", "ttyACM1"]), 3)
            hello.assert_not_called()

    def test_invalid_interface_is_rejected_before_discovery(self):
        for interface in ("../ttyACM0", "ttyACM0;reboot", "wlan0", "ttyACM١"):
            with self.subTest(interface=interface), \
                    patch.object(c5_device, "discover") as discover, \
                    patch.object(c5_device, "hello") as hello, \
                    patch.object(sys, "stderr", new_callable=io.StringIO), \
                    self.assertRaises(SystemExit) as error:
                c5_device.main(["discover", "--verify", "--interface", interface])
            self.assertEqual(error.exception.code, 2)
            discover.assert_not_called()
            hello.assert_not_called()


class HelloTests(unittest.TestCase):
    def verify(self, info):
        incoming = bytearray()
        sent = []
        controls = []
        attrs = [0, 0, termios.HUPCL, 0, termios.B9600, termios.B9600, [0] * 32]

        def write(fd, data):
            sent.append(bytes(data))
            _, kind, version, length, sequence = c5_device.HEADER.unpack_from(data)
            self.assertEqual((kind, version, length), (1, 1, 0))  # HELLO, never START.
            # Exercise noise and a valid response with an unrelated sequence.
            incoming.extend(b"boot text\n" + c5_device.packet(129, sequence ^ 1, b"{}") +
                            c5_device.packet(129, sequence, json.dumps(info).encode()))
            return len(data)

        def read(fd, size):
            chunk = bytes(incoming[:7])  # Split every frame into small fragments.
            del incoming[:7]
            return chunk

        def select(readers, writers, exceptions, timeout):
            return readers if incoming else [], writers, []

        with patch.object(c5_device.os, "open", return_value=41), \
                patch.object(c5_device.os, "close"), \
                patch.object(c5_device.os, "write", side_effect=write), \
                patch.object(c5_device.os, "read", side_effect=read), \
                patch.object(c5_device.fcntl, "ioctl", side_effect=lambda fd, command: controls.append(command)), \
                patch.object(c5_device.termios, "tcgetattr", return_value=attrs), \
                patch.object(c5_device.termios, "tcsetattr"), \
                patch.object(c5_device.select, "select", side_effect=select):
            try:
                return c5_device.hello("/dev/test", timeout=0.1)
            finally:
                self.assertEqual(len(sent), 1)
                self.assertEqual(controls, [termios.TIOCEXCL, termios.TIOCNXCL])
                self.assertFalse(attrs[2] & termios.HUPCL)

    def test_compatible_firmware(self):
        self.assertEqual(self.verify({"firmware": "cachydrop-c5-radio-test", "protocol": 1}),
                         "cachydrop-c5-radio-test")

    def test_named_firmware_remains_compatible(self):
        self.assertEqual(self.verify({"firmware": "cachydrop-c5-radio-0.3", "protocol": 1,
                                      "device_name": c5_device.DEVICE_LABEL}),
                         "cachydrop-c5-radio-0.3")

    def test_same_usb_id_wrong_firmware_is_rejected(self):
        with self.assertRaises(ValueError):
            self.verify({"firmware": "unrelated-esp-firmware", "protocol": 1})

    def test_wrong_protocol_is_rejected(self):
        for version in (2, True, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.verify({"firmware": "cachydrop-c5-radio-test", "protocol": version})


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.devices = patch.object(c5_device, "registered_devices",
                                    return_value=[c5_device.Device("ttyACM0", "TEST_C5_001")])
        self.devices.start()
        self.addCleanup(self.devices.stop)

    def status(self, unit="inactive", saved=None):
        with patch.object(control, "unit_state", return_value=unit), \
                patch.object(control, "read_state", return_value=saved or {}), \
                patch.object(control.time, "time", return_value=1000):
            return control.status()

    def test_disconnected_is_absent_even_with_old_state(self):
        with patch.object(c5_device, "registered_devices", return_value=[]):
            self.assertEqual(self.status("active", {"state": "receiving"})["state"], "absent")

    def test_inactive_is_ready_after_timeout(self):
        result = self.status(saved={"state": "receiving", "expires_at": 990})
        self.assertEqual((result["state"], result["remaining"]), ("ready", 0))
        self.assertEqual(result["device"], "CachyDrop-C5-Reciever")
        self.assertIn(result["device"], result["detail"])

    def test_starting_does_not_reuse_stale_ready(self):
        self.assertEqual(self.status("active", {"state": "ready"})["state"], "starting")
        self.assertEqual(self.status("activating")["state"], "starting")

    def test_receiving_countdown(self):
        result = self.status("active", {"state": "receiving", "expires_at": 1234})
        self.assertEqual((result["state"], result["remaining"]), ("receiving", 234))
        self.assertEqual(result["device"], "CachyDrop-C5-Reciever")

    def test_expired_active_window_is_stopping(self):
        self.assertEqual(self.status("active", {"state": "receiving", "expires_at": 999})["state"], "stopping")
        self.assertEqual(self.status("deactivating")["state"], "stopping")

    def test_error_is_retained(self):
        self.assertEqual(self.status("failed")["state"], "error")
        self.assertEqual(self.status(saved={"state": "error", "detail": "failed"})["detail"], "failed")
        self.assertNotEqual(self.status("failed", {"state": "receiving", "detail": "Visible"})["detail"], "Visible")

    def test_invalid_unit_is_rejected_before_systemctl(self):
        for interface in ("wlan0", "../ttyACM0", "ttyACM0.service", "ttyACM0;reboot", "ttyACM١"):
            with self.subTest(interface=interface), self.assertRaises(ValueError):
                control.unit_for(interface)
        self.assertEqual(control.unit_for("ttyACM12"), "esp32c5-airdrop@ttyACM12.service")

    def test_start_rechecks_registration(self):
        with patch.object(c5_device, "is_registered", return_value=False), \
                patch.object(control.subprocess, "run") as command:
            self.assertEqual(control.manage("start", "ttyACM0"), 3)
            command.assert_not_called()

    def test_toggle_and_stop_dispatch_only_own_unit(self):
        with patch.object(control, "status", return_value={"state": "receiving", "interface": "ttyACM0"}), \
                patch.object(control, "manage", return_value=0) as manage:
            self.assertEqual(control.main(["toggle"]), 0)
            manage.assert_called_once_with("stop", "ttyACM0")


class SessionTests(unittest.TestCase):
    def test_receive_ready_status_uses_shared_device_label(self):
        launcher = DESKTOP.parent / "c5/receive.sh"
        ready_status = launcher.read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "ttyACM0.json"
            with patch.object(sys, "argv", ["-", str(status_path), "ttyACM0", "1600", str(DESKTOP)]):
                exec(compile(ready_status, str(launcher), "exec"), {})
            data = json.loads(status_path.read_text())
            self.assertEqual(data["state"], "receiving")
            self.assertEqual(data["device"], c5_device.DEVICE_LABEL)
            self.assertEqual(data["expires_at"], 1600)

    def test_unregistered_device_never_launches(self):
        with patch.object(c5_device, "is_registered", return_value=False), \
                patch.object(session, "write_state") as status, \
                patch.object(session.subprocess, "Popen") as spawn:
            self.assertEqual(session.main(["ttyACM0"]), 3)
            self.assertEqual(status.call_args.args[1], "absent")
            spawn.assert_not_called()

    def test_launcher_receives_exact_device_and_window(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "c5").mkdir()
            (project / "c5/receive.sh").write_text("#!/bin/bash\n")
            with patch.dict(os.environ, {"C5_PROJECT": directory, "XDG_RUNTIME_DIR": str(project / "not-created")}), \
                    patch.object(c5_device, "is_registered", return_value=True), \
                    patch.object(session, "configuration", return_value=("00", "6", 10)), \
                    patch.object(session, "write_state") as status, \
                    patch.object(session.signal, "signal"), \
                    patch.object(session.subprocess, "Popen") as spawn:
                spawn.return_value.wait.return_value = 0
                spawn.return_value.poll.return_value = 0
                self.assertEqual(session.main(["ttyACM0"]), 0)
                command = spawn.call_args.args[0]
                self.assertEqual(command[2:], ["-p", "/dev/ttyACM0", "-c", "6", "-r", "00", "-s", "10"])
                environment = spawn.call_args.kwargs["env"]
                self.assertEqual(environment["C5_STATUS_INTERFACE"], "ttyACM0")
                self.assertNotIn("XDG_RUNTIME_DIR", environment)
                self.assertEqual(status.call_args.args[1], "ready")

    def test_final_states(self):
        with patch.object(c5_device, "is_registered", return_value=True):
            self.assertEqual(session.final_state("ttyACM0", 0, False)[0], "ready")
            self.assertEqual(session.final_state("ttyACM0", 1, False)[0], "error")
            self.assertEqual(session.final_state("ttyACM0", -15, True)[0], "ready")
        with patch.object(c5_device, "is_registered", return_value=False):
            self.assertEqual(session.final_state("ttyACM0", 1, False)[0], "absent")

    def test_configuration_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / "regdomain").write_text("00\n")
            (config / "channel").write_text("6\n")
            with patch.object(session, "CONFIG_DIR", config), patch.dict(os.environ, {"C5_WINDOW": "600"}):
                self.assertEqual(session.configuration(), ("00", "6", 600))
                (config / "regdomain").write_text("A;")
                with self.assertRaises(ValueError):
                    session.configuration()
                (config / "regdomain").write_text("00")
                (config / "channel").write_text("165")
                with self.assertRaises(ValueError):
                    session.configuration()

    def test_atomic_state_file_has_no_serial(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(session, "STATE_DIR", Path(directory)):
            session.write_state("ttyACM0", "ready", "Connected")
            data = json.loads((Path(directory) / "ttyACM0.json").read_text())
            self.assertEqual(data["backend"], "esp32c5")
            self.assertEqual(data["device"], "CachyDrop-C5-Reciever")
            self.assertNotIn("serial", data)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
