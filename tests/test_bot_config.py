"""Tests for bot.py configuration and startup."""

import logging
from unittest.mock import MagicMock, patch

import pytest

import bot as bot_module


class TestBotConfig:
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_phone_number_exits(self):
        with pytest.raises(SystemExit, match="PHONE_NUMBER"):
            bot_module.main()

    @patch.dict("os.environ", {"PHONE_NUMBER": "+15551234567"}, clear=True)
    def test_missing_signal_group_exits(self):
        with pytest.raises(SystemExit, match="SIGNAL_GROUP"):
            bot_module.main()

    @patch.dict("os.environ", {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1"}, clear=True)
    @patch("bot.SignalBot")
    def test_valid_env_constructs_bot(self, mock_bot_cls):
        mock_bot = MagicMock()
        mock_bot_cls.return_value = mock_bot
        bot_module.main()

        config = mock_bot_cls.call_args[0][0]
        assert config["signal_service"] == "localhost:8080"
        assert config["phone_number"] == "+15551234567"
        assert config["storage"] == {"type": "in-memory"}

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1", "SIGNAL_SERVICE": "custom:9090"},
        clear=True,
    )
    @patch("bot.SignalBot")
    def test_custom_signal_service(self, mock_bot_cls):
        mock_bot = MagicMock()
        mock_bot_cls.return_value = mock_bot
        bot_module.main()

        config = mock_bot_cls.call_args[0][0]
        assert config["signal_service"] == "custom:9090"

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1", "DEBUG": "false"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.logging.disable")
    def test_debug_false_disables_logging(self, mock_disable, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_disable.assert_called_with(logging.CRITICAL)

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1", "DEBUG": "true"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("signalbot.enable_console_logging")
    def test_debug_true_enables_logging(self, mock_enable, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_enable.assert_called_once_with(logging.INFO)

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1"},
        clear=True,
    )
    @patch("bot.SignalBot")
    def test_commands_registered_with_groups(self, mock_bot_cls):
        mock_bot = MagicMock()
        mock_bot_cls.return_value = mock_bot
        bot_module.main()

        register_calls = mock_bot.register.call_args_list
        assert len(register_calls) == 4
        for call in register_calls:
            assert call.kwargs.get("contacts") is False
            assert call.kwargs.get("groups") == ["grp1"]

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.logging.warning")
    def test_missing_both_defrost_vars_warns_all_disabled(self, mock_warning, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_warning.assert_called_once()
        msg = mock_warning.call_args[0][0]
        assert "DEFROST_DECRYPT_KEY" in msg
        assert "DEFROST_JSON_URL" in msg
        assert "all defrostmn.net lookups will be disabled" in msg

    @patch.dict(
        "os.environ",
        {
            "PHONE_NUMBER": "+15551234567",
            "SIGNAL_GROUP": "grp1",
            "DEFROST_JSON_URL": "https://example.com/plates.json",
        },
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.logging.warning")
    def test_only_json_url_warns_stopice_only(self, mock_warning, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_warning.assert_called_once()
        msg = mock_warning.call_args[0][0]
        assert "DEFROST_DECRYPT_KEY" in msg
        assert "only stopice snapshot" in msg

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1", "DEFROST_DECRYPT_KEY": "somekey"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.logging.warning")
    def test_only_decrypt_key_no_warning(self, mock_warning, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_warning.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "PHONE_NUMBER": "+15551234567",
            "SIGNAL_GROUP": "grp1",
            "DEFROST_DECRYPT_KEY": "somekey",
            "DEFROST_JSON_URL": "https://example.com/plates.json",
        },
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.logging.warning")
    def test_both_defrost_vars_set_no_warning(self, mock_warning, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        bot_module.main()
        mock_warning.assert_not_called()

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.PlateDetailCommand")
    def test_detail_cmd_linked_to_plate_cmd(self, mock_detail_cls, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        mock_detail = MagicMock()
        mock_detail_cls.return_value = mock_detail
        bot_module.main()
        mock_detail.set_plate_command.assert_called_once()

    @patch.dict(
        "os.environ",
        {"PHONE_NUMBER": "+15551234567", "SIGNAL_GROUP": "grp1"},
        clear=True,
    )
    @patch("bot.SignalBot")
    @patch("bot.VoicePlateCommand")
    def test_voice_cmd_linked_to_plate_cmd(self, mock_voice_cls, mock_bot_cls):
        mock_bot_cls.return_value = MagicMock()
        mock_voice = MagicMock()
        mock_voice_cls.return_value = mock_voice
        bot_module.main()
        mock_voice.set_plate_command.assert_called_once()
