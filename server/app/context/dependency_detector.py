"""
Dependency Detector: AST-based import extraction for action code.

Parses Python source code to detect imports and maps them to PyPI packages.
Handles the common case where import name != package name (e.g., cv2 -> opencv-python).
"""

import ast
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

# Standard library modules (Python 3.10+)
# For older Python, we maintain a static list of common stdlib modules
try:
    STDLIB_MODULES = sys.stdlib_module_names
except AttributeError:
    # Fallback for Python < 3.10
    STDLIB_MODULES = frozenset(
        {
            "abc",
            "aifc",
            "argparse",
            "array",
            "ast",
            "asynchat",
            "asyncio",
            "asyncore",
            "atexit",
            "audioop",
            "base64",
            "bdb",
            "binascii",
            "binhex",
            "bisect",
            "builtins",
            "bz2",
            "calendar",
            "cgi",
            "cgitb",
            "chunk",
            "cmath",
            "cmd",
            "code",
            "codecs",
            "codeop",
            "collections",
            "colorsys",
            "compileall",
            "concurrent",
            "configparser",
            "contextlib",
            "contextvars",
            "copy",
            "copyreg",
            "cProfile",
            "crypt",
            "csv",
            "ctypes",
            "curses",
            "dataclasses",
            "datetime",
            "dbm",
            "decimal",
            "difflib",
            "dis",
            "distutils",
            "doctest",
            "email",
            "encodings",
            "enum",
            "errno",
            "faulthandler",
            "fcntl",
            "filecmp",
            "fileinput",
            "fnmatch",
            "fractions",
            "ftplib",
            "functools",
            "gc",
            "getopt",
            "getpass",
            "gettext",
            "glob",
            "graphlib",
            "grp",
            "gzip",
            "hashlib",
            "heapq",
            "hmac",
            "html",
            "http",
            "idlelib",
            "imaplib",
            "imghdr",
            "imp",
            "importlib",
            "inspect",
            "io",
            "ipaddress",
            "itertools",
            "json",
            "keyword",
            "lib2to3",
            "linecache",
            "locale",
            "logging",
            "lzma",
            "mailbox",
            "mailcap",
            "marshal",
            "math",
            "mimetypes",
            "mmap",
            "modulefinder",
            "multiprocessing",
            "netrc",
            "nis",
            "nntplib",
            "numbers",
            "operator",
            "optparse",
            "os",
            "ossaudiodev",
            "pathlib",
            "pdb",
            "pickle",
            "pickletools",
            "pipes",
            "pkgutil",
            "platform",
            "plistlib",
            "poplib",
            "posix",
            "posixpath",
            "pprint",
            "profile",
            "pstats",
            "pty",
            "pwd",
            "py_compile",
            "pyclbr",
            "pydoc",
            "queue",
            "quopri",
            "random",
            "re",
            "readline",
            "reprlib",
            "resource",
            "rlcompleter",
            "runpy",
            "sched",
            "secrets",
            "select",
            "selectors",
            "shelve",
            "shlex",
            "shutil",
            "signal",
            "site",
            "smtpd",
            "smtplib",
            "sndhdr",
            "socket",
            "socketserver",
            "spwd",
            "sqlite3",
            "ssl",
            "stat",
            "statistics",
            "string",
            "stringprep",
            "struct",
            "subprocess",
            "sunau",
            "symtable",
            "sys",
            "sysconfig",
            "syslog",
            "tabnanny",
            "tarfile",
            "telnetlib",
            "tempfile",
            "termios",
            "test",
            "textwrap",
            "threading",
            "time",
            "timeit",
            "tkinter",
            "token",
            "tokenize",
            "trace",
            "traceback",
            "tracemalloc",
            "tty",
            "turtle",
            "turtledemo",
            "types",
            "typing",
            "unicodedata",
            "unittest",
            "urllib",
            "uu",
            "uuid",
            "venv",
            "warnings",
            "wave",
            "weakref",
            "webbrowser",
            "winreg",
            "winsound",
            "wsgiref",
            "xdrlib",
            "xml",
            "xmlrpc",
            "zipapp",
            "zipfile",
            "zipimport",
            "zlib",
            # Also include common submodules that might be imported directly
            "collections.abc",
            "os.path",
            "urllib.parse",
            "urllib.request",
            "concurrent.futures",
            "asyncio.tasks",
        }
    )

# Mapping: import name -> PyPI package name
# Only includes cases where they differ
IMPORT_TO_PACKAGE: Dict[str, str] = {
    # Computer vision / ML
    "cv2": "opencv-python",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "tf": "tensorflow",
    "torch": "pytorch",
    "torchvision": "torchvision",
    # Data science
    "np": "numpy",
    "pd": "pandas",
    "plt": "matplotlib",
    "sns": "seaborn",
    "scipy": "scipy",
    # Web / HTTP
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "flask": "flask",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "requests": "requests",
    "urllib3": "urllib3",
    # Database
    "psycopg2": "psycopg2-binary",
    "pymongo": "pymongo",
    "redis": "redis",
    "sqlalchemy": "sqlalchemy",
    # Utils
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "dateutil": "python-dateutil",
    "tz": "pytz",
    "pytz": "pytz",
    "slugify": "python-slugify",
    "faker": "faker",
    # Async
    "trio": "trio",
    "anyio": "anyio",
    # CLI / formatting
    "rich": "rich",
    "click": "click",
    "typer": "typer",
    "tabulate": "tabulate",
    # Testing (usually not needed in actions but included)
    "pytest": "pytest",
    # Parsing / serialization
    "msgpack": "msgpack",
    "orjson": "orjson",
    "ujson": "ujson",
    "toml": "toml",
    "tomli": "tomli",
    # Crypto
    "cryptography": "cryptography",
    "nacl": "pynacl",
    "bcrypt": "bcrypt",
    # Cloud SDKs
    "boto3": "boto3",
    "botocore": "botocore",
    "google.cloud": "google-cloud",
    "azure": "azure",
    # API clients
    "openai": "openai",
    "anthropic": "anthropic",
    "stripe": "stripe",
    "twilio": "twilio",
}

