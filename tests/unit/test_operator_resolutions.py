"""Unit tests for operator resolution persistence (feature 009, T047)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.api.settings_store import SettingsStore


class TestOperatorResolutionPersistence:
    """Tests for T047: Unit test for operator resolution persistence."""

    @patch("ado2gh.api.settings_store.SettingsStore.load")
    @patch("ado2gh.api.settings_store.SettingsStore.save")
    def test_set_operator_resolutions_persists_mappings(self, mock_save, mock_load):
        """Verify set_operator_resolutions persists mappings to SettingsStore."""
        from ado2gh.api.settings_models import UISettings
        mock_settings = UISettings()
        mock_settings.operator_resolutions = {}
        mock_load.return_value = mock_settings
        mock_save.return_value = None

        store = SettingsStore(db_path=":memory:")
        resolutions = {
            "secret_mapping__Project__SCName": "AZURE_CLIENT_ID",
            "secret_mapping__Project__SCName2": "AZURE_TENANT_ID",
        }
        store.set_operator_resolutions("profile-1", resolutions)

        mock_save.assert_called_once()
        # Verify the resolutions were included in the saved data
        call_args = mock_save.call_args[0][0]
        assert call_args.operator_resolutions["profile-1"] == resolutions

    @patch("ado2gh.api.settings_store.SettingsStore.load")
    def test_get_operator_resolutions_loads_mappings(self, mock_load):
        """Verify get_operator_resolutions loads mappings from SettingsStore."""
        from ado2gh.api.settings_models import UISettings
        mock_settings = UISettings()
        mock_settings.operator_resolutions = {
            "profile-1": {
                "secret_mapping__Project__SCName": "AZURE_CLIENT_ID",
                "secret_mapping__Project__SCName2": "AZURE_TENANT_ID",
            }
        }
        mock_load.return_value = mock_settings

        store = SettingsStore(db_path=":memory:")
        resolutions = store.get_operator_resolutions("profile-1")

        assert resolutions == {
            "secret_mapping__Project__SCName": "AZURE_CLIENT_ID",
            "secret_mapping__Project__SCName2": "AZURE_TENANT_ID",
        }

    @patch("ado2gh.api.settings_store.SettingsStore.load")
    def test_get_operator_resolutions_returns_empty_dict_when_none(self, mock_load):
        """Verify get_operator_resolutions returns empty dict when no resolutions exist."""
        from ado2gh.api.settings_models import UISettings
        mock_settings = UISettings()
        mock_settings.operator_resolutions = {}
        mock_load.return_value = mock_settings

        store = SettingsStore(db_path=":memory:")
        resolutions = store.get_operator_resolutions("profile-1")

        assert resolutions == {}

    @patch("ado2gh.api.settings_store.SettingsStore.load")
    @patch("ado2gh.api.settings_store.SettingsStore.save")
    def test_resolutions_per_profile_isolation(self, mock_save, mock_load):
        """Verify resolutions are isolated per profile."""
        from ado2gh.api.settings_models import UISettings
        mock_settings = UISettings()
        mock_settings.operator_resolutions = {}
        mock_load.return_value = mock_settings
        mock_save.return_value = None

        store = SettingsStore(db_path=":memory:")

        # Set resolutions for profile-1
        store.set_operator_resolutions("profile-1", {"sc1": "secret1"})

        # Set resolutions for profile-2
        store.set_operator_resolutions("profile-2", {"sc2": "secret2"})

        # Verify each profile has its own resolutions
        mock_settings.operator_resolutions = {
            "profile-1": {"sc1": "secret1"},
            "profile-2": {"sc2": "secret2"},
        }

        res1 = store.get_operator_resolutions("profile-1")
        res2 = store.get_operator_resolutions("profile-2")

        assert res1 == {"sc1": "secret1"}
        assert res2 == {"sc2": "secret2"}

    @patch("ado2gh.api.settings_store.SettingsStore.load")
    @patch("ado2gh.api.settings_store.SettingsStore.save")
    def test_set_operator_resolutions_overwrites_existing(self, mock_save, mock_load):
        """Verify set_operator_resolutions overwrites existing mappings."""
        from ado2gh.api.settings_models import UISettings
        mock_settings = UISettings()
        mock_settings.operator_resolutions = {
            "profile-1": {"old_sc": "old_secret"},
        }
        mock_load.return_value = mock_settings
        mock_save.return_value = None

        store = SettingsStore(db_path=":memory:")
        new_resolutions = {"new_sc": "new_secret"}
        store.set_operator_resolutions("profile-1", new_resolutions)

        mock_save.assert_called_once()
        call_args = mock_save.call_args[0][0]
        assert call_args.operator_resolutions["profile-1"] == {"old_sc": "old_secret", "new_sc": "new_secret"}
