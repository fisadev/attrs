"""
Tests for `attr._make`.
"""

from __future__ import absolute_import, division, print_function

import copy
import gc
import inspect
import itertools
import sys

from operator import attrgetter

import pytest

from hypothesis import given
from hypothesis.strategies import booleans, integers, lists, sampled_from, text

import attr

from attr import _config
from attr._compat import PY2, ordered_dict
from attr._make import (
    Attribute,
    Factory,
    _AndValidator,
    _Attributes,
    _ClassBuilder,
    _CountingAttr,
    _transform_attrs,
    and_,
    fields,
    fields_dict,
    make_class,
    validate,
)
from attr.exceptions import (
    DefaultAlreadySetError,
    NotAnAttrsClassError,
    PythonTooOldError,
)

from .strategies import (
    gen_attr_names,
    list_of_attrs,
    simple_attrs,
    simple_attrs_with_metadata,
    simple_attrs_without_metadata,
    simple_classes,
)
from .utils import simple_attr


attrs_st = simple_attrs.map(lambda c: Attribute.from_counting_attr("name", c))


class TestCountingAttr(object):
    """
    Tests for `attr`.
    """

    def test_returns_Attr(self):
        """
        Returns an instance of _CountingAttr.
        """
        a = attr.ib()

        assert isinstance(a, _CountingAttr)

    def test_validators_lists_to_wrapped_tuples(self):
        """
        If a list is passed as validator, it's just converted to a tuple.
        """

        def v1(_, __):
            pass

        def v2(_, __):
            pass

        a = attr.ib(validator=[v1, v2])

        assert _AndValidator((v1, v2)) == a._validator

    def test_validator_decorator_single(self):
        """
        If _CountingAttr.validator is used as a decorator and there is no
        decorator set, the decorated method is used as the validator.
        """
        a = attr.ib()

        @a.validator
        def v():
            pass

        assert v == a._validator

    @pytest.mark.parametrize(
        "wrap", [lambda v: v, lambda v: [v], lambda v: and_(v)]
    )
    def test_validator_decorator(self, wrap):
        """
        If _CountingAttr.validator is used as a decorator and there is already
        a decorator set, the decorators are composed using `and_`.
        """

        def v(_, __):
            pass

        a = attr.ib(validator=wrap(v))

        @a.validator
        def v2(self, _, __):
            pass

        assert _AndValidator((v, v2)) == a._validator

    def test_default_decorator_already_set(self):
        """
        Raise DefaultAlreadySetError if the decorator is used after a default
        has been set.
        """
        a = attr.ib(default=42)

        with pytest.raises(DefaultAlreadySetError):

            @a.default
            def f(self):
                pass

    def test_default_decorator_sets(self):
        """
        Decorator wraps the method in a Factory with pass_self=True and sets
        the default.
        """
        a = attr.ib()

        @a.default
        def f(self):
            pass

        assert Factory(f, True) == a._default


def make_tc():
    class TransformC(object):
        z = attr.ib()
        y = attr.ib()
        x = attr.ib()
        a = 42

    return TransformC


