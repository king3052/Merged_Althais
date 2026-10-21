"""The app's dental rules (static/v4/dental.js) and the server's (dental_rules.py) must agree: same findings, same words.
Runs the JavaScript copy under Node on the same claims. Skipped where Node isn't installed."""
import json
import os
import random
import shutil
import subprocess

import pytest

import dental_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node isn't installed")

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { window: {} }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const X = ctx.window.DX, input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({ checks: input.claims.map(c => X.check(c)), history: input.history.map(h => X.historyFlags(h)),
  parsed: input.parse.map(t => X.parseProcs(t)) }));
"""


def run_js(claims, history, parse=()):
    out = subprocess.run([NODE, "-e", RUNNER, os.path.join(ROOT, "static", "v4", "dental.js")], input=json.dumps({"claims": claims, "history": history, "parse": list(parse)}),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


FIXED = [
    {"cpt": ["D0120", "D1110", "D0274"], "lines": [{"code": "D0120"}, {"code": "D1110"}, {"code": "D0274"}], "tooth_column": True, "dob": "1986-04-12", "dos": "2026-10-01"},
    {"cpt": ["D0140", "D2391"], "lines": [{"code": "D0140"}, {"code": "D2391", "tooth": "19", "surface": "MOD"}]},
    {"cpt": ["D4341", "D1110"], "lines": [{"code": "D4341"}, {"code": "D1110"}]},
    {"lines": [{"code": "D2740", "tooth": "30"}, {"code": "D2391", "tooth": "30", "surface": "O"}]},
    {"lines": [{"code": "D7140", "tooth": "17"}, {"code": "D2391", "tooth": "17", "surface": "O"}, {"code": "D2140", "tooth": "33", "surface": "O"}]},
    {"lines": [{"code": "D0150"}, {"code": "D0120"}, {"code": "D2331", "tooth": "8", "surface": "MO"}]},
    {"cpt": ["D2392", "D3330"], "tooth_column": False},
    {"lines": [{"code": "D3330", "tooth": "8"}, {"code": "D3320", "tooth": "K"}, {"code": "D3310", "tooth": "E"}, {"code": "D2392", "tooth": "#3", "surface": "m o x"}]},
    {"lines": [{"code": "D1206"}, {"code": "D1351", "tooth": "3"}, {"code": "D1120"}], "dob": "1990-01-01", "dos": "2026-10-01"},
    {"lines": [{"code": "D1110"}], "dob": "2018-05-05", "dos": "2026-10-01"},
    {"lines": [{"code": "D0210"}, {"code": "D0272"}, {"code": "D4910"}, {"code": "D1110"}, {"code": "D5110", "quad": "UA"}, {"code": "D5120"}, {"code": "D6010", "tooth": "19"}, {"code": "D8080"}, {"code": "D9999"}]},
    {"lines": [{"code": "D2392", "tooth": "14", "surface": "MO"}, {"code": "D2392", "tooth": "14", "surface": "MO"}, {"code": "D2394", "tooth": "15", "surface": "MODBL"}, {"code": "D2161", "tooth": "2", "surface": "MODB"}]},
    {"cpt": ["D123", "99213"], "lines": [{"code": "DX100"}]},
]


def _random_claims(n=250, seed=7):
    rnd = random.Random(seed)
    codes = [f[0] for f in [("D0120",), ("D0150",), ("D0140",), ("D0210",), ("D0274",), ("D0330",), ("D1110",), ("D1120",), ("D1206",), ("D1351",), ("D2140",), ("D2150",),
             ("D2330",), ("D2331",), ("D2332",), ("D2335",), ("D2391",), ("D2392",), ("D2393",), ("D2394",), ("D2740",), ("D2950",), ("D3310",), ("D3320",), ("D3330",),
             ("D4341",), ("D4342",), ("D4910",), ("D5110",), ("D6010",), ("D7140",), ("D7210",), ("D8080",), ("D9110",)]]
    teeth = [""] * 4 + [str(i) for i in range(1, 33)] + list("ABCKLT") + ["33", "0", "#14"]
    surfs = ["", "", "M", "O", "MO", "MOD", "DO", "MODB", "I", "MI", "X", "mo"]
    quads = ["", "", "", "UR", "LL", "UA", "U"]
    out = []
    for _ in range(n):
        lines = [{"code": rnd.choice(codes), "tooth": rnd.choice(teeth), "surface": rnd.choice(surfs), "quad": rnd.choice(quads)} for _ in range(rnd.randint(1, 5))]
        c = {"lines": lines, "tooth_column": rnd.random() > .1}
        if rnd.random() > .5:
            c["dob"], c["dos"] = f"{rnd.randint(1950, 2022)}-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}", "2026-10-01"
        out.append(c)
    return out


def _histories(seed=11):
    rnd = random.Random(seed)
    out = []
    for _ in range(40):
        h = []
        for _ in range(rnd.randint(1, 7)):
            y, m = rnd.choice([2022, 2024, 2025, 2026]), rnd.randint(1, 12)
            h.append({"dos": f"{y}-{m:02d}-{rnd.randint(10, 28)}", "cpt": rnd.sample(["D0120", "D1110", "D0274", "D0210", "D1206", "D4910", "D2740", "D4341"], 2),
                      "lines": [{"code": "D2740", "tooth": rnd.choice(["3", "14"])}, {"code": "D4341", "quad": rnd.choice(["UR", "LL"])}]})
        out.append(h)
    return out


def test_js_and_python_rules_agree():
    claims = FIXED + _random_claims()
    hist = _histories()
    js = run_js(claims, hist)
    for i, c in enumerate(claims):
        assert js["checks"][i] == dental_rules.check(c), f"claim {i}: {c}"
    for i, h in enumerate(hist):
        py = {str(k): v for k, v in dental_rules.history_flags([dict(x, mrn="p") for x in h]).items()}
        assert js["history"][i] == py, f"history {i}"


def test_procedure_text_parses_into_lines():
    js = run_js([], [], ["D2392 #14 MO; D4341 UR", "D0120, D1110\nD2331 8 MI", "d3330 #k"])["parsed"]
    assert js[0] == [{"code": "D2392", "tooth": "14", "surface": "MO", "quad": "", "units": 1}, {"code": "D4341", "tooth": "", "surface": "", "quad": "UR", "units": 1}]
    assert [l["code"] for l in js[1]] == ["D0120", "D1110", "D2331"] and js[1][2]["tooth"] == "8" and js[1][2]["surface"] == "MI"
    assert js[2][0]["code"] == "D3330" and js[2][0]["tooth"] == "K"
