"""Tests for the pre-invocation safety checks."""

from __future__ import annotations

import os
import tempfile

from tooldb.assessment.safety import check_invocation_safety

from .conftest import make_tool


class TestSafetyChecks:
    def test_status_avoid_blocked(self) -> None:
        tool = make_tool(my_status="avoid")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is False
        assert "avoid" in (verdict.blocked_reason or "")

    def test_status_works_passes(self) -> None:
        tool = make_tool(my_status="works")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True

    def test_unrecognized_domain_warning(self) -> None:
        tool = make_tool(url="https://sketchy-site.example.com/tool")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True
        assert any("not from a recognized source" in w for w in verdict.warnings)

    def test_trusted_domain_no_warning(self) -> None:
        tool = make_tool(url="https://github.com/foo/bar")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True
        assert not verdict.warnings

    def test_dangerous_template_warning(self) -> None:
        tool = make_tool(invocation_template="curl $(cat /etc/passwd)")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True
        assert any("dangerous shell patterns" in w for w in verdict.warnings)

    def test_safe_template_no_warning(self) -> None:
        tool = make_tool(invocation_template="python -m tool {input}")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True
        assert not any("dangerous" in w for w in verdict.warnings)

    def test_backtick_template_warning(self) -> None:
        tool = make_tool(invocation_template="echo `whoami`")
        verdict = check_invocation_safety(tool)
        assert any("dangerous" in w for w in verdict.warnings)

    def test_pipe_template_warning(self) -> None:
        tool = make_tool(invocation_template="cat file || rm -rf /")
        verdict = check_invocation_safety(tool)
        assert any("dangerous" in w for w in verdict.warnings)

    def test_world_writable_wrapper_warning(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"def invoke(inputs): return {}")
            wrapper_path = f.name

        try:
            os.chmod(wrapper_path, 0o777)
            tool = make_tool(wrapper_path=wrapper_path)
            verdict = check_invocation_safety(tool)
            assert any("world-writable" in w for w in verdict.warnings)
        finally:
            os.unlink(wrapper_path)

    def test_nonexistent_wrapper_no_crash(self) -> None:
        tool = make_tool(wrapper_path="/nonexistent/wrapper.py")
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True  # non-existent wrappers don't block

    def test_multiple_warnings_accumulated(self) -> None:
        tool = make_tool(
            url="https://sketchy.example.com/tool",
            invocation_template="echo `id`",
        )
        verdict = check_invocation_safety(tool)
        assert verdict.safe is True
        assert len(verdict.warnings) >= 2