class TestTransformAttrs(object):
    """
    Tests for `_transform_attrs`.
    """

    def test_no_modifications(self):
        """
        Does not attach __attrs_attrs__ to the class.
        """
        C = make_tc()
        _transform_attrs(C, None, False, False)

        assert None is getattr(C, "__attrs_attrs__", None)

    def test_normal(self):
        """
        Transforms every `_CountingAttr` and leaves others (a) be.
        """
        C = make_tc()
        attrs, _, _ = _transform_attrs(C, None, False, False)

        assert ["z", "y", "x"] == [a.name for a in attrs]

    def test_empty(self):
        """
        No attributes works as expected.
        """

        @attr.s
        class C(object):
            pass

        assert _Attributes(((), [], {})) == _transform_attrs(
            C, None, False, False
        )

    def test_transforms_to_attribute(self):
        """
        All `_CountingAttr`s are transformed into `Attribute`s.
        """
        C = make_tc()
        attrs, base_attrs, _ = _transform_attrs(C, None, False, False)

        assert [] == base_attrs
        assert 3 == len(attrs)
        assert all(isinstance(a, Attribute) for a in attrs)

    def test_conflicting_defaults(self):
        """
        Raises `ValueError` if attributes with defaults are followed by
        mandatory attributes.
        """

        class C(object):
            x = attr.ib(default=None)
            y = attr.ib()

        with pytest.raises(ValueError) as e:
            _transform_attrs(C, None, False, False)
        assert (
            "No mandatory attributes allowed after an attribute with a "
            "default value or factory.  Attribute in question: Attribute"
            "(name='y', default=NOTHING, validator=None, repr=True, "
            "cmp=True, hash=None, init=True, metadata=mappingproxy({}), "
            "type=None, converter=None, kw_only=False)",
        ) == e.value.args

    def test_kw_only(self):
        """
        Converts all attributes, including base class' attributes, if `kw_only`
        is provided. Therefore, `kw_only` allows attributes with defaults to
        preceed mandatory attributes.

        Updates in the subclass *don't* affect the base class attributes.
        """

        @attr.s
        class B(object):
            b = attr.ib()

        for b_a in B.__attrs_attrs__:
            assert b_a.kw_only is False

        class C(B):
            x = attr.ib(default=None)
            y = attr.ib()

        attrs, base_attrs, _ = _transform_attrs(C, None, False, True)

        assert len(attrs) == 3
        assert len(base_attrs) == 1

        for a in attrs:
            assert a.kw_only is True

        for b_a in B.__attrs_attrs__:
            assert b_a.kw_only is False

    def test_these(self):
        """
        If these is passed, use it and ignore body and base classes.
        """

        class Base(object):
            z = attr.ib()

        class C(Base):
            y = attr.ib()

        attrs, base_attrs, _ = _transform_attrs(
            C, {"x": attr.ib()}, False, False
        )

        assert [] == base_attrs
        assert (simple_attr("x"),) == attrs

    def test_these_leave_body(self):
        """
        If these is passed, no attributes are removed from the body.
        """

        @attr.s(init=False, these={"x": attr.ib()})
        class C(object):
            x = 5

        assert 5 == C().x
        assert "C(x=5)" == repr(C())

    def test_these_ordered(self):
        """
        If these is passed ordered attrs, their order respect instead of the
        counter.
        """
        b = attr.ib(default=2)
        a = attr.ib(default=1)

        @attr.s(these=ordered_dict([("a", a), ("b", b)]))
        class C(object):
            pass

        assert "C(a=1, b=2)" == repr(C())

    def test_multiple_inheritance(self):
        """
        Order of attributes doesn't get mixed up by multiple inheritance.

        See #285
        """

        @attr.s
        class A(object):
            a1 = attr.ib(default="a1")
            a2 = attr.ib(default="a2")

        @attr.s
        class B(A):
            b1 = attr.ib(default="b1")
            b2 = attr.ib(default="b2")

        @attr.s
        class C(B, A):
            c1 = attr.ib(default="c1")
            c2 = attr.ib(default="c2")

        @attr.s
        class D(A):
            d1 = attr.ib(default="d1")
            d2 = attr.ib(default="d2")

        @attr.s
        class E(C, D):
            e1 = attr.ib(default="e1")
            e2 = attr.ib(default="e2")

        assert (
            "E(a1='a1', a2='a2', b1='b1', b2='b2', c1='c1', c2='c2', d1='d1', "
            "d2='d2', e1='e1', e2='e2')"
        ) == repr(E())


