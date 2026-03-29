from shared.signatures.rce_smb.rules import RuleEngine
from shared.signatures.rce_smb.loader import SignatureLoader


def test_all_signature_verdict_conditions_parse():
    loader = SignatureLoader()
    signatures = loader.load_all()
    engine = RuleEngine(signatures)

    for sig in signatures:
        for key in ["confirmed_when", "likely_when", "not_vulnerable_when", "not_assessable_when"]:
            for rule in sig.verdict_mapping.get(key, []):
                condition = rule.get("condition", "")
                assert isinstance(engine._eval_verdict_condition(condition, {}), bool)


def test_hex_int_comparison():
    engine = RuleEngine([])
    assert engine._eval_verdict_condition("status == 0xC0000205", {"status": 0xC0000205}) is True
    assert engine._eval_verdict_condition("status == 0xC0000205", {"status": "0xc0000205"}) is True


def test_list_membership_with_integers():
    engine = RuleEngine([])
    assert engine._eval_verdict_condition("status in [0xC0000022, 0xC0000008]", {"status": 0xC0000022}) is True
    assert engine._eval_verdict_condition("status in [0xC0000022, 0xC0000008]", {"status": "0xc0000008"}) is True


def test_boolean_string_comparison():
    engine = RuleEngine([])
    assert engine._eval_verdict_condition("smb1_possible == true", {"smb1_possible": True}) is True
    assert engine._eval_verdict_condition("smb1_possible == false", {"smb1_possible": False}) is True