# Packages that are blocked for security or resource reasons
BLOCKED_PACKAGES: Set[str] = {
    # System-level access
    "subprocess32",
    "os-sys",
    "pexpect",
    "pty",
    "paramiko",  # SSH - could be used to escape sandbox
    "fabric",
    # Heavy ML packages (too slow to install in sandbox)
    # Users can request these be pre-installed in custom images
    "tensorflow",
    "tensorflow-gpu",
    "torch",
    "pytorch",
    "jax",
    "jaxlib",
    # Potentially dangerous
    "pickle5",  # Arbitrary code execution via pickle
    "dill",  # Extended pickle
    "cloudpickle",
}

# Packages that generate warnings but are allowed
WARNING_PACKAGES: Dict[str, str] = {
    "opencv-python": "Large package (~50MB), may slow sandbox startup",
    "pandas": "Large package, consider if you really need dataframes",
    "numpy": "Usually fine, but large arrays may hit memory limits",
    "scipy": "Large package (~100MB), may slow sandbox startup",
    "pillow": "Usually fine for image processing",
    "matplotlib": "Large package, consider 'pillow' for simple image tasks",
    "seaborn": "Pulls in matplotlib + pandas, quite heavy",
}


@dataclass
class DependencyAnalysis:
    """Result of analyzing action code for dependencies."""

    # Raw imports found in code
    imports: List[str] = field(default_factory=list)

    # Mapped to PyPI package names
    packages: List[str] = field(default_factory=list)

    # Packages that are blocked
    blocked: List[Tuple[str, str]] = field(default_factory=list)  # (package, reason)

    # Packages with warnings
    warnings: List[Tuple[str, str]] = field(default_factory=list)  # (package, warning)

    # Imports that couldn't be mapped (might be local modules in user code)
    unknown: List[str] = field(default_factory=list)

    # Any syntax errors in the code
    syntax_error: str | None = None


def extract_imports(source_code: str) -> Tuple[List[str], str | None]:
    """
    Extract all import names from Python source code using AST.

    Returns:
        Tuple of (list of import names, optional syntax error message)
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return [], f"Syntax error at line {e.lineno}: {e.msg}"

    imports: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # import foo, bar
            for alias in node.names:
                # Get the top-level module name
                module_name = alias.name.split(".")[0]
                imports.add(module_name)

        elif isinstance(node, ast.ImportFrom):
            # from foo import bar
            if node.module:
                # Get the top-level module name
                module_name = node.module.split(".")[0]
                imports.add(module_name)

    return sorted(imports), None


def is_stdlib_module(module_name: str) -> bool:
    """Check if a module is part of the Python standard library."""
    # Check direct match
    if module_name in STDLIB_MODULES:
        return True

    # Check if it's a submodule of a stdlib package
    top_level = module_name.split(".")[0]
    return top_level in STDLIB_MODULES


def map_import_to_package(import_name: str) -> str | None:
    """
    Map an import name to its PyPI package name.

    Returns None if it's a stdlib module or unknown.
    """
    # Skip stdlib
    if is_stdlib_module(import_name):
        return None

    # Check explicit mapping
    if import_name in IMPORT_TO_PACKAGE:
        return IMPORT_TO_PACKAGE[import_name]

    # Most packages have the same import and package name
    return import_name


def analyze_dependencies(
    source_code: str,
    additional_blocked: Set[str] | None = None,
) -> DependencyAnalysis:
    """
    Analyze Python source code and extract dependencies.

    Args:
        source_code: The Python code to analyze
        additional_blocked: Extra packages to block (workspace-specific)

    Returns:
        DependencyAnalysis with all findings
    """
    result = DependencyAnalysis()

    # Extract imports
    imports, syntax_error = extract_imports(source_code)
    if syntax_error:
        result.syntax_error = syntax_error
        return result

    result.imports = imports

    # Build full blocklist (pre-compute lowercase for efficient lookup)
    blocklist_lower = {p.lower() for p in BLOCKED_PACKAGES}
    if additional_blocked:
        blocklist_lower.update(p.lower() for p in additional_blocked)

    # Map imports to packages
    seen_packages: Set[str] = set()

    for import_name in imports:
        package = map_import_to_package(import_name)

        if package is None:
            # stdlib, skip
            continue

        # Avoid duplicates
        if package in seen_packages:
            continue
        seen_packages.add(package)

        # Check if blocked
        if package.lower() in blocklist_lower:
            result.blocked.append((package, f"Package '{package}' is not allowed in sandboxes"))
            continue

        # Check for warnings
        if package in WARNING_PACKAGES:
            result.warnings.append((package, WARNING_PACKAGES[package]))

        result.packages.append(package)

    return result


def get_blocked_packages() -> List[Dict[str, str]]:
    """Return list of blocked packages with reasons."""
    return [
        {"package": pkg, "reason": "Security or resource constraints"}
        for pkg in sorted(BLOCKED_PACKAGES)
    ]