class TestAttributes(object):
    """
    Tests for the `attrs`/`attr.s` class decorator.
    """

    @pytest.mark.skipif(not PY2, reason="No old-style classes in Py3")
    def test_catches_old_style(self):
        """
        Raises TypeError on old-style classes.
        """
        with pytest.raises(TypeError) as e:

            @attr.s
            class C:
                pass

        assert ("attrs only works with new-style classes.",) == e.value.args

    def test_sets_attrs(self):
        """
        Sets the `__attrs_attrs__` class attribute with a list of `Attribute`s.
        """

        @attr.s
        class C(object):
            x = attr.ib()

        assert "x" == C.__attrs_attrs__[0].name
        assert all(isinstance(a, Attribute) for a in C.__attrs_attrs__)

    def test_empty(self):
        """
        No attributes, no problems.
        """

        @attr.s
        class C3(object):
            pass

        assert "C3()" == repr(C3())
        assert C3() == C3()

    @given(attr=attrs_st, attr_name=sampled_from(Attribute.__slots__))
    def test_immutable(self, attr, attr_name):
        """
        Attribute instances are immutable.
        """
        with pytest.raises(AttributeError):
            setattr(attr, attr_name, 1)

    @pytest.mark.parametrize(
        "method_name", ["__repr__", "__eq__", "__hash__", "__init__"]
    )
    def test_adds_all_by_default(self, method_name):
        """
        If no further arguments are supplied, all add_XXX functions except
        add_hash are applied.  __hash__ is set to None.
        """
        # Set the method name to a sentinel and check whether it has been
        # overwritten afterwards.
        sentinel = object()

        class C(object):
            x = attr.ib()

        setattr(C, method_name, sentinel)

        C = attr.s(C)
        meth = getattr(C, method_name)

        assert sentinel != meth
        if method_name == "__hash__":
            assert meth is None

    @pytest.mark.parametrize(
        "arg_name, method_name",
        [
            ("repr", "__repr__"),
            ("cmp", "__eq__"),
            ("hash", "__hash__"),
            ("init", "__init__"),
        ],
    )
    def test_respects_add_arguments(self, arg_name, method_name):
        """
        If a certain `add_XXX` is `False`, `__XXX__` is not added to the class.
        """
        # Set the method name to a sentinel and check whether it has been
        # overwritten afterwards.
        sentinel = object()

        am_args = {"repr": True, "cmp": True, "hash": True, "init": True}
        am_args[arg_name] = False

        class C(object):
            x = attr.ib()

        setattr(C, method_name, sentinel)

        C = attr.s(**am_args)(C)

        assert sentinel == getattr(C, method_name)

    @pytest.mark.skipif(PY2, reason="__qualname__ is PY3-only.")
    @given(slots_outer=booleans(), slots_inner=booleans())
    def test_repr_qualname(self, slots_outer, slots_inner):
        """
        On Python 3, the name in repr is the __qualname__.
        """

        @attr.s(slots=slots_outer)
        class C(object):
            @attr.s(slots=slots_inner)
            class D(object):
                pass

        assert "C.D()" == repr(C.D())
        assert "GC.D()" == repr(GC.D())

    @given(slots_outer=booleans(), slots_inner=booleans())
    def test_repr_fake_qualname(self, slots_outer, slots_inner):
        """
        Setting repr_ns overrides a potentially guessed namespace.
        """

        @attr.s(slots=slots_outer)
        class C(object):
            @attr.s(repr_ns="C", slots=slots_inner)
            class D(object):
                pass

        assert "C.D()" == repr(C.D())

    @pytest.mark.skipif(PY2, reason="__qualname__ is PY3-only.")
    @given(slots_outer=booleans(), slots_inner=booleans())
    def test_name_not_overridden(self, slots_outer, slots_inner):
        """
        On Python 3, __name__ is different from __qualname__.
        """

        @attr.s(slots=slots_outer)
        class C(object):
            @attr.s(slots=slots_inner)
            class D(object):
                pass

        assert C.D.__name__ == "D"
        assert C.D.__qualname__ == C.__qualname__ + ".D"

    @given(with_validation=booleans())
    def test_post_init(self, with_validation, monkeypatch):
        """
        Verify that __attrs_post_init__ gets called if defined.
        """
        monkeypatch.setattr(_config, "_run_validators", with_validation)

        @attr.s
        class C(object):
            x = attr.ib()
            y = attr.ib()

            def __attrs_post_init__(self2):
                self2.z = self2.x + self2.y

        c = C(x=10, y=20)

        assert 30 == getattr(c, "z", None)

    def test_types(self):
        """
        Sets the `Attribute.type` attr from type argument.
        """

        @attr.s
        class C(object):
            x = attr.ib(type=int)
            y = attr.ib(type=str)
            z = attr.ib()

        assert int is fields(C).x.type
        assert str is fields(C).y.type
        assert None is fields(C).z.type

    @pytest.mark.parametrize("slots", [True, False])
    def test_clean_class(self, slots):
        """
        Attribute definitions do not appear on the class body after @attr.s.
        """

        @attr.s(slots=slots)
        class C(object):
            x = attr.ib()

        x = getattr(C, "x", None)

        assert not isinstance(x, _CountingAttr)

    def test_factory_sugar(self):
        """
        Passing factory=f is syntactic sugar for passing default=Factory(f).
        """

        @attr.s
        class C(object):
            x = attr.ib(factory=list)

        assert Factory(list) == attr.fields(C).x.default

    def test_sugar_factory_mutex(self):
        """
        Passing both default and factory raises ValueError.
        """
        with pytest.raises(ValueError, match="mutually exclusive"):

            @attr.s
            class C(object):
                x = attr.ib(factory=list, default=Factory(list))

    def test_sugar_callable(self):
        """
        Factory has to be a callable to prevent people from passing Factory
        into it.
        """
        with pytest.raises(ValueError, match="must be a callable"):

            @attr.s
            class C(object):
                x = attr.ib(factory=Factory(list))


