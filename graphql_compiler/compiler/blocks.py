# Copyright 2017-present Kensho Technologies, LLC.
"""Definitions of the basic blocks of the compiler."""

from typing import Callable, Dict, Set

import six

from .compiler_entities import BasicBlock, Expression, MarkerBlock
from .helpers import (
    BaseLocation,
    FoldScopeLocation,
    ensure_unicode_string,
    safe_quoted_string,
    validate_edge_direction,
    validate_marked_location,
    validate_safe_string,
)


class QueryRoot(BasicBlock):
    """The starting object of the query to be compiled."""

    __slots__ = ("start_class",)

    def __init__(self, start_class: Set[str]) -> None:
        """Construct a QueryRoot object that starts querying at the specified class name.

        Args:
            start_class: set of string, class names from which to start the query.
                         This will generally be a set of length 1, except when using Gremlin
                         with a non-final class, where we have to include all subclasses
                         of the start class. This is done using a Gremlin-only IR lowering step.
        """
        super(QueryRoot, self).__init__(start_class)
        self.start_class = start_class
        self.validate()

    def validate(self) -> None:
        """Ensure that the QueryRoot block is valid."""
        if not (
            isinstance(self.start_class, set)
            and all(isinstance(x, six.string_types) for x in self.start_class)
        ):
            raise TypeError(
                "Expected set of string start_class, got: {} {}".format(
                    type(self.start_class).__name__, self.start_class
                )
            )

        for cls in self.start_class:
            validate_safe_string(cls)

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()
        if len(self.start_class) == 1:
            # The official Gremlin documentation claims that this approach
            # is generally faster than the one below, since it makes using indexes easier.
            # http://gremlindocs.spmallette.documentup.com/#filter/has
            start_class = list(self.start_class)[0]
            return "g.V({}, {})".format("'@class'", safe_quoted_string(start_class))
        else:
            start_classes_list = ",".join(safe_quoted_string(x) for x in self.start_class)
            return "g.V.has('@class', T.in, [{}])".format(start_classes_list)


class CoerceType(BasicBlock):
    """A special type of filter that discards any data that is not of the specified set of types."""

    __slots__ = ("target_class",)

    def __init__(self, target_class: Set[str]) -> None:
        """Construct a CoerceType object that filters out any data that is not of the given types.

        Args:
            target_class: set of string, class names from which to start the query.
                          This will generally be a set of length 1, except when using Gremlin
                          with a non-final class, where we have to include all subclasses
                          of the target class. This is done using a Gremlin-only IR lowering step.
        """
        super(CoerceType, self).__init__(target_class)
        self.target_class = target_class
        self.validate()

    def validate(self) -> None:
        """Ensure that the CoerceType block is valid."""
        if not (
            isinstance(self.target_class, set)
            and all(isinstance(x, six.string_types) for x in self.target_class)
        ):
            raise TypeError(
                "Expected set of string target_class, got: {} {}".format(
                    type(self.target_class).__name__, self.target_class
                )
            )

        for cls in self.target_class:
            validate_safe_string(cls)

    def to_gremlin(self) -> str:
        """Not implemented, should not be used."""
        raise AssertionError(
            "CoerceType blocks must be appropriately lowered before being "
            "transformed into Gremlin code. This function should not be used."
        )


