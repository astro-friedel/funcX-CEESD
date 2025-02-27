import inspect
import sys

import pytest

import funcx.serialize.concretes as concretes


def foo(x, y=3):
    return x * y


def double_first_argument(func):
    """
    Test decorator that will double the first argument if third is True
    """

    def wrapper_double_first_argument(a, b, c):
        return func(a * 2, b, c) if c else func(a, b, c)

    return wrapper_double_first_argument


@double_first_argument
def decorated_add(a, b, c):
    """
    Third argument is ignored, only for decorator usage
    """
    return a + b


def check_deserialized_foo(f):
    p1, p2 = inspect.signature(f).parameters.items()

    param_1: inspect.Parameter = p1[1]
    param_2: inspect.Parameter = p2[1]

    assert isinstance(param_1, inspect.Parameter)
    assert isinstance(param_2, inspect.Parameter)
    assert "x" == param_1.name
    assert "y" == param_2.name
    assert 3 == param_2.default


def check_deserialized_bar(f):
    p1, p2 = inspect.signature(f).parameters.items()

    param_1: inspect.Parameter = p1[1]
    param_2: inspect.Parameter = p2[1]

    assert isinstance(param_1, inspect.Parameter)
    assert isinstance(param_2, inspect.Parameter)
    assert "a" == param_1.name
    assert "b" == param_2.name
    assert 5 == param_2.default


def check_serialize_deserialize_foo(concrete_cls, pre_serialized=None):
    serialized_foo = concrete_cls.serialize(foo)
    deserialized_foo = concrete_cls.deserialize(serialized_foo)
    check_deserialized_foo(deserialized_foo)

    if pre_serialized:
        deserialized_foo = concrete_cls.deserialize(pre_serialized)
        check_deserialized_foo(deserialized_foo)


def check_serialize_deserialize_bar(concrete_cls, pre_serialized=None):
    def inner_bar(a, b=5):
        return a * 5

    serialized_bar = concrete_cls.serialize(inner_bar)
    deserialized_bar = concrete_cls.deserialize(serialized_bar)
    check_deserialized_bar(deserialized_bar)

    if pre_serialized:
        deserialized_bar = concrete_cls.deserialize(pre_serialized)
        check_deserialized_bar(deserialized_bar)


def check_serialize_deserialize_add(concrete_cls, pre_serialized=None):
    serialized_add = concrete_cls.serialize(decorated_add)
    deserialized_add = concrete_cls.deserialize(serialized_add)
    assert 10 == deserialized_add(3, 4, True)
    assert 7 == deserialized_add(3, 4, False)

    if pre_serialized:
        deserialized_add = concrete_cls.deserialize(pre_serialized)
        assert 10 == deserialized_add(3, 4, True)
        assert 7 == deserialized_add(3, 4, False)


def test_base64_data():
    jb = concretes.DillDataBase64()

    d = jb.serialize(([2], {"y": 10}))
    assert "00" == d.split("\n")[0]
    args, kwargs = jb.deserialize(d)
    assert args[0] == 2
    assert kwargs["y"] == 10

    # Depends on running this test in non 3.7 and passing
    py37_serial_data = "00\ngANdcQBLAmF9cQFYAQAAAHlxAksKc4ZxAy4=\n"

    args, kwargs = jb.deserialize(py37_serial_data)
    assert args[0] == 2
    assert kwargs["y"] == 10


def test_pickle_deserialize():
    jb = concretes.PickleCode()

    with pytest.raises(NotImplementedError):
        jb.serialize(foo)

    # Serialized via 3.7, should work in other versions
    serialized_foo_pickle = "02\ngANjdGVzdF9zZXJpYWxpemF0aW9uCmZvbwpxAC4="

    deserialized_foo = jb.deserialize(serialized_foo_pickle)
    check_deserialized_foo(deserialized_foo)


