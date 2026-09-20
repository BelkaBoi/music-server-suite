import unittest
from pathlib import Path
from unittest.mock import patch

import server_control as sc


class ServerControlTests(unittest.TestCase):
    def test_parse_http_code_accepts_only_success(self):
        self.assertTrue(sc.http_ok("200"))
        self.assertFalse(sc.http_ok("530"))
        self.assertFalse(sc.http_ok(""))

    def test_task_names_cover_all_automation(self):
        self.assertEqual(
            set(sc.AUTOMATION_TASKS),
            {"hourly_sync", "daily_ai_dj", "public_health", "navidrome_watchdog"},
        )

    def test_hidden_command_never_opens_console(self):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok"
            result = sc.run_hidden(["example.exe", "arg"])
        self.assertEqual(result.stdout, "ok")
        kwargs = run.call_args.kwargs
        self.assertNotEqual(kwargs.get("creationflags", 0) & sc.CREATE_NO_WINDOW, 0)

    def test_button_text_is_explicit_for_all_action_buttons(self):
        expected = {
            "Start", "Stop", "Open Local", "Restart", "Open Remote",
            "Sync Now", "Acquire Now", "Open Missing List", "Refresh",
            "Music Folder", "Sync Reports", "Server Folder", "Run AI DJ",
            "Open Sync Log", "Open Acquire Log",
        }
        self.assertEqual(sc.ACTION_BUTTON_LABELS, expected)

    def test_status_snapshot_has_required_sections(self):
        with patch.object(sc, "process_running", return_value=False), \
             patch.object(sc, "http_probe", return_value=(False, "000")), \
             patch.object(sc, "task_enabled", return_value=False), \
             patch.object(sc, "wsl_service_state", return_value="inactive"), \
             patch.object(sc, "acquisition_running", return_value=False), \
             patch.object(sc, "next_sync_run", return_value="n/a"), \
             patch.object(sc, "last_run_summary", return_value="no runs yet"), \
             patch.object(sc, "library_stats", return_value={"files": 0, "lrc": 0, "gib": 0}):
            status = sc.status_snapshot()
        self.assertEqual(
            set(status),
            {"navidrome", "tunnel", "spotify", "acquisition", "automation", "capabilities"},
        )
        self.assertIn("local", status["navidrome"])
        self.assertIn("public", status["tunnel"])
        self.assertIn("running", status["acquisition"])
        self.assertIn("library", status["acquisition"])

    def test_acquisition_running_requires_fresh_lock(self):
        from unittest.mock import MagicMock
        lock = MagicMock()
        lock.exists.return_value = False
        with patch.object(sc, "ACQ_LOCK", lock):
            self.assertFalse(sc.acquisition_running())

    def test_start_acquisition_spawns_venv_python_in_sync_dir(self):
        from unittest.mock import MagicMock
        python = MagicMock()
        python.exists.return_value = True
        python.__str__.return_value = str(sc.SYNC_VENV_PYTHON)
        with patch.object(sc, "acquisition_running", return_value=False), \
             patch.object(sc, "SYNC_VENV_PYTHON", python), \
             patch.object(sc, "spawn_hidden") as spawn:
            sc.start_acquisition()
        args = spawn.call_args.args[0]
        self.assertIn("-m", args)
        self.assertIn("ultimate_music_sync.auto_acquire", args)
        self.assertEqual(spawn.call_args.kwargs.get("cwd"), str(sc.SYNC))

    def test_start_acquisition_skips_when_already_running(self):
        with patch.object(sc, "acquisition_running", return_value=True), \
             patch.object(sc, "spawn_hidden") as spawn:
            sc.start_acquisition()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