@pytest.mark.skipif(PY2, reason="keyword-only arguments are PY3-only.")
class TestKeywordOnlyAttributes(object):
    """
    Tests for keyword-only attributes.
    """

    def test_adds_keyword_only_arguments(self):
        """
        Attributes can be added as keyword-only.
        """

        @attr.s
        class C(object):
            a = attr.ib()
            b = attr.ib(default=2, kw_only=True)
            c = attr.ib(kw_only=True)
            d = attr.ib(default=attr.Factory(lambda: 4), kw_only=True)

        c = C(1, c=3)

        assert c.a == 1
        assert c.b == 2
        assert c.c == 3
        assert c.d == 4

    def test_ignores_kw_only_when_init_is_false(self):
        """
        Specifying ``kw_only=True`` when ``init=False`` is essentially a no-op.
        """

        @attr.s
        class C(object):
            x = attr.ib(init=False, default=0, kw_only=True)
            y = attr.ib()

        c = C(1)

        assert c.x == 0
        assert c.y == 1

    def test_keyword_only_attributes_presence(self):
        """
        Raises `TypeError` when keyword-only arguments are
        not specified.
        """

        @attr.s
        class C(object):
            x = attr.ib(kw_only=True)

        with pytest.raises(TypeError) as e:
            C()

        assert (
            "missing 1 required keyword-only argument: 'x'"
        ) in e.value.args[0]

    def test_keyword_only_attributes_can_come_in_any_order(self):
        """
        Mandatory vs non-mandatory attr order only matters when they are part
        of the __init__ signature and when they aren't kw_only (which are
        moved to the end and can be mandatory or non-mandatory in any order,
        as they will be specified as keyword args anyway).
        """

        @attr.s
        class C(object):
            a = attr.ib(kw_only=True)
            b = attr.ib(kw_only=True, default="b")
            c = attr.ib(kw_only=True)
            d = attr.ib()
            e = attr.ib(default="e")
            f = attr.ib(kw_only=True)
            g = attr.ib(kw_only=True, default="g")
            h = attr.ib(kw_only=True)

        c = C("d", a="a", c="c", f="f", h="h")

        assert c.a == "a"
        assert c.b == "b"
        assert c.c == "c"
        assert c.d == "d"
        assert c.e == "e"
        assert c.f == "f"
        assert c.g == "g"
        assert c.h == "h"

    def test_keyword_only_attributes_allow_subclassing(self):
        """
        Subclass can define keyword-only attributed without defaults,
        when the base class has attributes with defaults.
        """

        @attr.s
        class Base(object):
            x = attr.ib(default=0)

        @attr.s
        class C(Base):
            y = attr.ib(kw_only=True)

        c = C(y=1)

        assert c.x == 0
        assert c.y == 1

    def test_keyword_only_class_level(self):
        """
        `kw_only` can be provided at the attr.s level, converting all
        attributes to `kw_only.`
        """

        @attr.s(kw_only=True)
        class C:
            x = attr.ib()
            y = attr.ib(kw_only=True)

        with pytest.raises(TypeError):
            C(0, y=1)

        c = C(x=0, y=1)

        assert c.x == 0
        assert c.y == 1

    def test_keyword_only_class_level_subclassing(self):
        """
        Subclass `kw_only` propagates to attrs inherited from the base,
        allowing non-default following default.
        """

        @attr.s
        class Base(object):
            x = attr.ib(default=0)

        @attr.s(kw_only=True)
        class C(Base):
            y = attr.ib()

        with pytest.raises(TypeError):
            C(1)

        c = C(x=0, y=1)

        assert c.x == 0
        assert c.y == 1

    def test_init_false_attribute_after_keyword_attribute(self):
        """
        A positional attribute cannot follow a `kw_only` attribute,
        but an `init=False` attribute can because it won't appear
        in `__init__`
        """

        @attr.s
        class KwArgBeforeInitFalse:
            kwarg = attr.ib(kw_only=True)
            non_init_function_default = attr.ib(init=False)
            non_init_keyword_default = attr.ib(
                init=False, default="default-by-keyword"
            )

            @non_init_function_default.default
            def _init_to_init(self):
                return self.kwarg + "b"

        c = KwArgBeforeInitFalse(kwarg="a")

        assert c.kwarg == "a"
        assert c.non_init_function_default == "ab"
        assert c.non_init_keyword_default == "default-by-keyword"

    def test_init_false_attribute_after_keyword_attribute_with_inheritance(
        self
    ):
        """
        A positional attribute cannot follow a `kw_only` attribute,
        but an `init=False` attribute can because it won't appear
        in `__init__`. This test checks that we allow this
        even when the `kw_only` attribute appears in a parent class
        """

        @attr.s
        class KwArgBeforeInitFalseParent:
            kwarg = attr.ib(kw_only=True)

        @attr.s
        class KwArgBeforeInitFalseChild(KwArgBeforeInitFalseParent):
            non_init_function_default = attr.ib(init=False)
            non_init_keyword_default = attr.ib(
                init=False, default="default-by-keyword"
            )

            @non_init_function_default.default
            def _init_to_init(self):
                return self.kwarg + "b"

        c = KwArgBeforeInitFalseChild(kwarg="a")

        assert c.kwarg == "a"
        assert c.non_init_function_default == "ab"
        assert c.non_init_keyword_default == "default-by-keyword"


