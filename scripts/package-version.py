#!/usr/bin/env python3
"""Print the shared release version, rejecting inconsistent package metadata."""

import ast
from pathlib import Path
import re
import sys


source = Path(__file__).resolve().parents[1]
control = (source / "packaging/control").read_text()
match = re.search(r"^Version: ([0-9]+\.[0-9]+\.[0-9]+)$", control, re.MULTILINE)
if not match:
    sys.exit("packaging/control must contain a three-part release version (e.g. 2.0.0).")
version = match.group(1)
module = ast.parse((source / "uoo/__init__.py").read_text())
versions = [ast.literal_eval(node.value) for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
if versions != [version]:
    sys.exit("uoo.__version__ and packaging/control Version must match.")
print(version)
