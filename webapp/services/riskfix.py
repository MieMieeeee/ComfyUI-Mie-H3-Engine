"""What to do about a render risk.

Some of the risks are arithmetic: a shot is three seconds short of
what its lines need at a slow delivery, and no amount of rewriting
will change that - the shot has to be longer, or one line has to go.
The app can work that out exactly, from the same numbers the contract
uses, and apply it: no model, no guessing, no waiting.

The others need a decision - split the shot, rewrite the line, take a
character out of the frame. Those are the model's job, and the app
hands it the diagnosis together with the way to fix it.
"""

from __future__ import annotations

from mie_craft.script import contract

#: The risks that are arithmetic: the shot is too short for its lines.
_DURATION_TAGS = (
    "speech and action budget too tight",
    "shot budget at the floor",
    "duration pinned at the feasibility floor",
    "shouting delivery",
)

#: The way to fix each kind that needs a decision. The diagnosis says
#: where it is short; this says what to move.
_FIXES = {
    "punctuation-only dialogue":
        "把这一句改写成有可发音内容的台词（至少留一个字），不能只留标点",
    "single-speaker continuation clip":
        "把这一镜拆成两镜，或者让画面里可见的角色接上这句台词",
    "long same-speaker burst":
        "把同一角色连着说的几句拆散到不同片段（每段不超过 4 句），"
        "或者把收尾的反应拆成一个没有台词的第二镜",
    "text-only crowds":
        "把没有台词的角色从这一镜里去掉，或者给他们各安排一个出场动作",
}


def _tag_of(warning: str) -> str:
    """The English marker at the end of a warning."""
    at = str(warning or "").rpartition("·")
    return at[2].strip() if at[0] else ""


def _fix_for(tag: str) -> str:
    for needle, fix in _FIXES.items():
        if needle in tag:
            return fix
    return "按诊断把这一处改到没有风险"


def classify(warning: str) -> str:
    """``duration`` when the app can compute the fix, ``model`` when it
    takes a decision."""
    tag = _tag_of(warning)
    for needle in _DURATION_TAGS:
        if needle in tag:
            return "duration"
    return "model"


def duration_fix(script: dict) -> dict:
    """How long the shots have to ask for, and how long the film has to
    be, for the tight ones to fit.

    Two numbers decide this, and both come from the contract: what a
    shot needs at a slow delivery, and how the budget is shared out.
    The budget is shared by weight - a shot asks for a length, and the
    longer it asks, the more of the film it gets. So a tight shot has
    to ask for more, and the film has to be long enough to give it.
    """
    asset = contract.validate_v2(script)
    shots = asset.get("shots") or []
    overlap = asset.get("continuation_overlap_frames", 22)
    needs = [contract.estimate_shot_seconds(
        shot, rate=5.5, beat_seconds=1.2, travel_seconds=2.0)
        for shot in shots]
    # 每镜申请多长：不够的补到够。契约的余量口径是 0.5 秒，
    # 这里照着来，不然改完那条风险还在。
    asks = [max(float(shot.get("duration_seconds") or 0), need + 0.6)
            for shot, need in zip(shots, needs)]
    target = max(float(asset.get("target_duration_seconds") or 0),
                 sum(asks) + (overlap * (len(shots) - 1) / 24.0) + 0.5)

    def frames_for(target_seconds, asks_now):
        candidate = dict(asset)
        candidate["target_duration_seconds"] = target_seconds
        candidate["shots"] = [dict(shot, duration_seconds=ask)
                              for shot, ask in zip(shots, asks_now)]
        return contract.allocate_frames(candidate)

    # 用契约自己的分配器验一遍：每一镜都要拿到够它的秒数。
    for _ in range(12):
        try:
            frames = frames_for(target, asks)
        except ValueError:
            target += 1.0
            continue
        fits = True
        for index, length in enumerate(frames):
            effective = (length - (overlap if index else 0)) / 24
            if effective < needs[index] + 0.5:
                fits = False
                break
        if fits:
            break
        target += 1.0

    return {
        "kind": "duration",
        "target_duration_seconds": round(target, 1),
        "shot_durations": [round(ask, 2) for ask in asks],
        "note": "把目标时长从 %s 秒加到 %s 秒，并把不够用的镜头改长："
                "台词与动作就装得下了。"
                % (asset.get("target_duration_seconds"), round(target, 1)),
    }


def model_request(script: dict, warning: str) -> dict:
    """The instruction the model gets: where it is, and what to move."""
    text = str(warning or "")
    advice = text.rpartition("·")[0].strip() or text
    tag = _tag_of(text)
    shot = ""
    if text.startswith("scene_"):
        shot = text.split(" ", 1)[0]
    return {
        "kind": "model",
        "request": ("按下面这条渲染风险改这一版，"
                    + "台词、角色、画风、接线都保持原样，只动问题所在的那一处：\n\n"
                    + (shot + "：" if shot else "") + advice + "\n\n"
                    + "改法：" + _fix_for(tag)),
    }


def plan(script: dict, warning: str) -> dict:
    """What to do about this warning.

    ``duration`` - the app can work it out and apply it itself.
    ``model``   - it takes a decision; the app hands the model the
    diagnosis and the way to fix it.
    """
    if classify(warning) == "duration":
        return duration_fix(script)
    return model_request(script, warning)