class ConstructResult(BasicBlock):
    """A transformation of the data into a new form, for output."""

    __slots__ = ("fields",)

    def __init__(self, fields: Dict[str, Expression]) -> None:
        """Construct a ConstructResult object that maps the given field names to their expressions.

        Args:
            fields: dict, variable name string -> Expression
                    see rules for variable names in validate_safe_string().
        """
        self.fields = {ensure_unicode_string(key): value for key, value in six.iteritems(fields)}

        # All key values are normalized to unicode before being passed to the parent constructor,
        # which saves them to enable human-readable printing and other functions.
        super(ConstructResult, self).__init__(self.fields)
        self.validate()

    def validate(self) -> None:
        """Ensure that the ConstructResult block is valid."""
        if not isinstance(self.fields, dict):
            raise TypeError(
                "Expected dict fields, got: {} {}".format(type(self.fields).__name__, self.fields)
            )

        for key, value in six.iteritems(self.fields):
            validate_safe_string(key)
            if not isinstance(value, Expression):
                raise TypeError(
                    "Expected Expression values in the fields dict, got: "
                    "{} -> {}".format(key, value)
                )

    def visit_and_update_expressions(
        self, visitor_fn: Callable[[Expression], Expression]
    ) -> "ConstructResult":
        """Create an updated version (if needed) of the ConstructResult via the visitor pattern."""
        new_fields = {}

        for key, value in six.iteritems(self.fields):
            new_value = value.visit_and_update(visitor_fn)
            if new_value is not value:
                new_fields[key] = new_value

        if new_fields:
            return ConstructResult(dict(self.fields, **new_fields))
        else:
            return self

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()

        template = (
            "transform{{"
            "it, m -> new com.orientechnologies.orient.core.record.impl.ODocument([ {} ])"
            "}}"
        )

        field_representations = (
            "{name}: {expr}".format(name=key, expr=self.fields[key].to_gremlin())
            for key in sorted(self.fields.keys())  # Sort the keys for deterministic output order.
        )
        return template.format(", ".join(field_representations))


class Filter(BasicBlock):
    """A filter that ensures data matches a predicate expression, and discards all other data."""

    __slots__ = ("predicate",)

    def __init__(self, predicate: Expression) -> None:
        """Create a new Filter with the specified Expression as a predicate."""
        super(Filter, self).__init__(predicate)
        self.predicate = predicate
        self.validate()

    def validate(self) -> None:
        """Ensure that the Filter block is valid."""
        if not isinstance(self.predicate, Expression):
            raise TypeError(
                "Expected Expression predicate, got: {} {}".format(
                    type(self.predicate).__name__, self.predicate
                )
            )

    def visit_and_update_expressions(
        self, visitor_fn: Callable[[Expression], Expression]
    ) -> "Filter":
        """Create an updated version (if needed) of the Filter via the visitor pattern."""
        new_predicate = self.predicate.visit_and_update(visitor_fn)
        if new_predicate is not self.predicate:
            return Filter(new_predicate)
        else:
            return self

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()
        return "filter{{it, m -> {}}}".format(self.predicate.to_gremlin())


class MarkLocation(BasicBlock):
    """A block that assigns a name to a given BaseLocation in the query."""

    __slots__ = ("location",)

    def __init__(self, location: BaseLocation) -> None:
        """Create a new MarkLocation at the specified BaseLocation.

        Args:
            location: BaseLocation object, must not be at a property field in the query
        """
        super(MarkLocation, self).__init__(location)
        self.location = location
        self.validate()

    def validate(self) -> None:
        """Ensure that the MarkLocation block is valid."""
        validate_marked_location(self.location)

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()
        mark_name, _ = self.location.get_location_name()
        return "as({})".format(safe_quoted_string(mark_name))


