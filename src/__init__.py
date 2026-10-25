import typing

try:
    import sqlalchemy.util.typing

    def _fixed_make_union_type(*types):
        if not types:
            return typing.Any
        if len(types) == 1:
            return types[0]
        return typing.Union[types]

    sqlalchemy.util.typing.make_union_type = _fixed_make_union_type
except ImportError:
    pass