@pytest.mark.skipif(not PY2, reason="PY2-specific keyword-only error behavior")
class TestKeywordOnlyAttributesOnPy2(object):
    """
    Tests for keyword-only attribute behavior on py2.
    """

    def test_syntax_error(self):
        """
        Keyword-only attributes raise Syntax error on ``__init__`` generation.
        """

        with pytest.raises(PythonTooOldError):

            @attr.s(kw_only=True)
            class ClassLevel(object):
                a = attr.ib()

        with pytest.raises(PythonTooOldError):

            @attr.s()
            class AttrLevel(object):
                a = attr.ib(kw_only=True)

    def test_no_init(self):
        """
        Keyworld-only is a no-op, not any error, if ``init=false``.
        """

        @attr.s(kw_only=True, init=False)
        class ClassLevel(object):
            a = attr.ib()

        @attr.s(init=False)
        class AttrLevel(object):
            a = attr.ib(kw_only=True)


@attr.s
class GC(object):
    @attr.s
    class D(object):
        pass


class TestMakeClass(object):
    """
    Tests for `make_class`.
    """

    @pytest.mark.parametrize("ls", [list, tuple])
    def test_simple(self, ls):
        """
        Passing a list of strings creates attributes with default args.
        """
        C1 = make_class("C1", ls(["a", "b"]))

        @attr.s
        class C2(object):
            a = attr.ib()
            b = attr.ib()

        assert C1.__attrs_attrs__ == C2.__attrs_attrs__

    def test_dict(self):
        """
        Passing a dict of name: _CountingAttr creates an equivalent class.
        """
        C1 = make_class(
            "C1", {"a": attr.ib(default=42), "b": attr.ib(default=None)}
        )

        @attr.s
        class C2(object):
            a = attr.ib(default=42)
            b = attr.ib(default=None)

        assert C1.__attrs_attrs__ == C2.__attrs_attrs__

    def test_attr_args(self):
        """
        attributes_arguments are passed to attributes
        """
        C = make_class("C", ["x"], repr=False)

        assert repr(C(1)).startswith("<tests.test_make.C object at 0x")

    def test_catches_wrong_attrs_type(self):
        """
        Raise `TypeError` if an invalid type for attrs is passed.
        """
        with pytest.raises(TypeError) as e:
            make_class("C", object())

        assert ("attrs argument must be a dict or a list.",) == e.value.args

    def test_bases(self):
        """
        Parameter bases default to (object,) and subclasses correctly
        """

        class D(object):
            pass

        cls = make_class("C", {})

        assert cls.__mro__[-1] == object

        cls = make_class("C", {}, bases=(D,))

        assert D in cls.__mro__
        assert isinstance(cls(), D)

    @pytest.mark.parametrize("slots", [True, False])
    def test_clean_class(self, slots):
        """
        Attribute definitions do not appear on the class body.
        """
        C = make_class("C", ["x"], slots=slots)

        x = getattr(C, "x", None)

        assert not isinstance(x, _CountingAttr)

    def test_missing_sys_getframe(self, monkeypatch):
        """
        `make_class()` does not fail when `sys._getframe()` is not available.
        """
        monkeypatch.delattr(sys, "_getframe")
        C = make_class("C", ["x"])

        assert 1 == len(C.__attrs_attrs__)

    def test_make_class_ordered(self):
        """
        If `make_class()` is passed ordered attrs, their order is respected
        instead of the counter.
        """
        b = attr.ib(default=2)
        a = attr.ib(default=1)

        C = attr.make_class("C", ordered_dict([("a", a), ("b", b)]))

        assert "C(a=1, b=2)" == repr(C())


