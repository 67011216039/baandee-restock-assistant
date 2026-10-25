import pytest

from tests.cases import CASES


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.__name__)
def test_case(case):
    inp, expected, actual, passed = case()
    assert passed, f"input={inp!r}\nexpected={expected!r}\nactual={actual!r}"
