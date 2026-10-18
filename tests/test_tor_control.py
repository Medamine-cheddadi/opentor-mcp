"""Tor control regression checks without a running Tor daemon."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from stem import Signal

from tor_mcp.browser import TorBrowser


class TorControlTests(unittest.TestCase):
    def test_newnym_uses_integer_port_and_authenticates_before_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = TorBrowser(
                tor_control_port=9151,
                tor_control_password="test-password",
                archives_dir=Path(directory),
            )
            controller = MagicMock()
            controller.get_newnym_wait.return_value = 2

            def connect(*, port):
                # Stem passes this value directly to socket.connect(), which
                # rejects a string even though Stem's port validator accepts it.
                if type(port) is not int:
                    raise TypeError("port must be an integer")
                self.assertEqual(port, 9151)
                context = MagicMock()
                context.__enter__.return_value = controller
                return context

            with (
                patch("stem.control.Controller.from_port", side_effect=connect),
                patch("time.sleep") as sleep,
            ):
                browser._signal_newnym()

            controller.authenticate.assert_called_once_with(password="test-password")
            sleep.assert_called_once_with(2)
            controller.signal.assert_called_once_with(Signal.NEWNYM)