class TestFields(object):
    """
    Tests for `fields`.
    """

    def test_instance(self, C):
        """
        Raises `TypeError` on non-classes.
        """
        with pytest.raises(TypeError) as e:
            fields(C(1, 2))

        assert "Passed object must be a class." == e.value.args[0]

    def test_handler_non_attrs_class(self, C):
        """
        Raises `ValueError` if passed a non-``attrs`` instance.
        """
        with pytest.raises(NotAnAttrsClassError) as e:
            fields(object)

        assert (
            "{o!r} is not an attrs-decorated class.".format(o=object)
        ) == e.value.args[0]

    @given(simple_classes())
    def test_fields(self, C):
        """
        Returns a list of `Attribute`a.
        """
        assert all(isinstance(a, Attribute) for a in fields(C))

    @given(simple_classes())
    def test_fields_properties(self, C):
        """
        Fields returns a tuple with properties.
        """
        for attribute in fields(C):
            assert getattr(fields(C), attribute.name) is attribute


class TestFieldsDict(object):
    """
    Tests for `fields_dict`.
    """

    def test_instance(self, C):
        """
        Raises `TypeError` on non-classes.
        """
        with pytest.raises(TypeError) as e:
            fields_dict(C(1, 2))

        assert "Passed object must be a class." == e.value.args[0]

    def test_handler_non_attrs_class(self, C):
        """
        Raises `ValueError` if passed a non-``attrs`` instance.
        """
        with pytest.raises(NotAnAttrsClassError) as e:
            fields_dict(object)

        assert (
            "{o!r} is not an attrs-decorated class.".format(o=object)
        ) == e.value.args[0]

    @given(simple_classes())
    def test_fields_dict(self, C):
        """
        Returns an ordered dict of ``{attribute_name: Attribute}``.
        """
        d = fields_dict(C)

        assert isinstance(d, ordered_dict)
        assert list(fields(C)) == list(d.values())
        assert [a.name for a in fields(C)] == [field_name for field_name in d]


class TestConverter(object):
    """
    Tests for attribute conversion.
    """

    def test_convert(self):
        """
        Return value of converter is used as the attribute's value.
        """
        C = make_class(
            "C", {"x": attr.ib(converter=lambda v: v + 1), "y": attr.ib()}
        )
        c = C(1, 2)

        assert c.x == 2
        assert c.y == 2

    @given(integers(), booleans())
    def test_convert_property(self, val, init):
        """
        Property tests for attributes using converter.
        """
        C = make_class(
            "C",
            {
                "y": attr.ib(),
                "x": attr.ib(
                    init=init, default=val, converter=lambda v: v + 1
                ),
            },
        )
        c = C(2)

        assert c.x == val + 1
        assert c.y == 2

    @given(integers(), booleans())
    def test_converter_factory_property(self, val, init):
        """
        Property tests for attributes with converter, and a factory default.
        """
        C = make_class(
            "C",
            ordered_dict(
                [
                    ("y", attr.ib()),
                    (
                        "x",
                        attr.ib(
                            init=init,
                            default=Factory(lambda: val),
                            converter=lambda v: v + 1,
                        ),
                    ),
                ]
            ),
        )
        c = C(2)

        assert c.x == val + 1
        assert c.y == 2

    def test_factory_takes_self(self):
        """
        If takes_self on factories is True, self is passed.
        """
        C = make_class(
            "C",
            {
                "x": attr.ib(
                    default=Factory((lambda self: self), takes_self=True)
                )
            },
        )

        i = C()

        assert i is i.x

    def test_factory_hashable(self):
        """
        Factory is hashable.
        """
        assert hash(Factory(None, False)) == hash(Factory(None, False))

    def test_convert_before_validate(self):
        """
        Validation happens after conversion.
        """

        def validator(inst, attr, val):
            raise RuntimeError("foo")

        C = make_class(
            "C",
            {
                "x": attr.ib(validator=validator, converter=lambda v: 1 / 0),
                "y": attr.ib(),
            },
        )
        with pytest.raises(ZeroDivisionError):
            C(1, 2)

    def test_frozen(self):
        """
        Converters circumvent immutability.
        """
        C = make_class(
            "C", {"x": attr.ib(converter=lambda v: int(v))}, frozen=True
        )
        C("1")