class Traverse(BasicBlock):
    """A block that encodes a traversal across an edge, in either direction."""

    __slots__ = ("direction", "edge_name", "optional", "within_optional_scope")

    def __init__(
        self,
        direction: str,
        edge_name: str,
        optional: bool = False,
        within_optional_scope: bool = False,
    ) -> None:
        """Create a new Traverse block in the given direction and across the given edge.

        Args:
            direction: string, 'in' or 'out'
            edge_name: string obeying variable name rules (see validate_safe_string).
            optional: optional bool, specifying whether the traversal to the given location
                      is optional (i.e. non-filtering) or mandatory (filtering).
            within_optional_scope: optional bool, set to True to indicate that this Traverse
                                   is located within a scope marked @optional
        """
        super(Traverse, self).__init__(
            direction, edge_name, optional=optional, within_optional_scope=within_optional_scope
        )
        self.direction = direction
        self.edge_name = edge_name
        self.optional = optional
        # Denotes whether the traversal is occurring after a prior @optional traversal
        self.within_optional_scope = within_optional_scope
        self.validate()

    def validate(self) -> None:
        """Ensure that the Traverse block is valid."""
        if not isinstance(self.direction, six.string_types):
            raise TypeError(
                "Expected string direction, got: {} {}".format(
                    type(self.direction).__name__, self.direction
                )
            )

        validate_edge_direction(self.direction)
        validate_safe_string(self.edge_name)

        if not isinstance(self.optional, bool):
            raise TypeError(
                "Expected bool optional, got: {} {}".format(
                    type(self.optional).__name__, self.optional
                )
            )

        if not isinstance(self.within_optional_scope, bool):
            raise TypeError(
                "Expected bool within_optional_scope, got: {} "
                "{}".format(type(self.within_optional_scope).__name__, self.within_optional_scope)
            )

    def get_field_name(self) -> str:
        """Return the field name corresponding to the edge being traversed."""
        return "{}_{}".format(self.direction, self.edge_name)

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()
        if self.optional:
            # Optional edges have to be handled differently than non-optionals, since the compiler
            # provides the guarantee that properties read from an optional, non-existing location
            # always resolve to a "null" value. This guarantee is not upheld by default by Gremlin;
            # in fact, Gremlin .as('foo').out().as('bar').optional('foo') does not provide
            # ANY guarantees as to what the value at any "bar.*" is -- it could be "null",
            # it could be a previous pipeline element's location at "bar.*" or anything else.
            # The .ifThenElse block ensures that the edge traversal happens only if the edge exists,
            # and that otherwise the result in the pipeline is replaced with "null".
            #
            # The code below makes the assumption that links to outward/inward edges are stored
            # as vertex properties named "<direction>_<edge_name>" where direction is "in" or "out".
            # For example, the links to outward edges named "Person_SpeechBy" from Person
            # are assumed to be stored as "out_Person_SpeechBy" on the Person node.
            return (
                "ifThenElse{{it.{direction}_{edge_name} == null}}"
                "{{null}}{{it.{direction}({edge_quoted})}}".format(
                    direction=self.direction,
                    edge_name=self.edge_name,
                    edge_quoted=safe_quoted_string(self.edge_name),
                )
            )
        elif self.within_optional_scope:
            # During a traversal, the pipeline element may be null.
            # The following code returns null when the current pipeline entity is null
            # (an optional edge did not exist at some earlier traverse).
            # Otherwise it performs a normal traversal (previous optional edge did exist).
            return "ifThenElse{{it == null}}{{null}}{{it.{direction}({edge_quoted})}}".format(
                direction=self.direction, edge_quoted=safe_quoted_string(self.edge_name)
            )
        else:
            return "{direction}({edge})".format(
                direction=self.direction, edge=safe_quoted_string(self.edge_name)
            )


