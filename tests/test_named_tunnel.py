"""Offline terminal checks for first-time and replacement Tunnel Token setup."""

import base64
import json
import os
from pathlib import Path
import pty
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIRST_PROMPT = "Tunnel Token（粘贴后回车，直接回车取消）："
SAVED_PROMPT = "Tunnel Token（回车沿用，粘贴新 Token 更换，输入 0 取消）："


def fixture_token(character):
    value = {"a": "a" * 32, "t": "11111111-2222-3333-4444-555555555555",
             "s": base64.b64encode(character.encode() * 32).decode()}
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


OLD_TOKEN = fixture_token("o")
NEW_TOKEN = fixture_token("n")
FAIL_TOKEN = fixture_token("f")


SERVICE_MOCK = r'''import json, os, re, sys
from pathlib import Path
base = Path(os.environ['IBT_TEST_HOME'])
db = base / 'services.json'
data = json.loads(db.read_text())
args = [x for x in sys.argv[1:] if x != '--user']
with (base / 'service-calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
action = args[0]
name = args[-1] if args else ''
entry = data.setdefault(name, {'active': False, 'enabled': False})
unit = base / '.config/systemd/user' / name
def save():
    db.write_text(json.dumps(data))
def start():
    content = unit.read_text() if unit.exists() else ''
    match = re.search(r'--token-file\s+([^\s]+)', content)
    token = ''
    if match:
        path = Path(match.group(1).replace('%h', str(base)))
        if path.exists(): token = path.read_text().strip()
    entry['active'] = token != os.environ.get('IBT_TEST_FAIL_TOKEN', '') and bool(token)
    save()
    return 0 if entry['active'] else 1
if action == 'daemon-reload':
    raise SystemExit(0)
if action == 'is-active':
    if '--quiet' not in args: print('active' if entry['active'] else 'inactive')
    raise SystemExit(0 if entry['active'] else 3)
if action == 'is-enabled':
    if '--quiet' not in args: print('enabled' if entry['enabled'] else 'disabled')
    raise SystemExit(0 if entry['enabled'] else 1)
if action == 'enable':
    entry['enabled'] = True
    save()
    raise SystemExit(start() if '--now' in args and not entry['active'] else 0)
if action == 'disable':
    entry['enabled'] = False
    if '--now' in args: entry['active'] = False
    save()
    raise SystemExit(0)
if action in ('start', 'restart'):
    if action == 'start' and entry['active']: raise SystemExit(0)
    raise SystemExit(start())
if action == 'stop':
    entry['active'] = False
    save()
    raise SystemExit(0)
if action == 'reset-failed':
    raise SystemExit(0)
if action == 'cat':
    if unit.exists(): print(unit.read_text(), end=''); raise SystemExit(0)
    raise SystemExit(1)
if action == 'show':
    values = {'FragmentPath': str(unit) if unit.exists() else '',
              'ActiveState': 'active' if entry['active'] else 'inactive',
              'LoadState': 'loaded' if unit.exists() else 'not-found'}
    prop = next((x.split('=', 1)[1] for x in args if x.startswith('--property=')), '')
    if not prop and '-p' in args: prop = args[args.index('-p') + 1]
    if prop:
        print(values.get(prop, '') if '--value' in args else prop + '=' + values.get(prop, ''))
    else:
        print('\n'.join(key + '=' + value for key, value in values.items()))
    raise SystemExit(0)
print('Unexpected fixture systemctl call: ' + repr(args), file=sys.stderr)
raise SystemExit(97)
'''


class NamedTunnelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ibt-named-tunnel-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture_home = self.base / "user"
        self.fixture_home.mkdir(mode=0o700)
        self.app = self.fixture_home / ".local/share/insta-box"
        self.binary = self.app / "bin"
        self.binary.mkdir(parents=True, mode=0o700)
        self.tokens = self.fixture_home / ".config/cloudflared"
        self.unit = self.fixture_home / ".config/systemd/user/insta-box-tunnel.service"
        self.state = self.app / "state.conf"
        self.state.write_text("mode=quick\ndomain=\nuuid=" + "1" * 36 +
                              "\npath=/ws-" + "2" * 36 + "\nport=23456\n")
        self.config = self.app / "config.json"
        self.config.write_text('{"fixture": "preserve node configuration"}\n')
        self.state.chmod(0o600)
        self.config.chmod(0o600)
        self.original_node = {path: path.read_bytes() for path in (self.state, self.config)}
        self.services = self.fixture_home / "services.json"
        self.services.write_text(json.dumps({
            "insta-box-node.service": {"active": True, "enabled": True},
            "insta-box-quick.service": {"active": True, "enabled": True},
        }))
        self.commands = self.base / "commands"
        self.commands.mkdir()
        self.command("systemctl", "#!" + sys.executable + "\n" + SERVICE_MOCK)
        self.command("loginctl", "#!/bin/sh\nprintf 'yes\\n'\n")
        for name in ("curl", "sudo", "cloudflared", "journalctl"):
            self.command(name, "#!/bin/sh\nprintf 'Unexpected external operation\\n' >&2\nexit 97\n")
        for name in ("cloudflared", "sing-box"):
            target = self.binary / name
            target.write_text("#!/bin/sh\nexit 97\n")
            target.chmod(0o700)
        self.env = dict(os.environ, IBT_TEST_HOME=str(self.fixture_home),
                        IBT_TEST_FAIL_TOKEN=FAIL_TOKEN, PYTHONDONTWRITEBYTECODE="1",
                        PATH=str(self.commands) + os.pathsep + os.environ["PATH"],
                        NO_COLOR="1", TERM="dumb")
        source = (ROOT / "install.sh").read_text()
        library, dispatch = source.rsplit('case "${1:-install}" in', 1)
        # Use task-scoped fixture paths without replacing the process HOME.
        source = library + "\npreflight() { :; }\nensure_linger() { :; }\nensure_cloudflared() { :; }\n" + 'case "${1:-install}" in' + dispatch
        source = source.replace("${HOME}", "${IBT_TEST_HOME}").replace("$HOME", "$IBT_TEST_HOME")
        self.script = self.base / "install.sh"
        self.script.write_text(source)

    def command(self, name, content):
        path = self.commands / name
        path.write_text(content)
        path.chmod(0o700)

    def saved(self, token=OLD_TOKEN, *, filename="token", managed=True, active=True):
        self.tokens.mkdir(parents=True, mode=0o700, exist_ok=True)
        target = self.tokens / filename
        target.write_text(token + "\n")
        target.chmod(0o600)
        self.unit.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.unit.write_text(("# Managed-by: InstaBox Tool\n" if managed else "# Owned by another app\n") +
                             "[Service]\nExecStart=%h/.local/share/insta-box/bin/cloudflared tunnel run --token-file " +
                             "%h/.config/cloudflared/" + filename + "\n")
        self.unit.chmod(0o600)
        services = json.loads(self.services.read_text())
        services["insta-box-tunnel.service"] = {"active": active, "enabled": True}
        self.services.write_text(json.dumps(services))
        return target

    def calls(self):
        path = self.fixture_home / "service-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def mutations(self):
        return [call for call in self.calls() if call[0] in
                ("enable", "disable", "start", "stop", "restart", "daemon-reload", "reset-failed")]

    def run_core(self, responses=(), *, interactive=True):
        if not interactive:
            result = subprocess.run(["bash", str(self.script), "tunnel"], env=self.env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=8, start_new_session=True)
            return result.returncode, result.stdout + result.stderr
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe("bash", ["bash", str(self.script), "tunnel"], self.env)
        transcript = b""
        pending = list(responses)
        offset = 0
        status = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.05)
                if ready:
                    try:
                        block = os.read(fd, 65536)
                    except OSError:
                        break
                    if not block:
                        break
                    transcript += block
                    text = transcript.decode(errors="replace")
                    if pending and pending[0][0] in text[offset:]:
                        prompt, answer = pending.pop(0)
                        offset = text.index(prompt, offset) + len(prompt)
                        os.write(fd, answer if isinstance(answer, bytes) else (answer + "\n").encode())
                if status is None:
                    exited, child_status = os.waitpid(pid, os.WNOHANG)
                    if exited:
                        status = os.waitstatus_to_exitcode(child_status)
            # Closing the PTY is not proof that waitpid can reap the child yet.
            while status is None and time.monotonic() < deadline:
                exited, child_status = os.waitpid(pid, os.WNOHANG)
                if exited:
                    status = os.waitstatus_to_exitcode(child_status)
                    break
                time.sleep(0.01)
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                self.fail("Unexpected prompt or hung installer: " + transcript.decode(errors="replace"))
        finally:
            os.close(fd)
        result = transcript.decode(errors="replace")
        self.assertFalse(pending, "Missing token prompt: " + result)
        return status, result

    def assert_node_preserved(self):
        for path, value in self.original_node.items():
            self.assertEqual(path.read_bytes(), value)
        for call in self.mutations():
            self.assertFalse(any(name in call for name in ("insta-box-node.service", "insta-box-quick.service")))

    def test_first_token_is_private_and_starts_named_service(self):
        code, output = self.run_core([(FIRST_PROMPT, NEW_TOKEN)])
        self.assertEqual(code, 0, output)
        target = self.tokens / "token"
        self.assertEqual(target.read_text(), NEW_TOKEN + "\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn(NEW_TOKEN, output + repr(self.calls()) + self.unit.read_text())
        self.assertTrue(json.loads(self.services.read_text())["insta-box-tunnel.service"]["active"])
        self.assertIn("http://127.0.0.1:23456", output)
        self.assert_node_preserved()

    def test_complete_service_install_command_is_safely_extracted(self):
        code, output = self.run_core([(FIRST_PROMPT, "sudo cloudflared service install " + NEW_TOKEN)])
        self.assertEqual(code, 0, output)
        self.assertEqual((self.tokens / "token").read_text().strip(), NEW_TOKEN)
        self.assertNotIn(NEW_TOKEN, output)
        self.assert_node_preserved()

    def test_invalid_inputs_retry_without_executing_shell_content(self):
        marker = self.base / "must-not-exist"
        invalid = ["ibk_wrong_provider", "eyJnot-json", "sudo cloudflared service install " +
                   NEW_TOKEN + "; touch " + str(marker)]
        responses = [(FIRST_PROMPT, value) for value in invalid] + [(FIRST_PROMPT, NEW_TOKEN)]
        code, output = self.run_core(responses)
        self.assertEqual(code, 0, output)
        self.assertFalse(marker.exists())
        self.assertGreaterEqual(output.count("Token 格式"), len(invalid))
        self.assertEqual((self.tokens / "token").read_text().strip(), NEW_TOKEN)
        self.assertNotIn(NEW_TOKEN, output)

    def test_empty_first_input_cancels_without_configuring_services(self):
        code, output = self.run_core([(FIRST_PROMPT, "")])
        self.assertEqual(code, 0, output)
        self.assertFalse((self.tokens / "token").exists())
        self.assertFalse(self.unit.exists())
        self.assertEqual(self.mutations(), [])
        self.assert_node_preserved()

    def test_saved_token_enter_reuses_without_restart_or_rewrite(self):
        target = self.saved()
        before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns, self.unit.read_bytes())
        code, output = self.run_core([(SAVED_PROMPT, "")])
        self.assertEqual(code, 0, output)
        self.assertEqual((target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns, self.unit.read_bytes()), before)
        self.assertFalse(any(call[0] == "restart" for call in self.mutations()))
        self.assertNotIn(OLD_TOKEN, output)
        self.assert_node_preserved()

    def test_saved_token_zero_and_interrupt_preserve_configuration(self):
        target = self.saved()
        before = (target.read_bytes(), target.stat().st_ino, self.unit.read_bytes())
        for response in ("0", b"\x03"):
            with self.subTest(response="cancel" if isinstance(response, str) else "interrupt"):
                code, output = self.run_core([(SAVED_PROMPT, response)])
                if response == "0":
                    self.assertEqual(code, 0, output)
                self.assertEqual((target.read_bytes(), target.stat().st_ino, self.unit.read_bytes()), before)
                self.assertEqual(self.mutations(), [])
                self.assertNotIn(OLD_TOKEN, output)

    def test_new_token_replaces_atomically_and_restarts_existing_service(self):
        target = self.saved()
        inode = target.stat().st_ino
        code, output = self.run_core([(SAVED_PROMPT, NEW_TOKEN)])
        self.assertEqual(code, 0, output)
        self.assertEqual(target.read_text().strip(), NEW_TOKEN)
        self.assertNotEqual(target.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertIn(["restart", "insta-box-tunnel.service"], self.mutations())
        self.assertEqual(list(self.tokens.iterdir()), [target])
        self.assert_node_preserved()

    def test_legacy_token_path_referenced_by_unit_is_used_even_if_token_also_exists(self):
        target = self.saved(filename="tunnel.token")
        unused = self.tokens / "token"
        unused.write_text("unrelated existing token file\n")
        unused.chmod(0o600)
        unit_before = self.unit.read_bytes()
        code, output = self.run_core([(SAVED_PROMPT, NEW_TOKEN)])
        self.assertEqual(code, 0, output)
        self.assertEqual(target.read_text().strip(), NEW_TOKEN)
        self.assertEqual(unused.read_text(), "unrelated existing token file\n")
        self.assertEqual(self.unit.read_bytes(), unit_before)
        self.assertIn(["restart", "insta-box-tunnel.service"], self.mutations())
        self.assert_node_preserved()

    def test_failed_replacement_restores_previous_token_and_running_service(self):
        target = self.saved()
        before = target.read_bytes(), self.unit.read_bytes()
        code, output = self.run_core([(SAVED_PROMPT, FAIL_TOKEN)])
        self.assertNotEqual(code, 0, output)
        self.assertEqual((target.read_bytes(), self.unit.read_bytes()), before)
        self.assertTrue(json.loads(self.services.read_text())["insta-box-tunnel.service"]["active"])
        self.assertNotIn(FAIL_TOKEN, output + repr(self.calls()))
        self.assertEqual(list(self.tokens.iterdir()), [target])
        self.assert_node_preserved()

    def test_failed_first_start_does_not_leave_failed_token_or_unit(self):
        code, output = self.run_core([(FIRST_PROMPT, FAIL_TOKEN)])
        self.assertNotEqual(code, 0, output)
        self.assertFalse((self.tokens / "token").exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(json.loads(self.services.read_text())["insta-box-tunnel.service"]["active"])
        self.assertNotIn(FAIL_TOKEN, output)
        self.assert_node_preserved()

    def test_noninteractive_missing_token_fails_and_existing_token_is_reused(self):
        code, output = self.run_core(interactive=False)
        self.assertNotEqual(code, 0, output)
        self.assertIn("终端", output)
        self.assertFalse((self.tokens / "token").exists())
        self.assertEqual(self.mutations(), [])
        target = self.saved()
        before = target.read_bytes(), target.stat().st_ino
        code, output = self.run_core(interactive=False)
        self.assertEqual(code, 0, output)
        self.assertEqual((target.read_bytes(), target.stat().st_ino), before)
        self.assertFalse(any(call[0] == "restart" for call in self.mutations()))
        self.assert_node_preserved()

    def test_foreign_unit_and_unrecognized_managed_token_path_are_untouched(self):
        target = self.saved(managed=False)
        foreign = self.unit.read_bytes()
        unknown = ("# Managed-by: InstaBox Tool\n[Service]\nExecStart=cloudflared tunnel run "
                   "--token-file %h/.config/another-app/token\n").encode()
        for value in (foreign, unknown):
            with self.subTest(managed=value == unknown):
                self.unit.write_bytes(value)
                before = target.read_bytes(), self.unit.read_bytes()
                code, output = self.run_core()
                self.assertNotEqual(code, 0, output)
                self.assertEqual((target.read_bytes(), self.unit.read_bytes()), before)
                self.assertEqual(self.mutations(), [])
                self.assert_node_preserved()


if __name__ == "__main__":
    unittest.main()
