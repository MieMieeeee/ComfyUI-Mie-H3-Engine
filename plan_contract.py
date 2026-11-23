"""The plan contract, in one place.

The engine's chain plan and the authoring package's plan are the SAME
document in two prompt dialects:

* the engine's chain plan accepts a plain prompt string per shot, and
  the whole document may be a bare list of shots - that is what a
  hand-written plan and the workbench's own fallback plan look like;
* the authoring package's plan is a SECTIONED prompt: an array of
  lines that starts with ``integrated_multimodal_description:`` and
  ends with ``non_diegetic_music:``.

Those dialects are not the same thing, so the section rules stay where
they are (``mie_craft.h3_loop_prompts.validate_plan``): moving them
into the engine would refuse every plain-string plan the engine
accepts today, and moving the engine's plain strings into the authoring
package would refuse every plan the workbench assembles.

What the two DO share is the shape of the document: a JSON object with
a ``shots`` list of 1..128 entries. That rule lives in the authoring
package (``shot_list_errors``), and the engine calls it - so the two
sides cannot drift apart. A local fallback covers an installation whose
``mie_craft`` is too old to have the helper: the engine never demands a
version it does not need, and the fallback says the same thing in the
same words, so the message does not depend on which version happens to
be installed.
"""

from __future__ import annotations

#: The shot count both sides agree on. When the authoring package is
#: installed, ITS numbers are the ones that are used - this pair is the
#: fallback for an installation that does not have the helper.
MIN_SHOTS = 1
MAX_SHOTS = 128


def craft_shot_list_errors():
    """The shared rule, from the authoring package.

    ``None`` when the installed ``mie_craft`` does not have the helper
    (an older version) or is not installed at all.
    """
    try:
        from mie_craft.h3_loop_prompts import shot_list_errors
    except ImportError:
        return None
    return shot_list_errors


def shot_list_errors(raw) -> list:
    """The shared shot-list rule: a JSON object with 1..128 shots.

    The supported path is the authoring package's own helper, so the
    engine and the package cannot drift apart. The fallback is the same
    rule, in the same words: a plan that is refused here is refused
    there for the same reason.
    """
    helper = craft_shot_list_errors()
    if helper is not None:
        return list(helper(raw))
    return _local_shot_list_errors(raw)


def _local_shot_list_errors(raw) -> list:
    """The same rule, for an installation without the helper."""
    if not isinstance(raw, dict):
        return ["plan must be a JSON object"]
    shots = raw.get("shots")
    if not isinstance(shots, list) or not (MIN_SHOTS <= len(shots) <= MAX_SHOTS):
        return ["shots must be a list of %d..%d entries"
                % (MIN_SHOTS, MAX_SHOTS)]
    return []
