import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load(rel, alias):
    spec = importlib.util.spec_from_file_location(alias, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod  # dataclasses look the module up while the class is created
    spec.loader.exec_module(mod)
    return mod
