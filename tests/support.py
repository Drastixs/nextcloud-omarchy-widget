"""Shared test setup: puts helper/ on the path and locates fixtures."""
import json
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, os.path.join(ROOT, "helper"))


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def fixture_json(name):
    return json.loads(fixture(name))


def fixture_jsonl(name):
    return [json.loads(line) for line in fixture(name).splitlines() if line.strip()]
