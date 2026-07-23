"""Import bridge for the ticket directory whose name contains a hyphen."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_path = Path(__file__).with_name("beaaa-15510") / "judge_contract.py"
_spec = spec_from_file_location("beaaa15510_judge_contract", _path)
assert _spec and _spec.loader
contract = module_from_spec(_spec)
sys.modules[_spec.name] = contract
_spec.loader.exec_module(contract)
