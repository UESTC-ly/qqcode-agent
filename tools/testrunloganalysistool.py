from tools.base import BaseTool
import requests
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class TestRunLogAnalysisTool(BaseTool):
    name = "testrunloganalysistool"
    description = '''
    Reusable development tool for running tests and analyzing logs.

    Accepts a local project path or a log file path. If a project path is provided,
    it detects common Python and Node.js test commands such as:
    - pytest
    - uv run pytest
    - npm test

    It captures stdout and stderr, determines test status, and analyzes logs to
    extract failing tests, stack traces, error highlights, and a concise summary
    of important issues.

    The tool is designed to be safe and cross-platform. It does not invoke shell
    pipelines and uses argument lists for subprocess execution.
    '''
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Local project directory path or log file path."
            },
            "log_path": {
                "type": "string",
                "description": "Optional explicit log file path. If provided, the log file will be analyzed."
            },
            "command": {
                "type": "string",
                "description": "Optional explicit test command to run, e.g. 'pytest', 'uv run pytest', or 'npm test'."
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout for the test command in seconds.",
                "default": 900
            },
            "max_error_lines": {
                "type": "integer",
                "description": "Maximum number of error highlight lines to return.",
                "default": 40
            }
        },
        "required": ["path"]
    }

    def execute(self, **kwargs) -> str:
        path = kwargs.get("path")
        log_path = kwargs.get("log_path")
        command = kwargs.get("command")
        timeout_seconds = int(kwargs.get("timeout_seconds", 900))
        max_error_lines = int(kwargs.get("max_error_lines", 40))

        if not path:
            return json.dumps({"success": False, "error": "Missing required parameter: path"}, indent=2)

        p = Path(path).expanduser().resolve()
        if not p.exists():
            return json.dumps({"success": False, "error": f"Path does not exist: {str(p)}"}, indent=2)

        if log_path:
            lp = Path(log_path).expanduser().resolve()
            if not lp.exists():
                return json.dumps({"success": False, "error": f"Log path does not exist: {str(lp)}"}, indent=2)
            content = self._read_text_file(lp)
            analysis = self._analyze_logs(content, source=str(lp), max_error_lines=max_error_lines)
            return json.dumps({"success": True, "mode": "log_analysis", "path": str(lp), "analysis": analysis}, indent=2)

        if p.is_file():
            content = self._read_text_file(p)
            analysis = self._analyze_logs(content, source=str(p), max_error_lines=max_error_lines)
            return json.dumps({"success": True, "mode": "log_analysis", "path": str(p), "analysis": analysis}, indent=2)

        project_dir = p
        selected_command = self._select_command(project_dir, command)
        if not selected_command:
            return json.dumps(
                {
                    "success": False,
                    "error": "Could not determine a suitable test command for the project.",
                    "project_path": str(project_dir),
                    "suggestions": ["Provide an explicit command using the 'command' parameter."]
                },
                indent=2
            )

        run_result = self._run_command(selected_command, cwd=project_dir, timeout_seconds=timeout_seconds)
        analysis = self._analyze_logs(
            "\n".join(filter(None, [run_result.get("stdout", ""), run_result.get("stderr", "")])),
            source=f"command: {selected_command}",
            max_error_lines=max_error_lines
        )

        status = "passed" if run_result.get("returncode") == 0 else "failed"
        if analysis.get("detected_failure") and status == "passed":
            status = "failed_with_errors"

        return json.dumps(
            {
                "success": True,
                "mode": "test_run",
                "project_path": str(project_dir),
                "command": selected_command,
                "status": status,
                "returncode": run_result.get("returncode"),
                "timed_out": run_result.get("timed_out"),
                "stdout": run_result.get("stdout", ""),
                "stderr": run_result.get("stderr", ""),
                "analysis": analysis
            },
            indent=2
        )

    def _select_command(self, project_dir: Path, explicit_command: Optional[str]) -> Optional[List[str]]:
        if explicit_command:
            return self._split_command(explicit_command)

        pyproject = project_dir / "pyproject.toml"
        pytest_ini = project_dir / "pytest.ini"
        tox_ini = project_dir / "tox.ini"
        setup_cfg = project_dir / "setup.cfg"
        package_json = project_dir / "package.json"
        requirements_txt = project_dir / "requirements.txt"

        if pyproject.exists() or pytest_ini.exists() or tox_ini.exists() or setup_cfg.exists() or requirements_txt.exists():
            if self._command_exists("uv"):
                return ["uv", "run", "pytest"]
            return ["pytest"]

        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
                if isinstance(scripts, dict) and "test" in scripts:
                    return ["npm", "test"]
            except Exception:
                pass

        if self._command_exists("pytest"):
            return ["pytest"]
        if self._command_exists("uv"):
            return ["uv", "run", "pytest"]
        if self._command_exists("npm"):
            return ["npm", "test"]

        return None

    def _split_command(self, command: str) -> List[str]:
        try:
            return shlex.split(command, posix=os.name != "nt")
        except Exception:
            return command.split()

    def _command_exists(self, cmd: str) -> bool:
        from shutil import which
        return which(cmd) is not None

    def _run_command(self, cmd: List[str], cwd: Path, timeout_seconds: int) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False
            )
            return {
                "returncode": proc.returncode,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "timed_out": False
            }
        except subprocess.TimeoutExpired as e:
            return {
                "returncode": None,
                "stdout": e.stdout or "",
                "stderr": e.stderr or f"Command timed out after {timeout_seconds} seconds.",
                "timed_out": True
            }
        except FileNotFoundError as e:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": str(e),
                "timed_out": False
            }
        except Exception as e:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": f"Failed to run command: {e}",
                "timed_out": False
            }

    def _read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            with open(path, "r", encoding=sys.getdefaultencoding(), errors="replace") as f:
                return f.read()

    def _analyze_logs(self, text: str, source: str, max_error_lines: int = 40) -> Dict[str, Any]:
        lines = text.splitlines()
        error_patterns = [
            r"\bERROR\b",
            r"\bFAILED\b",
            r"\bFAIL\b",
            r"Traceback \(most recent call last\):",
            r"AssertionError",
            r"TypeError",
            r"ValueError",
            r"ImportError",
            r"ModuleNotFoundError",
            r"SyntaxError",
            r"ReferenceError",
            r"UnhandledPromiseRejection",
            r"Segmentation fault",
            r"exit code\s+\d+"
        ]

        highlights: List[str] = []
        stack_traces: List[str] = []
        failing_tests: List[str] = []
        detected_failure = False

        for i, line in enumerate(lines):
            if any(re.search(p, line, re.IGNORECASE) for p in error_patterns):
                detected_failure = True
                highlights.append(line.strip())

            if "FAILED" in line or "FAIL" in line:
                m = re.search(r"^(.+?)\s+(FAILED|FAIL)\b", line.strip())
                if m:
                    failing_tests.append(m.group(1).strip())
                else:
                    failing_tests.append(line.strip())

            if "Traceback (most recent call last):" in line:
                block = [line.rstrip()]
                for j in range(i + 1, min(i + 40, len(lines))):
                    block.append(lines[j].rstrip())
                    if lines[j].strip() == "" and len(block) > 3:
                        break
                stack_traces.append("\n".join(block))

        highlights = self._dedupe_keep_order(highlights)[:max_error_lines]
        failing_tests = self._dedupe_keep_order(failing_tests)

        summary = self._build_summary(lines, detected_failure, failing_tests, highlights)

        return {
            "source": source,
            "line_count": len(lines),
            "detected_failure": detected_failure,
            "failing_tests": failing_tests,
            "error_highlights": highlights,
            "stack_traces": stack_traces[:10],
            "summary": summary
        }

    def _build_summary(self, lines: List[str], detected_failure: bool, failing_tests: List[str], highlights: List[str]) -> str:
        if not lines:
            return "No log content found."

        if not detected_failure:
            return "No obvious failure detected in the provided logs."

        parts = []
        if failing_tests:
            parts.append(f"Failing tests: {', '.join(failing_tests[:5])}")
        if highlights:
            parts.append(f"Key errors: {', '.join(highlights[:3])}")
        if not parts:
            parts.append("Failure detected, but no concise error highlights were extracted.")
        return " | ".join(parts)

    def _dedupe_keep_order(self, items: List[str]) -> List[str]:
        seen = set()
        out = []
        for item in items:
            key = item.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out