class TestValidate(object):
    """
    Tests for `validate`.
    """

    def test_success(self):
        """
        If the validator succeeds, nothing gets raised.
        """
        C = make_class(
            "C", {"x": attr.ib(validator=lambda *a: None), "y": attr.ib()}
        )
        validate(C(1, 2))

    def test_propagates(self):
        """
        The exception of the validator is handed through.
        """

        def raiser(_, __, value):
            if value == 42:
                raise FloatingPointError

        C = make_class("C", {"x": attr.ib(validator=raiser)})
        i = C(1)
        i.x = 42

        with pytest.raises(FloatingPointError):
            validate(i)

    def test_run_validators(self):
        """
        Setting `_run_validators` to False prevents validators from running.
        """
        _config._run_validators = False
        obj = object()

        def raiser(_, __, ___):
            raise Exception(obj)

        C = make_class("C", {"x": attr.ib(validator=raiser)})
        c = C(1)
        validate(c)
        assert 1 == c.x
        _config._run_validators = True

        with pytest.raises(Exception):
            validate(c)

        with pytest.raises(Exception) as e:
            C(1)
        assert (obj,) == e.value.args

    def test_multiple_validators(self):
        """
        If a list is passed as a validator, all of its items are treated as one
        and must pass.
        """

        def v1(_, __, value):
            if value == 23:
                raise TypeError("omg")

        def v2(_, __, value):
            if value == 42:
                raise ValueError("omg")

        C = make_class("C", {"x": attr.ib(validator=[v1, v2])})

        validate(C(1))

        with pytest.raises(TypeError) as e:
            C(23)

        assert "omg" == e.value.args[0]

        with pytest.raises(ValueError) as e:
            C(42)

        assert "omg" == e.value.args[0]

    def test_multiple_empty(self):
        """
        Empty list/tuple for validator is the same as None.
        """
        C1 = make_class("C", {"x": attr.ib(validator=[])})
        C2 = make_class("C", {"x": attr.ib(validator=None)})

        assert inspect.getsource(C1.__init__) == inspect.getsource(C2.__init__)


# Hypothesis seems to cache values, so the lists of attributes come out
# unsorted.
sorted_lists_of_attrs = list_of_attrs.map(
    lambda l: sorted(l, key=attrgetter("counter"))
)


class TestMetadata(object):
    """
    Tests for metadata handling.
    """

    @given(sorted_lists_of_attrs)
    def test_metadata_present(self, list_of_attrs):
        """
        Assert dictionaries are copied and present.
        """
        C = make_class("C", dict(zip(gen_attr_names(), list_of_attrs)))

        for hyp_attr, class_attr in zip(list_of_attrs, fields(C)):
            if hyp_attr.metadata is None:
                # The default is a singleton empty dict.
                assert class_attr.metadata is not None
                assert len(class_attr.metadata) == 0
            else:
                assert hyp_attr.metadata == class_attr.metadata

                # Once more, just to assert getting items and iteration.
                for k in class_attr.metadata:
                    assert hyp_attr.metadata[k] == class_attr.metadata[k]
                    assert hyp_attr.metadata.get(k) == class_attr.metadata.get(
                        k
                    )

    @given(simple_classes(), text())
    def test_metadata_immutability(self, C, string):
        """
        The metadata dict should be best-effort immutable.
        """
        for a in fields(C):
            with pytest.raises(TypeError):
                a.metadata[string] = string
            with pytest.raises(AttributeError):
                a.metadata.update({string: string})
            with pytest.raises(AttributeError):
                a.metadata.clear()
            with pytest.raises(AttributeError):
                a.metadata.setdefault(string, string)

            for k in a.metadata:
                # For some reason, Python 3's MappingProxyType throws an
                # IndexError for deletes on a large integer key.
                with pytest.raises((TypeError, IndexError)):
                    del a.metadata[k]
                with pytest.raises(AttributeError):
                    a.metadata.pop(k)
            with pytest.raises(AttributeError):
                a.metadata.popitem()

    @given(lists(simple_attrs_without_metadata, min_size=2, max_size=5))
    def test_empty_metadata_singleton(self, list_of_attrs):
        """
        All empty metadata attributes share the same empty metadata dict.
        """
        C = make_class("C", dict(zip(gen_attr_names(), list_of_attrs)))
        for a in fields(C)[1:]:
            assert a.metadata is fields(C)[0].metadata

    @given(lists(simple_attrs_without_metadata, min_size=2, max_size=5))
    def test_empty_countingattr_metadata_independent(self, list_of_attrs):
        """
        All empty metadata attributes are independent before ``@attr.s``.
        """
        for x, y in itertools.combinations(list_of_attrs, 2):
            assert x.metadata is not y.metadata

    @given(lists(simple_attrs_with_metadata(), min_size=2, max_size=5))
    def test_not_none_metadata(self, list_of_attrs):
        """
        Non-empty metadata attributes exist as fields after ``@attr.s``.
        """
        C = make_class("C", dict(zip(gen_attr_names(), list_of_attrs)))

        assert len(fields(C)) > 0

        for cls_a, raw_a in zip(fields(C), list_of_attrs):
            assert cls_a.metadata != {}
            assert cls_a.metadata == raw_a.metadata

    def test_metadata(self):
        """
        If metadata that is not None is passed, it is used.

        This is necessary for coverage because the previous test is
        hypothesis-based.
        """
        md = {}
        a = attr.ib(metadata=md)

        assert md is a.metadata


