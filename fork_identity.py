"""Fork identity: how this pack names its nodes.

Why a suffix at all
-------------------
ComfyUI registers node types in a plain dict and overwrites a duplicate
name silently - no error, no warning. The winner is decided by the order in
which the custom-node directories happen to be listed, which on Windows is
the filesystem's own directory order. Two packs that register the same type
name therefore make one of them run the other's implementation, quietly,
and which one wins is not something a user could predict.

So this pack registers every node type under a suffixed name. The upstream
pack and this one can then be installed side by side, and a workflow says
which pack it means.

Reading what was written earlier
--------------------------------
Saved prompts, run manifests and workflow files written before the rename
still carry the upstream type names, and a workflow may also be executed
against the upstream pack when both are installed. The code that inspects
those documents therefore accepts BOTH names, via :func:`type_aliases`.

The map itself is generated, never hand-written - see
``tools/gen_rename_map.py``.
"""

NODE_TYPE_SUFFIX = "|MieH3"

#: Marker appended to every display name so the fork is visible in the
#: node menu without changing the upstream wording.
DISPLAY_NAME_SUFFIX = " (MieH3)"


def add_suffix(name: str) -> str:
    """Return the type name this pack registers."""
    return name + NODE_TYPE_SUFFIX


def type_aliases(name: str) -> tuple[str, ...]:
    """Return the names a document may use for one node type.

    The first is this pack's name; the second is the upstream name, which
    still appears in prompts, manifests and workflows written before the
    rename, and in workflows executed against the upstream pack.
    """
    return (name + NODE_TYPE_SUFFIX, name)


def type_alias_set(*names: str) -> frozenset[str]:
    """Return every name any of the given node types may appear under."""
    aliases: set[str] = set()
    for name in names:
        aliases.update(type_aliases(name))
    return frozenset(aliases)


def display_name(name: str) -> str:
    """Return a display name that marks the fork."""
    return name + DISPLAY_NAME_SUFFIX
