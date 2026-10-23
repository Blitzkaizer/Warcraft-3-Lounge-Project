"""
Unit tests for GameLauncher and custom in-app AuthManager (Stage 3).
"""

from pathlib import Path
import tempfile
import unittest

from src.auth.auth_manager import AuthManager, UserSession
from src.launcher.game_launcher import GameLauncher

class TestLauncherAndAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_auth_registration_and_login_flow(self):
        auth = AuthManager(supabase_url="", supabase_key="", config_dir=self.config_path)

        # 1. Register with username and password
        success, msg = auth.register("Arthas99", "frostmourne123")
        self.assertTrue(success)
        self.assertFalse(auth.current_user.is_authenticated)

        # 2. Login with registered credentials
        success, msg = auth.login("Arthas99", "frostmourne123")
        self.assertTrue(success)
        self.assertEqual(auth.current_user.username, "Arthas99")
        self.assertTrue(auth.current_user.is_authenticated)

        # 3. Verify session persistence
        loaded_user = auth._load_saved_session()
        self.assertEqual(loaded_user.username, "Arthas99")
        self.assertTrue(loaded_user.is_authenticated)

        # 4. Test logout
        auth.logout()
        self.assertFalse(auth.current_user.is_authenticated)

        # 5. Test re-login
        success, msg = auth.login("Arthas99", "frostmourne123")
        self.assertTrue(success)
        self.assertEqual(auth.current_user.username, "Arthas99")

    def test_auth_validation_rules(self):
        auth = AuthManager(supabase_url="", supabase_key="", config_dir=self.config_path)
        # Too short username
        success, msg = auth.register("ab", "password123")
        self.assertFalse(success)

        # Too short password
        success, msg = auth.register("validuser", "123")
        self.assertFalse(success)

    def test_game_launcher_config_persistence(self):
        launcher = GameLauncher(config_dir=self.config_path)

        # Create a fake war3.exe in temp directory to test saving & loading
        dummy_exe = self.config_path / "war3.exe"
        dummy_exe.touch()

        saved = launcher.save_path(str(dummy_exe))
        self.assertTrue(saved)
        self.assertEqual(launcher.get_saved_path(), str(dummy_exe))

if __name__ == "__main__":
    unittest.main()