class TestClassBuilder(object):
    """
    Tests for `_ClassBuilder`.
    """

    def test_repr_str(self):
        """
        Trying to add a `__str__` without having a `__repr__` raises a
        ValueError.
        """
        with pytest.raises(ValueError) as ei:
            make_class("C", {}, repr=False, str=True)

        assert (
            "__str__ can only be generated if a __repr__ exists.",
        ) == ei.value.args

    def test_repr(self):
        """
        repr of builder itself makes sense.
        """

        class C(object):
            pass

        b = _ClassBuilder(
            C, None, True, True, False, False, False, False, False
        )

        assert "<_ClassBuilder(cls=C)>" == repr(b)

    def test_returns_self(self):
        """
        All methods return the builder for chaining.
        """

        class C(object):
            x = attr.ib()

        b = _ClassBuilder(
            C, None, True, True, False, False, False, False, False
        )

        cls = (
            b.add_cmp()
            .add_hash()
            .add_init()
            .add_repr("ns")
            .add_str()
            .build_class()
        )

        assert "ns.C(x=1)" == repr(cls(1))

    @pytest.mark.parametrize(
        "meth_name",
        [
            "__init__",
            "__hash__",
            "__repr__",
            "__str__",
            "__eq__",
            "__ne__",
            "__lt__",
            "__le__",
            "__gt__",
            "__ge__",
        ],
    )
    def test_attaches_meta_dunders(self, meth_name):
        """
        Generated methods have correct __module__, __name__, and __qualname__
        attributes.
        """

        @attr.s(hash=True, str=True)
        class C(object):
            def organic(self):
                pass

        meth = getattr(C, meth_name)

        assert meth_name == meth.__name__
        assert C.organic.__module__ == meth.__module__
        if not PY2:
            organic_prefix = C.organic.__qualname__.rsplit(".", 1)[0]
            assert organic_prefix + "." + meth_name == meth.__qualname__

    def test_handles_missing_meta_on_class(self):
        """
        If the class hasn't a __module__ or __qualname__, the method hasn't
        either.
        """

        class C(object):
            pass

        b = _ClassBuilder(
            C,
            these=None,
            slots=False,
            frozen=False,
            weakref_slot=True,
            auto_attribs=False,
            is_exc=False,
            kw_only=False,
            cache_hash=False,
        )
        b._cls = {}  # no __module__; no __qualname__

        def fake_meth(self):
            pass

        fake_meth.__module__ = "42"
        fake_meth.__qualname__ = "23"

        rv = b._add_method_dunders(fake_meth)

        assert "42" == rv.__module__ == fake_meth.__module__
        assert "23" == rv.__qualname__ == fake_meth.__qualname__

    def test_weakref_setstate(self):
        """
        __weakref__ is not set on in setstate because it's not writable in
        slotted classes.
        """

        @attr.s(slots=True)
        class C(object):
            __weakref__ = attr.ib(
                init=False, hash=False, repr=False, cmp=False
            )

        assert C() == copy.deepcopy(C())

    def test_no_references_to_original(self):
        """
        When subclassing a slotted class, there are no stray references to the
        original class.
        """

        @attr.s(slots=True)
        class C(object):
            pass

        @attr.s(slots=True)
        class C2(C):
            pass

        # The original C2 is in a reference cycle, so force a collect:
        gc.collect()

        assert [C2] == C.__subclasses__()


class TestMakeCmp:
    """
    Tests for _make_cmp().
    """

    @pytest.mark.parametrize(
        "op", ["__%s__" % (op,) for op in ("lt", "le", "gt", "ge")]
    )
    def test_subclasses_deprecated(self, recwarn, op):
        """
        Calling comparison methods on subclasses raises a deprecation warning;
        calling them on identical classes does not..
        """

        @attr.s
        class A(object):
            a = attr.ib()

        @attr.s
        class B(A):
            pass

        getattr(A(42), op)(A(42))
        getattr(B(42), op)(B(42))

        assert [] == recwarn.list

        getattr(A(42), op)(B(42))

        w = recwarn.pop()

        assert [] == recwarn.list
        assert isinstance(w.message, DeprecationWarning)
        assert (
            "Comparision of subclasses using %s is deprecated and will be "
            "removed in 2019." % (op,)
        ) == w.message.args[0]
