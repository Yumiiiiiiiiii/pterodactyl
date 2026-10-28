"""Prepare a minimal backend fix without touching the installed panel."""
import argparse
import difflib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--panel", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
relative = "app/Services/Servers/StartupModificationService.php"
before = (args.panel / relative).read_text("utf-8")
needle = """            $server = $server->forceFill([
                'egg_id' => $egg->id,
                'nest_id' => $egg->nest_id,
            ]);
"""
replacement = needle + """
            // A blank startup means the selected Egg's default. Laravel converts
            // the UI's empty string to null before this service receives it.
            if (!isset($data['startup']) || trim((string) $data['startup']) === '') {
                $data['startup'] = $egg->startup;
            }
"""
if before.count(needle) != 1:
    raise ValueError("Panel source differs; review before preparing a patch")
after = before.replace(needle, replacement)
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "StartupModificationService.original.php").write_text(before, encoding="utf-8", newline="\n")
(args.output / "StartupModificationService.fixed.php").write_text(after, encoding="utf-8", newline="\n")
patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
    fromfile="a/" + relative, tofile="b/" + relative))
(args.output / "startup-default.patch").write_text(patch, encoding="utf-8", newline="\n")
print("Prepared backend-only patch; installed panel unchanged")
