# Copyright 2026 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Compiler command hooks for legacy spawn() and modern call() entry points."""

import contextlib
import importlib.util
import os
from pathlib import Path
import types
import unittest
from unittest import mock

import setuptools  # Ensure distutils is available on Python 3.12 and later.
from distutils import ccompiler

_SOURCE = Path(__file__).resolve().parents[3] / "grpcio" / "_spawn_patch.py"


class CompilerHookTest(unittest.TestCase):
    @contextlib.contextmanager
    def compiler(self, modern, execute, response=False, windows=True):
        methods = {"spawn": execute}
        if modern:
            methods["call"] = execute
        compiler_type = type("Compiler", (), methods)
        with mock.patch.object(ccompiler, "CCompiler", compiler_type):
            spec = importlib.util.spec_from_file_location("spawn_under_test", _SOURCE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.MAX_COMMAND_LENGTH = 1 if response else 100000
            module.monkeypatch_spawn()
            simulated_os = types.SimpleNamespace(
                name="nt" if windows else "posix", environ={}, path=os.path
            )
            with mock.patch.object(module, "os", simulated_os):
                with mock.patch.object(module.shutil, "which", return_value=None):
                    yield getattr(compiler_type(), "call" if modern else "spawn")

    def test_language_flags_and_response_files(self):
        for modern in (False, True):
            for cpp in (False, True):
                for response in (False, True):
                    with self.subTest(modern=modern, cpp=cpp, response=response):
                        seen = []

                        def execute(compiler, command, **kwargs):
                            row = {"command": command, "kwargs": kwargs}
                            if len(command) > 1 and command[1].startswith("@"):
                                row["path"] = Path(command[1][1:])
                                row["text"] = row["path"].read_text()
                            else:
                                row["text"] = " ".join(command)
                            seen.append(row)

                        command = [
                            "cl.exe", "/Tpunit.cc" if cpp else "/Tcunit.c",
                            "/std:c++17", "/std:c11", "/Zc:preprocessor",
                        ]
                        original = command.copy()
                        with self.compiler(modern, execute, response) as invoke:
                            invoke(command, env={"marker": "retained"})
                        self.assertEqual(command, original)
                        self.assertEqual(len(seen), 1)
                        self.assertEqual(seen[0]["kwargs"], {"env": {"marker": "retained"}})
                        self.assertEqual("/std:c++17" in seen[0]["text"], cpp)
                        self.assertEqual("/std:c11" in seen[0]["text"], not cpp)
                        self.assertIn("/Zc:preprocessor", seen[0]["text"])
                        self.assertEqual("path" in seen[0], response)
                        if response:
                            self.assertFalse(seen[0]["path"].exists())

    def test_failed_commands_propagate_and_clean_up(self):
        for modern in (False, True):
            for response in (False, True):
                with self.subTest(modern=modern, response=response):
                    commands = []

                    def execute(compiler, command, **kwargs):
                        commands.append(command)
                        raise RuntimeError("expected child failure")

                    with self.compiler(modern, execute, response) as invoke:
                        with self.assertRaisesRegex(RuntimeError, "expected child failure"):
                            invoke(["cl.exe", "/Tpunit.cc", "/std:c++17", "/std:c11"])
                    self.assertEqual(len(commands), 1)
                    self.assertEqual(commands[0][1].startswith("@"), response)
                    if response:
                        self.assertFalse(Path(commands[0][1][1:]).exists())

    def test_non_windows_command_is_unchanged(self):
        for modern in (False, True):
            with self.subTest(modern=modern):
                seen = []

                def execute(compiler, command, **kwargs):
                    seen.append(command)

                command = ["cc", "/Tcunit.c", "/std:c++17", "/std:c11"]
                with self.compiler(modern, execute, response=True, windows=False) as invoke:
                    invoke(command)
                self.assertEqual(seen, [command])


if __name__ == "__main__":
    unittest.main()