class Recurse(BasicBlock):
    """A block for recursive traversal of an edge, collecting all endpoints along the way."""

    __slots__ = ("direction", "edge_name", "depth", "within_optional_scope")

    def __init__(
        self, direction: str, edge_name: str, depth: int, within_optional_scope: bool = False
    ) -> None:
        """Create a new Recurse block which traverses the given edge up to "depth" times.

        Args:
            direction: string, 'in' or 'out'.
            edge_name: string obeying variable name rules (see validate_safe_string).
            depth: int, always greater than or equal to 1.
            within_optional_scope: optional bool, set to True to indicate that this Recurse
                                   is located within a scope marked @optional
        """
        super(Recurse, self).__init__(
            direction, edge_name, depth, within_optional_scope=within_optional_scope
        )
        self.direction = direction
        self.edge_name = edge_name
        self.depth = depth
        # Denotes whether the traversal is occurring after a prior @optional traversal
        self.within_optional_scope = within_optional_scope
        self.validate()

    def validate(self) -> None:
        """Ensure that the Traverse block is valid."""
        validate_edge_direction(self.direction)
        validate_safe_string(self.edge_name)

        if not isinstance(self.within_optional_scope, bool):
            raise TypeError(
                "Expected bool within_optional_scope, got: {} "
                "{}".format(type(self.within_optional_scope).__name__, self.within_optional_scope)
            )

        if not isinstance(self.depth, int):
            raise TypeError(
                "Expected int depth, got: {} {}".format(type(self.depth).__name__, self.depth)
            )

        if not (self.depth >= 1):
            raise ValueError("depth ({}) >= 1 does not hold!".format(self.depth))

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this block."""
        self.validate()
        template = "copySplit({recurse}).exhaustMerge"
        recurse_base = "_()"
        recurse_traversal = ".{direction}('{edge_name}')".format(
            direction=self.direction, edge_name=self.edge_name
        )

        recurse_steps = [
            recurse_base + (recurse_traversal * i) for i in six.moves.xrange(self.depth + 1)
        ]
        recursion_string = template.format(recurse=",".join(recurse_steps))
        if self.within_optional_scope:
            # During a traversal, the pipeline element may be null.
            # The following code returns null when the current pipeline entity is null
            # (an optional edge did not exist at some earlier traverse).
            # Otherwise it performs a normal recursion (previous optional edge did exist).
            recurse_template = "ifThenElse{{it == null}}{{null}}{{it.{recursion_string}}}"
            return recurse_template.format(recursion_string=recursion_string)
        else:
            return recursion_string


class Backtrack(BasicBlock):
    """A block that specifies a return to a given BaseLocation in the query."""

    __slots__ = ("location", "optional")

    def __init__(self, location: BaseLocation, optional: bool = False) -> None:
        """Create a new Backtrack block, returning to the given location in the query.

        Args:
            location: BaseLocation object, specifying where to backtrack to
            optional: optional bool, specifying whether the steps between the current location
                      and the location to which Backtrack is returning were optional or not
        """
        super(Backtrack, self).__init__(location, optional=optional)
        self.location = location
        self.optional = optional
        self.validate()

    def validate(self) -> None:
        """Ensure that the Backtrack block is valid."""
        validate_marked_location(self.location)
        if not isinstance(self.optional, bool):
            raise TypeError(
                "Expected bool optional, got: {} {}".format(
                    type(self.optional).__name__, self.optional
                )
            )

    def to_gremlin(self) -> str:
        """Return a unicode object with the Gremlin representation of this BasicBlock."""
        self.validate()
        if self.optional:
            operation = "optional"
        else:
            operation = "back"

        mark_name, _ = self.location.get_location_name()

        return "{operation}({mark_name})".format(
            operation=operation, mark_name=safe_quoted_string(mark_name)
        )


class OutputSource(MarkerBlock):
    """A block that declares the output should have >= 1 row for each value at that location.

    This block, together with the @output_source directive that generates it,
    is a mitigation strategy that allows users to specify *which* set of results they want
    fully covered. Namely, OutputSource on a given location will ensure that all possible
    values at that location are represented in at least one row of the returned result set.

    See the comment on the @output_source directive in schema.py on why this is necessary.
    """

    __slots__ = ()

    def validate(self) -> None:
        """Validate the OutputSource block. An OutputSource block is always valid in isolation."""


class Fold(MarkerBlock):
    """A marker for the start of a @fold context."""

    __slots__ = ("fold_scope_location",)

    def __init__(self, fold_scope_location: FoldScopeLocation) -> None:
        """Create a new Fold block rooted at the given location."""
        super(Fold, self).__init__(fold_scope_location)
        self.fold_scope_location = fold_scope_location
        self.validate()

    def validate(self) -> None:
        """Ensure the Fold block is valid."""
        if not isinstance(self.fold_scope_location, FoldScopeLocation):
            raise TypeError(
                "Expected a FoldScopeLocation for fold_scope_location, got: {} "
                "{}".format(type(self.fold_scope_location), self.fold_scope_location)
            )


class Unfold(MarkerBlock):
    """A marker for the end of a @fold context."""

    __slots__ = ()

    def validate(self) -> None:
        """Unfold blocks are always valid in isolation."""


class EndOptional(MarkerBlock):
    """Marker for end of an @optional context.

    Optional scope is entered through an optional Traverse Block.
    """

    __slots__ = ()

    def validate(self) -> None:
        """In isolation, EndOptional blocks are always valid."""


class GlobalOperationsStart(MarkerBlock):
    """Marker block for the beginning of global operations.

    Global operations include, for example, various kinds of filters that affect more than one
    location in the query. Such filters are produced, e.g., as part of nested-optional processing,
    or when filters are applied to the "_x_count" meta property.
    """

    __slots__ = ()

    def validate(self) -> None:
        """In isolation, GlobalOperationsStart blocks are always valid."""
