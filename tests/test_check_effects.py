"""check_effects не должна требовать @fx: эффект берётся из контракта,
аннотации или выводится из имени вызова (как в proof.prove_safety).

Регрессия на исходный дефект: предупреждение о денежном шаге без
компенсации зависело от того, что модель сама напишет `@fx money`, —
а ловить проверка должна ровно пропуски модели.
"""
from picklang.check import check_effects

CHARGE = "> t(x)\np = bank.charge(x, 1)\nout p\n"
CHARGE_COMPENSATED = "> t(x)\np = bank.charge(x, 1) ! bank.refund(p.tx)\nout p\n"


def test_inferred_money_from_call_name():
    diags = check_effects(CHARGE)
    assert [d["code"] for d in diags] == ["EFFECT_NO_COMPENSATION"]
    assert "выведен из имени bank.charge" in diags[0]["message"]


def test_inferred_money_with_compensation_is_clean():
    assert check_effects(CHARGE_COMPENSATED) == []


def test_annotation_still_works_without_contracts():
    assert check_effects("> t(x)\np = api.reserve(x, 1) @fx money\nout p\n")
    assert not check_effects("> t(x)\np = api.reserve(x, 1) ! api.release(p.id) @fx money\nout p\n")


def test_explicit_read_annotation_suppresses_inference():
    assert check_effects("> t(x)\np = bank.charge(x, 1) @fx read\nout p\n") == []


def test_contract_effect_catches_nonstandard_name():
    diags = check_effects("> t(x)\np = api.reserve(x, 1)\nout p\n",
                           {"api.reserve": {"effect": "money"}})
    assert [d["code"] for d in diags] == ["EFFECT_NO_COMPENSATION"]
    assert "контракта api.reserve" in diags[0]["message"]


def test_contract_effect_overrides_inference():
    assert check_effects(CHARGE, {"bank.charge": {"effect": "read"}}) == []


def test_contract_without_effect_field_falls_through_to_inference():
    diags = check_effects(CHARGE, {"bank.charge": {"args": ["x", "amt"], "returns": ["ok", "tx"]}})
    assert [d["code"] for d in diags] == ["EFFECT_NO_COMPENSATION"]


def test_irreversible_inferred_for_delete():
    diags = check_effects("> t(x)\ndb.delete(x)\nout x\n")
    assert [d["code"] for d in diags] == ["EFFECT_NO_COMPENSATION"]
    assert "irreversible" in diags[0]["message"]


def test_send_and_refund_are_not_inferred():
    # send покрыт taint/prove_safety, refund — сама компенсация.
    assert check_effects('> t(x)\nm = mail.send(x, "hi")\nout m\n') == []
    assert check_effects("> t(x)\nbank.refund(x)\nout x\n") == []


def test_loop_body_is_checked():
    src = "> t(xs)\nfor i in xs: p = bank.charge(i, 1)\nout p\n"
    assert [d["line"] for d in check_effects(src)] == [2]
    src_ok = "> t(xs)\nfor i in xs: p = bank.charge(i, 1) ! bank.refund(p.tx)\nout p\n"
    assert check_effects(src_ok) == []


def test_cli_check_with_contracts_file(tmp_path):
    import json
    import subprocess
    import sys

    prog = tmp_path / "a.pick"
    prog.write_text("> t(x)\np = api.reserve(x, 1)\nout p.tx\n", encoding="utf-8")
    contracts = tmp_path / "contracts.json"
    contracts.write_text(json.dumps(
        {"api.reserve": {"args": ["x", "amt"], "returns": ["ok", "tx"], "effect": "money"}}),
        encoding="utf-8")

    r = subprocess.run([sys.executable, "-m", "picklang.cli", "check", str(prog),
                        "--contracts", str(contracts)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr  # эффект — warning, не error
    assert "EFFECT_NO_COMPENSATION" in r.stdout

    bad_field = tmp_path / "b.pick"
    bad_field.write_text("> t(x)\np = api.reserve(x, 1) ! api.release(p.id)\nout p.track\n",
                         encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "picklang.cli", "check", str(bad_field),
                        "--contracts", str(contracts)], capture_output=True, text=True)
    assert "UNKNOWN_FIELD" in r.stdout  # check_contracts подключён тем же флагом