def test_code_dill():
    """
    This is our default primary path going forward, and it should handle
    the most methods.
    """
    pre_serialized_foo_37 = "01\ngANjdGVzdF9zZXJpYWxpemF0aW9uCmZvbwpxAC4=\n"
    check_serialize_deserialize_foo(concretes.DillCode(), pre_serialized_foo_37)
    pre_serialized_bar_37 = (
        "01\ngANjZGlsbC5fZGlsbApfY3JlYXRlX2Z1bmN0aW9uCnEAKGNkaWxsLl9kaWxsCl9"
        "jcmVhdGVfY29kZQpxAShLAksASwJLAktTQwh8AGQBFABTAHECTksFhnEDKVgBAAAAYX"
        "EEWAEAAABicQWGcQZYRwAAAC9Vc2Vycy9sZWkvZ2xvYi9mdW5jWC9mdW5jeF9zZGsvd"
        "GVzdHMvaW50ZWdyYXRpb24vdGVzdF9zZXJpYWxpemF0aW9uLnB5cQdYCQAAAGlubmVy"
        "X2JhcnEISzhDAgABcQkpKXRxClJxC2N0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18"
        "KaAhLBYVxDE50cQ1ScQ59cQ99cRAoWA8AAABfX2Fubm90YXRpb25zX19xEX1xElgMAA"
        "AAX19xdWFsbmFtZV9fcRNYMgAAAGNoZWNrX3NlcmlhbGl6ZV9kZXNlcmlhbGl6ZV9iY"
        "XIuPGxvY2Fscz4uaW5uZXJfYmFycRR1hnEVYi4=\n"
    )
    check_serialize_deserialize_bar(
        concretes.DillCode(), pre_serialized=pre_serialized_bar_37
    )
    pre_serialized_add_37 = (
        "01\n"
        "gANjZGlsbC5fZGlsbApfY3JlYXRlX2Z1bmN0aW9uCnEAKGNkaWxsLl9kaWxsCl9jcmVhdGVfY29k\n"
        "ZQpxAShLA0sASwNLBEsTQyB8AnIUiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAHECTksChnED\n"
        "KVgBAAAAYXEEWAEAAABicQVYAQAAAGNxBodxB1hHAAAAL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1\n"
        "bmN4X3Nkay90ZXN0cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHlxCFgdAAAAd3Jh\n"
        "cHBlcl9kb3VibGVfZmlyc3RfYXJndW1lbnRxCUsNQwIAAXEKWAQAAABmdW5jcQuFcQwpdHENUnEO\n"
        "Y3Rlc3Rfc2VyaWFsaXphdGlvbgpfX2RpY3RfXwpoCU5jZGlsbC5fZGlsbApfY3JlYXRlX2NlbGwK\n"
        "cQ9OhXEQUnERhXESdHETUnEUfXEVfXEWKFgPAAAAX19hbm5vdGF0aW9uc19fcRd9cRhYDAAAAF9f\n"
        "cXVhbG5hbWVfX3EZWDwAAABkb3VibGVfZmlyc3RfYXJndW1lbnQuPGxvY2Fscz4ud3JhcHBlcl9k\n"
        "b3VibGVfZmlyc3RfYXJndW1lbnRxGnWGcRtiY2J1aWx0aW5zCmdldGF0dHIKcRxjZGlsbApfZGls\n"
        "bApxHVgIAAAAX3NldGF0dHJxHmNidWlsdGlucwpzZXRhdHRyCnEfh3EgUnEhaBFYDQAAAGNlbGxf\n"
        "Y29udGVudHNxImgAKGgBKEsDSwBLA0sCS0NDCHwAfAEXAFMAcSNYPQAAAAogICAgVGhpcmQgYXJn\n"
        "dW1lbnQgaXMgaWdub3JlZCwgb25seSBmb3IgZGVjb3JhdG9yIHVzYWdlCiAgICBxJIVxJSloBGgF\n"
        "aAaHcSZoCFgNAAAAZGVjb3JhdGVkX2FkZHEnSxJDAgAFcSgpKXRxKVJxKmN0ZXN0X3NlcmlhbGl6\n"
        "YXRpb24KX19kaWN0X18KaCdOTnRxK1JxLH1xLX1xLihYBwAAAF9fZG9jX19xL2gkaBd9cTB1hnEx\n"
        "YodxMlIwLg==\n"
    )
    pre_serialized_add_310 = (
        "01\n"
        "gASVxAIAAAAAAACMCmRpbGwuX2RpbGyUjBBfY3JlYXRlX2Z1bmN0aW9ulJOUKGgAjAxfY3JlYXRl\n"
        "X2NvZGWUk5QoSwNLAEsASwNLBEsTQyB8AnIKiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAJRO\n"
        "SwKGlCmMAWGUjAFilIwBY5SHlIxHL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1bmN4X3Nkay90ZXN0\n"
        "cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHmUjB13cmFwcGVyX2RvdWJsZV9maXJz\n"
        "dF9hcmd1bWVudJRLEkMCAAGUjARmdW5jlIWUKXSUUpRjdGVzdF9zZXJpYWxpemF0aW9uCl9fZGlj\n"
        "dF9fCmgMTmgAjAxfY3JlYXRlX2NlbGyUk5ROhZRSlIWUdJRSlH2UfZQojA9fX2Fubm90YXRpb25z\n"
        "X1+UfZSMDF9fcXVhbG5hbWVfX5SMPGRvdWJsZV9maXJzdF9hcmd1bWVudC48bG9jYWxzPi53cmFw\n"
        "cGVyX2RvdWJsZV9maXJzdF9hcmd1bWVudJR1hpRijAhidWlsdGluc5SMB2dldGF0dHKUk5SMC2Rp\n"
        "bGwuX3NoaW1zlIwFX2RpbGyUk5SMCF9zZXRhdHRylGggjAdzZXRhdHRylJOUh5RSlGgVjA1jZWxs\n"
        "X2NvbnRlbnRzlGgCKGgEKEsDSwBLAEsDSwJLQ0MIfAB8ARcAUwCUjD0KICAgIFRoaXJkIGFyZ3Vt\n"
        "ZW50IGlzIGlnbm9yZWQsIG9ubHkgZm9yIGRlY29yYXRvciB1c2FnZQogICAglIWUKWgKaAuMDWRl\n"
        "Y29yYXRlZF9hZGSUSxhDAgAFlCkpdJRSlGN0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18KaC9O\n"
        "TnSUUpR9lH2UKIwHX19kb2NfX5RoLWgbfZR1hpRih5RSMC4=\n"
    )

    pre_serialized_add_39 = (
        "01\n"
        "gASVxAIAAAAAAACMCmRpbGwuX2RpbGyUjBBfY3JlYXRlX2Z1bmN0aW9ulJOUKGgAjAxfY3JlYXRl\n"
        "X2NvZGWUk5QoSwNLAEsASwNLBEsTQyB8AnIUiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAJRO\n"
        "SwKGlCmMAWGUjAFilIwBY5SHlIxHL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1bmN4X3Nkay90ZXN0\n"
        "cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHmUjB13cmFwcGVyX2RvdWJsZV9maXJz\n"
        "dF9hcmd1bWVudJRLEkMCAAGUjARmdW5jlIWUKXSUUpRjdGVzdF9zZXJpYWxpemF0aW9uCl9fZGlj\n"
        "dF9fCmgMTmgAjAxfY3JlYXRlX2NlbGyUk5ROhZRSlIWUdJRSlH2UfZQojA9fX2Fubm90YXRpb25z\n"
        "X1+UfZSMDF9fcXVhbG5hbWVfX5SMPGRvdWJsZV9maXJzdF9hcmd1bWVudC48bG9jYWxzPi53cmFw\n"
        "cGVyX2RvdWJsZV9maXJzdF9hcmd1bWVudJR1hpRijAhidWlsdGluc5SMB2dldGF0dHKUk5SMC2Rp\n"
        "bGwuX3NoaW1zlIwFX2RpbGyUk5SMCF9zZXRhdHRylGggjAdzZXRhdHRylJOUh5RSlGgVjA1jZWxs\n"
        "X2NvbnRlbnRzlGgCKGgEKEsDSwBLAEsDSwJLQ0MIfAB8ARcAUwCUjD0KICAgIFRoaXJkIGFyZ3Vt\n"
        "ZW50IGlzIGlnbm9yZWQsIG9ubHkgZm9yIGRlY29yYXRvciB1c2FnZQogICAglIWUKWgKaAuMDWRl\n"
        "Y29yYXRlZF9hZGSUSxhDAgAFlCkpdJRSlGN0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18KaC9O\n"
        "TnSUUpR9lH2UKIwHX19kb2NfX5RoLWgbfZR1hpRih5RSMC4=\n"
    )

    pre_serialized = pre_serialized_add_37

    # dill.dumps()/loads() does not deserialize functions serialized with some
    #     other python versions.  Some info:
    #     https://github.com/cloudpipe/cloudpickle/issues/293
    # TODO figure out a better way to deserialize mismatched versions
    #     or possibly give up on trying to deserialize mismatched versions
    # For now, just test pre-generated strings for 3.7, 3.9, and 3.10, and
    #     force version upgrades to fail in tox until a known string is
    #     generated in check_serialize_deserialize_add() above
    major = sys.version_info.major
    minor = sys.version_info.minor
    if major == 3 and minor == 9:
        pre_serialized = pre_serialized_add_39
    elif major == 3 and minor == 10:
        pre_serialized = pre_serialized_add_310
    elif major == 3 and minor > 10:
        # Failing probably better than forgetting about version mismatches
        # in the future
        pytest.fail(f"Pre-serialized string needed for py{major}.{minor}")

    check_serialize_deserialize_add(concretes.DillCode(), pre_serialized)


def test_code_text_inspect():
    check_serialize_deserialize_foo(concretes.DillCodeTextInspect())
    # code source inspect doesn't handle the indents of bar


def test_code_dill_source():
    check_serialize_deserialize_foo(concretes.DillCodeSource())
    # code source inspect doesn't handle the indents of bar


def test_overall():
    from funcx.serialize.facade import FuncXSerializer

    check_serialize_deserialize_foo(FuncXSerializer())
    check_serialize_deserialize_bar(FuncXSerializer())
