"""
The AI layer's refusals, and the autostart registration.

Almost every assertion here is about something NOT happening. That is the shape
of this feature: the AI layer's value is that it cannot be handed raw data, and
the autostart's value is that it does not ask for Administrator.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config import AIConfig, Config
from app.database.models import Category, WorkItem
from app.integrations.ai_provider import (
    AIRefused,
    LocalModelProvider,
    NullProvider,
    OpenAIProvider,
    build_provider,
)
from app.intelligence.sanitizer import Sanitizer


def _sanitized() -> list[dict]:
    item = WorkItem(
        project="Platinum Fit",
        brand="Platinum Fit",
        category=Category.GRAPHIC_DESIGN,
        action="Designed and revised the campaign key visual",
        file_count=6,
        first_at=datetime(2026, 3, 2, 9, 0),
        last_at=datetime(2026, 3, 2, 11, 0),
    )
    return Sanitizer().sanitize_batch([item])


# ======================================================================
class TestAIIsOffAndStaysOff:
    def test_ai_is_disabled_by_default(self) -> None:
        cfg = Config()
        assert cfg.ai.enabled is False
        assert cfg.ai.provider == "none"
        assert isinstance(build_provider(cfg), NullProvider)

    def test_the_null_provider_refuses_rather_than_pretending(self) -> None:
        """
        It raises instead of returning "". A caller that got an empty string
        would write an empty section into a report and nobody would know why.
        """
        with pytest.raises(AIRefused):
            NullProvider().send(_sanitized(), "March 2026")

    def test_a_remote_provider_is_refused_while_local_only_is_on(self) -> None:
        """
        AND IT SAYS SO. Silently downgrading to NullProvider would leave
        somebody wondering for an afternoon why their API key does nothing;
        silently honouring the key would break the guarantee the rest of the
        program rests on.
        """
        cfg = Config()
        cfg.ai = AIConfig(enabled=True, provider="openai")
        assert cfg.privacy.local_only is True

        with pytest.raises(AIRefused) as exc:
            build_provider(cfg, api_key="sk-test")
        assert "local_only" in str(exc.value)

    def test_a_local_model_is_permitted_because_loopback_is_not_egress(self) -> None:
        cfg = Config()
        cfg.ai = AIConfig(enabled=True, provider="local")
        assert cfg.privacy.local_only is True

        provider = build_provider(cfg)
        assert isinstance(provider, LocalModelProvider)

    def test_a_remote_provider_works_once_local_only_is_turned_off(self) -> None:
        cfg = Config()
        cfg.ai = AIConfig(enabled=True, provider="openai")
        cfg.privacy.local_only = False

        assert isinstance(build_provider(cfg, api_key="sk-test"), OpenAIProvider)


# ======================================================================
class TestNothingUnsanitizedCanBeSent:
    def test_a_raw_work_item_is_refused(self) -> None:
        """
        A WorkItem carries evidence ids, exact timestamps and file types. It is
        not a payload, and send() will not take one.
        """
        item = WorkItem(project="Platinum Fit", action="Designed something")
        with pytest.raises((AIRefused, TypeError, AttributeError)):
            NullProvider().send([item.model_dump()], "March 2026")  # type: ignore[list-item]

    def test_an_extra_field_is_refused(self) -> None:
        """
        THE REGRESSION THIS GUARDS. A field added upstream — "filename",
        "path", "notes" — would otherwise ride along inside a dict that
        otherwise looks sanitized.
        """
        payload = _sanitized()
        payload[0]["filename"] = "hero-final-v3.psd"

        with pytest.raises(AIRefused) as exc:
            NullProvider().send(payload, "March 2026")
        assert "filename" in str(exc.value)

    def test_a_path_in_an_allowed_field_is_refused(self) -> None:
        payload = _sanitized()
        payload[0]["action"] = r"Designed D:\Work\Platinum\hero.psd"

        with pytest.raises(AIRefused):
            NullProvider().send(payload, "March 2026")

    def test_the_refusal_never_repeats_the_leaked_value(self) -> None:
        """
        An error message goes into a log file. Naming the path there would
        write the leak to disk in the one place somebody later greps.
        """
        payload = _sanitized()
        payload[0]["action"] = r"Designed D:\Work\Confidential\salary-review.psd"

        with pytest.raises(AIRefused) as exc:
            NullProvider().send(payload, "March 2026")

        message = str(exc.value)
        assert "salary-review" not in message
        assert "Confidential" not in message
        assert "action" in message  # the FIELD is named, the value is not

    def test_a_sanitized_payload_passes_the_gate(self) -> None:
        """The checks must not be so strict that nothing legitimate survives."""
        from app.integrations.ai_provider import _assert_sendable

        _assert_sendable(_sanitized())   # must not raise


# ======================================================================
class TestAutostart:
    @staticmethod
    def _code_only() -> str:
        """
        The module's CODE, with docstrings removed.

        The first version of these two tests grepped the raw file and failed
        against correct code: this module explains at length why it does not
        use `shell=True` and why elevation is refused, so the forbidden strings
        appear in the prose that forbids them. A source-scanning test has to
        read the source the way the interpreter does.
        """
        import ast
        from pathlib import Path

        from app.security import autostart

        tree = ast.parse(Path(autostart.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body[0].value.value = ""
        # unparse drops `#` comments entirely, so what returns is executable
        # code and nothing else.
        return ast.unparse(tree)

    def test_it_never_asks_for_elevation(self) -> None:
        """
        Nothing machine-wide, nothing elevated, no service, no scheduled task.

        The first implementation used `schtasks /SC ONLOGON /RL LIMITED`, which
        is the textbook answer — and which this machine refuses with "Access is
        denied", because the policy demands elevation even to register a task
        that runs as the current user with none. A feature that needs an
        administrator to switch on is a feature most people never switch on.
        """
        code = self._code_only()
        for forbidden in ("HKLM", "runas", "schtasks", "ShellExecute", "ONSTART"):
            assert forbidden.lower() not in code.lower(), forbidden

    def test_it_writes_inside_the_users_own_profile(self) -> None:
        """
        APPDATA, not a hard-coded C:\\Users path. A domain machine with folder
        redirection puts the profile somewhere else entirely, and a hard-coded
        path would write the file where Windows never looks.
        """
        code = self._code_only()
        assert "APPDATA" in code
        assert "C:\\Users" not in code

    def test_it_never_builds_a_shell_string(self) -> None:
        assert "shell=True" not in self._code_only()

    def test_the_entry_is_a_file_the_user_can_see_and_delete(self) -> None:
        """
        The whole reason for preferring the Startup folder to a registry key.

        A tracker that can only be disabled from inside itself is one you have
        to trust. One you can switch off with the Delete key is one you can
        check — and for this program that is worth more than robustness.
        """
        from app.security.autostart import ENTRY_NAME

        assert ENTRY_NAME.endswith(".cmd")

    def test_the_scan_can_actually_fail(self) -> None:
        """
        Guard the guard. If _code_only returned something empty or unrelated,
        the assertions above would pass regardless of what the module does.
        """
        code = self._code_only()
        assert "Startup" in code
        assert len(code) > 500

    def test_is_registered_is_safe_to_call_anywhere(self) -> None:
        """It answers False off Windows rather than raising."""
        from app.security.autostart import is_registered

        assert isinstance(is_registered(), bool)
