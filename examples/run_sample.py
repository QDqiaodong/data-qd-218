import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core_recovery import process

p = Path(__file__).parents[1] / "fixtures" / "input.json"
print(json.dumps(process(json.loads(p.read_text())), indent=2))
