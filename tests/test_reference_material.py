import hashlib
import importlib
import random
from fractions import Fraction

from pm_mcp.domain.numbers import to_fraction
from pm_mcp.engine.pert import activity_variance, expected_time
from pm_mcp.reference import load_inventory, load_methodology, methodology_topic


def keys():
    inventory = load_inventory()
    images = {img["key"] for source in inventory["sources"] for img in source["images"]}
    cells = {f"{source['source_id']}:{c['sheet']}!{c['cell']}" for source in inventory["sources"] for c in source["cells"]}
    return images, cells


def test_inventory_matches_the_reference_files(exercise_docx, concepts_xlsx):
    hashes = {s["source_id"]: s["sha256"] for s in load_inventory()["sources"]}
    assert hashlib.sha256(exercise_docx.read_bytes()).hexdigest() == hashes["exercise_docx"]
    assert hashlib.sha256(concepts_xlsx.read_bytes()).hexdigest() == hashes["concepts_xlsx"]


def test_every_image_and_cell_is_classified():
    methodology = load_methodology()
    images, cells = keys()
    assert images == {item["key"] for item in methodology["items"]}
    assert cells == {cell["key"] for cell in methodology["cells"]}
    categories = set(methodology["categories"])
    assert all(entry["category"] in categories for entry in [*methodology["items"], *methodology["cells"]])


def test_rules_cite_existing_sources_and_engine_functions():
    images, cells = keys()
    for name, rule in load_methodology()["rules"].items():
        assert rule["sources"], name
        for source in rule["sources"]:
            assert source in images | cells, (name, source)
        if "engine" in rule:
            module, attr = rule["engine"].rsplit(".", 1)
            assert hasattr(importlib.import_module(module), attr), rule["engine"]


def test_rule_formulas_agree_with_engine():
    rules = load_methodology()["rules"]
    rng = random.Random(7)
    for _ in range(300):
        a = to_fraction(rng.randint(0, 40) / 2)
        m = a + Fraction(rng.randint(0, 20))
        b = m + Fraction(rng.randint(0, 20))
        env = {"a": a, "m": m, "b": b}
        assert eval(rules["pert_expected_time"]["formula"], {}, env) == expected_time(a, m, b)
        assert eval(rules["pert_activity_variance"]["formula"], {}, env) == activity_variance(a, b)


def test_topic_filter():
    crashing = methodology_topic("crashing")
    assert {"crash_slope", "crashing_procedure"} <= set(crashing["rules"]) and crashing["items"]
    assert set(methodology_topic(None)) >= {"rules", "items", "cells"}
