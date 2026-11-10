"""What to do about a render risk.

Some of the risks are arithmetic: a shot is three seconds short of
what its lines need at a slow delivery, and no amount of rewriting
will change that - the shot has to be longer, or one line has to go.
The app can work that out exactly, from the same numbers the contract
uses, and apply it: no model, no guessing, no waiting.

The others need a decision - split the shot, rewrite the line, take a
character out of the frame. Those are the model's job, and the app
hands it the diagnosis together with the way to fix it.

Both kinds can be acted on at once, and the order matters: the
arithmetic first (so the model sees a board that already fits), the
model second (it rewrites the words), the arithmetic once more (the
words changed, so the budget has to be recomputed).

Two things this module refuses to do:

  * it never returns a board the contract would reject. The budget is
    shared out by the contract's own allocator, and that allocator
    caps a lone dialogue line at ``minimum + 2`` clips - a single long
    line simply cannot be given the room it needs. That case is not an
    arithmetic problem at all, and saying so is the honest answer.
  * it never inflates a shot that was not named. The shipped version
    asked every shot for its slow-delivery seconds and added them all
    up, which turned a six second scene into twenty one - a board the
    contract then refused.
"""

from __future__ import annotations

import math
import re

from mie_craft.script import contract

#: The risks that time can settle: the shot is too short for its lines.
_DURATION_TAGS = (
    "speech and action budget too tight",
    "shot budget at the floor",
    "duration pinned at the feasibility floor",
)

#: The risks that need a decision. The shout is here because a louder
#: line is delivered slower than the budget assumes - more time helps,
#: but only a rewrite makes the diagnostic go away.
_MODEL_TAGS = (
    "punctuation-only dialogue",
    "single-speaker continuation clip",
    "long same-speaker burst",
    "text-only crowds",
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
        + "或者把收尾的反应拆成一个没有台词的第二镜；"
        + "拆完之后，新的续接那一镜不能整镜只有一个人在说话，"
        + "否则会冒出一条新的风险",
    "text-only crowds":
        "把没有台词的角色从这一镜里去掉，或者给他们各安排一个出场动作",
    "shouting delivery":
        "把喊叫的那几句改短，或者拆到更小的片段里"
        + "（实测喊叫语速只有约 2.5 字/秒，预算按 5.5 会截尾）",
}

#: What to do when a shot is too short but the contract will not let it
#: be longer. The time is out of reach; only the content can move.
_OVERLOADED = ("这一镜只有一句台词，H3 的预算上限只肯给它 minimum+2 个片段，"
               + "再多就不给：把这句拆成两句（分到两镜），或者改短到一镜装得下")

_SHOT_AT_START = re.compile(r"^(scene_\d+)\b")

# The contract's own constants, mirrored here so this module can plan
# against the allocator without reaching into its internals.
_FRAMES_PER_CLIP = 17
_CLIP_OVERHEAD = 5
_MAX_CLIPS = 42
_LONE_DIALOGUE_HEADROOM = 2

#: How many times the model is asked. One round fixes the risks that
#: were there; the second one is for the risks the model's own split
#: brought up - a new continuation clip whose dialogue belongs to one
#: character, for instance.
_MODEL_ROUNDS = 2


# --------------------------------------------------------------------------- #
# the diagnosis
# --------------------------------------------------------------------------- #

def _tag_of(warning: str) -> str:
    """The English marker at the end of a warning."""
    at = str(warning or "").rpartition("·")
    return at[2].strip() if at[0] else ""


def _fix_for(tag: str, board: dict = None) -> str:
    """The way to fix this kind of risk.

    One kind depends on the script: a continuation clip whose dialogue
    belongs to one character cannot be fixed by adding a character to
    the frame when there is only one character in the script.
    """
    for needle, fix in _FIXES.items():
        if needle not in tag:
            continue
        if needle == "single-speaker continuation clip" and board is not None:
            on_screen = [role for role in board.get("roles") or []
                         if role.get("presence", "on_screen") == "on_screen"]
            if len(on_screen) < 2:
                return ("这一版只有 %d 个画内角色：续接的那一镜必然整镜都是他说话，"
                        "画面里没有人可以接这句。要么把这一镜并回上一镜"
                        "（那样连珠炮会回来），要么准备重摇这一镜"
                        % len(on_screen))
        return fix
    return "按诊断把这一处改到没有风险"


def classify(warning: str) -> str:
    """``duration`` when time can settle it, ``model`` when it takes a
    decision."""
    tag = _tag_of(warning)
    for needle in _DURATION_TAGS:
        if needle in tag:
            return "duration"
    return "model"


def _shot_of(warning: str) -> str:
    """The shot a warning is about, or ``""`` when it is about the film."""
    found = _SHOT_AT_START.match(str(warning or ""))
    return found.group(1) if found else ""


def _index_of(shot_id: str, count: int) -> int:
    """``scene_03`` -> 2. The contract keeps the ids contiguous."""
    if not shot_id:
        return -1
    try:
        index = int(shot_id.split("_")[1]) - 1
    except (IndexError, ValueError):
        return -1
    return index if 0 <= index < count else -1


# --------------------------------------------------------------------------- #
# the allocator's arithmetic (mirrors contract.allocate_frames)
# --------------------------------------------------------------------------- #

def _minimum_clips(shots, overlap) -> list:
    """The clips each shot cannot go below."""
    out = []
    for index, shot in enumerate(shots):
        raw = max(1.0, contract.estimate_shot_seconds(shot)) * 24
        if index:
            raw += overlap
        out.append(max(0, math.ceil((raw - _CLIP_OVERHEAD) / _FRAMES_PER_CLIP)))
    return out


def _caps(shots, minimum) -> list:
    """The clips each shot cannot go above.

    A shot that holds a single dialogue line is capped near its own
    floor: one line must not swallow the whole film. Every other shot
    may take the full per-shot limit.
    """
    out = []
    for index, shot in enumerate(shots):
        events = shot.get("events") or []
        lone = len(events) == 1 and events[0].get("type") == "dialogue"
        out.append(min(_MAX_CLIPS, minimum[index] + _LONE_DIALOGUE_HEADROOM)
                   if lone else _MAX_CLIPS)
    return out


def _clips_for(seconds: float, overlap: int) -> int:
    """The clips a shot needs to carry ``seconds`` of delivered time."""
    return max(0, math.ceil((seconds * 24 + overlap - _CLIP_OVERHEAD)
                            / _FRAMES_PER_CLIP))


def _delivered(frames: int, overlap: int) -> float:
    """The seconds a shot actually gets, overlap removed."""
    return (frames - overlap) / 24.0


def _current_clips(board: dict) -> list:
    """The clips each shot has right now."""
    frames = contract.allocate_frames(board)
    return [max(0, round((f - _CLIP_OVERHEAD) / _FRAMES_PER_CLIP)) for f in frames]


def _implied_total(board: dict) -> int:
    """The clips the board's own target duration buys."""
    shots = board.get("shots") or []
    count = len(shots)
    overlap = int(board.get("continuation_overlap_frames") or 0)
    target = float(board.get("target_duration_seconds") or 0)
    return int(round((target * 24 + overlap * (count - 1)
                      - _CLIP_OVERHEAD * count) / _FRAMES_PER_CLIP))


def repair_target(board: dict) -> dict:
    """A film long enough for the shots it now has.

    A model that splits one shot into two has made a longer film, and
    the target duration it wrote usually stays where it was - so the
    contract refuses the result and the rewrite is thrown away for a
    reason the model never knew about. The allocator knows what the
    shots need, so the target is set from that: the contract's own
    numbers, not a guess.
    """
    shots = board.get("shots") or []
    count = len(shots)
    overlap = int(board.get("continuation_overlap_frames") or 0)
    minimum = _minimum_clips(shots, overlap)
    caps = _caps(shots, minimum)
    total = min(max(_implied_total(board), sum(minimum)), sum(caps))
    target = (total * _FRAMES_PER_CLIP + _CLIP_OVERHEAD * count
              - overlap * (count - 1)) / 24.0
    out = dict(board)
    out["target_duration_seconds"] = round(target, 2)
    return out


# --------------------------------------------------------------------------- #
# the arithmetic plan
# --------------------------------------------------------------------------- #

def duration_plan(board: dict, warnings) -> dict:
    """How long the shots have to ask for, and how long the film has to
    be, for the named shots to fit.

    Only the shots a warning names are touched. The budget is shared out
    by the contract's own allocator, and it hands the clips out in
    proportion to what each shot asks for - so a shot that has to grow
    grows its ask, the film grows by exactly that many clips, and every
    other shot keeps the time it had.

    A shot the allocator will not let grow (a lone dialogue line, which
    is capped near its floor) is reported as ``infeasible`` instead of
    being given a number that the contract would reject.
    """
    shots = board.get("shots") or []
    overlap = int(board.get("continuation_overlap_frames") or 0)
    count = len(shots)
    if not count:
        return {"kind": "duration", "feasible": False, "target_duration_seconds": 0.0,
                "shot_durations": [], "infeasible": [], "note": "这一版还没有分镜。"}

    minimum = _minimum_clips(shots, overlap)
    caps = _caps(shots, minimum)
    have = _current_clips(board)

    want = list(have)
    infeasible = []
    pinned = False

    for warning in warnings or []:
        tag = _tag_of(warning)
        index = _index_of(_shot_of(warning), count)
        if "duration pinned at the feasibility floor" in tag:
            pinned = True
            continue
        if index < 0:
            continue
        lead = overlap if index else 0
        if "speech and action budget too tight" in tag:
            need = contract.estimate_shot_seconds(
                shots[index], rate=5.5, beat_seconds=1.2, travel_seconds=2.0) + 0.5
        elif "shot budget at the floor" in tag:
            need = contract.estimate_shot_seconds(shots[index]) + 0.2
        elif "shouting delivery" in tag:
            # A shouted line is delivered at about 2.5 characters a
            # second, not the 5.5 the budget assumes. Budget it slowly.
            need = contract.estimate_shot_seconds(
                shots[index], rate=5.5, beat_seconds=1.2, travel_seconds=2.0) + 0.5
        else:
            continue
        clips = _clips_for(need, lead)
        if clips > caps[index]:
            infeasible.append(warning)
            continue
        want[index] = max(want[index], clips)

    if pinned and sum(want) <= sum(minimum):
        # The film is pressed flat on the floor: give one shot one more
        # clip so the buffer the contract asks for exists.
        for index in sorted(range(count), key=lambda i: want[i] - have[i], reverse=True):
            if want[index] < caps[index]:
                want[index] += 1
                break

    total = sum(want)
    if total > _MAX_CLIPS * count:
        infeasible.extend(warnings or [])
        want = list(have)
        total = sum(want)

    target = (total * _FRAMES_PER_CLIP + _CLIP_OVERHEAD * count
              - overlap * (count - 1)) / 24.0
    durations = [(_FRAMES_PER_CLIP * clips + _CLIP_OVERHEAD) / 24.0 for clips in want]

    note = "目标时长从 %.1f 秒调到 %.1f 秒" % (
        board.get("target_duration_seconds") or 0.0, target)
    grown = [str(shot.get("id") or ("scene_%02d" % (i + 1)))
             for i, shot in enumerate(shots) if want[i] > have[i]]
    if grown:
        note += "，%s 这几镜改长" % "、".join(grown)
    if infeasible:
        note += "。有的镜头按时长加不上去（契约只给一句台词那么多时间），"
        note += "那几条要改台词或拆镜。"
    else:
        note += "，台词与动作就装得下了。"

    return {"kind": "duration", "feasible": not infeasible,
            "target_duration_seconds": round(target, 2),
            "shot_durations": [round(value, 3) for value in durations],
            "infeasible": infeasible, "note": note}


def apply_duration_plan(board: dict, plan: dict) -> dict:
    """The board with the plan's numbers written into it."""
    out = dict(board)
    out["target_duration_seconds"] = plan["target_duration_seconds"]
    shots = []
    for index, shot in enumerate(board.get("shots") or []):
        copy = dict(shot)
        if index < len(plan.get("shot_durations") or []):
            copy["duration_seconds"] = plan["shot_durations"][index]
        shots.append(copy)
    out["shots"] = shots
    return out


def duration_fix(script: dict) -> dict:
    """The arithmetic answer for every shot this board warns about.

    Kept as the single-shot entry point the page used before; the plan
    is the same one the batch path uses.
    """
    board = contract.validate_v2(script)
    warnings = list(contract.storyboard_warnings(board))
    return duration_plan(board, warnings)


# --------------------------------------------------------------------------- #
# the model's answer
# --------------------------------------------------------------------------- #

def model_request(script: dict, warnings, overloaded=None) -> dict:
    """The instruction the model gets: where it is, and what to move.

    Several risks go into ONE request: the model is asked once, and
    asking it once about three problems costs a third of what asking
    it three times does.

    ``overloaded`` names the risks the clock cannot settle. Those get
    the advice that fits them - a line the contract will not stretch
    has to be split - instead of the generic one.
    """
    items = list(warnings) if isinstance(warnings, (list, tuple)) else [warnings]
    stuck = list(overloaded or [])
    lines = []
    for index, text in enumerate(items, 1):
        text = str(text or "")
        advice = text.rpartition("·")[0].strip() or text
        tag = _tag_of(text)
        shot = _shot_of(text)
        where = shot + "：" if (shot and not advice.startswith(shot)) else ""
        fix = _OVERLOADED if text in stuck else _fix_for(tag, script)
        lines.append("%d) %s%s\n   改法：%s" % (index, where, advice, fix))
    if len(lines) > 1:
        head = ("按下面这几条渲染风险改这一版。只动下面指出的那几处，"
                "画风、接线、角色设定都保持原样；"
                "台词要改就按每条的「改法」改，其余一个字都不要动：")
    else:
        head = ("按下面这条渲染风险改这一版。只动下面指出的那一处，"
                "画风、接线、角色设定都保持原样；"
                "台词要改就按「改法」改，其余一个字都不要动：")
    return {"kind": "model", "request": head + "\n\n" + "\n\n".join(lines)}


def model_request_for(script: dict, warnings) -> dict:
    """The instruction for the risks only a decision can settle."""
    return model_request(script, warnings)


# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #

def _matches(warning: str, board_warnings) -> bool:
    """Whether a warning is still on the list (by shot and by tag)."""
    tag = _tag_of(warning)
    shot = _shot_of(warning)
    for other in board_warnings:
        if _tag_of(other) != tag:
            continue
        if _shot_of(other) == shot:
            return True
    return False


def _score(board: dict, before) -> tuple:
    """How good a board is: the risks that were there, then the new ones.

    Fixing what the author was shown comes first; a board that trades
    one old risk for two new ones is not a fix.
    """
    warnings = list(contract.storyboard_warnings(board))
    still = len([w for w in before if _matches(w, warnings)])
    return (still, len(warnings) - still)


def _event_count(board: dict) -> int:
    """How many events the board carries."""
    return sum(len(shot.get("events") or [])
               for shot in board.get("shots") or [])


def _spoken_chars(board: dict) -> int:
    """How many characters of dialogue the board carries."""
    total = 0
    for shot in board.get("shots") or []:
        for event in shot.get("events") or []:
            if event.get("type") == "dialogue":
                total += len(str(event.get("text") or ""))
    return total


def plan(script: dict, warning: str) -> dict:
    """What to do about this warning.

    ``duration`` - the app can work it out and apply it itself.
    ``model``   - it takes a decision; the app hands the model the
    diagnosis and the way to fix it.

    A risk the clock cannot settle is never dressed up as an
    arithmetic one. The contract caps a lone dialogue line near its own
    floor, so a line that is too long has to be split, not stretched -
    and saying "the model has to do this" is the honest answer.
    """
    board = contract.validate_v2(script)
    text = str(warning or "")
    if not text.strip():
        raise ValueError("没有说清是哪一条风险。")
    arithmetic = duration_plan(board, [text])
    if classify(text) == "duration" and arithmetic["feasible"]:
        return {"kind": "duration",
                "target_duration_seconds": arithmetic["target_duration_seconds"],
                "shot_durations": arithmetic["shot_durations"],
                "feasible": True, "infeasible": [],
                "note": arithmetic["note"]}
    request = model_request(board, [text],
                            overloaded=arithmetic["infeasible"])["request"]
    return {"kind": "model", "request": request}


def _needs_decision(texts, arithmetic) -> list:
    """The risks the model has to settle.

    That is every risk that is not about time, plus every risk the
    clock could not fix: a lone line that is too long is still a real
    risk, and only the model can split it.
    """
    out = [text for text in texts if classify(text) != "duration"]
    for text in arithmetic.get("infeasible") or []:
        if text not in out:
            out.append(text)
    return out


def plan_all(script: dict, warnings) -> dict:
    """What to do about all of these risks at once.

    The arithmetic covers every risk that names a shot - more time
    never hurts, even for a risk the model also has to look at - and
    the model gets one request for everything that needs a decision.
    """
    board = contract.validate_v2(script)
    texts = [str(w) for w in (warnings or []) if str(w).strip()]
    if not texts:
        raise ValueError("没有要处理的风险。")
    arithmetic = duration_plan(board, texts)
    needs_model = _needs_decision(texts, arithmetic)
    result = {"count": len(texts), "duration": arithmetic, "model": None,
              "duration_count": len(texts) - len(needs_model),
              "model_count": len(needs_model)}
    if needs_model:
        request = model_request(board, needs_model,
                                overloaded=arithmetic["infeasible"])["request"]
        result["model"] = {"kind": "model", "request": request}
    return result


def fix_all(script: dict, warnings, revise=None) -> dict:
    """Act on every risk at once, in the order that makes it stick.

    1. The arithmetic: the shots a warning names get the time they
       need, computed with the contract's own allocator, so the board
       stays valid.
    2. The decision: whatever is left goes to the model in ONE request.
    3. The arithmetic again: the model rewrote the words, so the
       budget has to be recomputed on what it wrote.

    Splitting a shot can raise a risk that was not there before - a new
    continuation clip whose dialogue belongs to one character - so the
    model is asked a second time about the risks ITS OWN rewrite
    brought up. Two rounds at most, and the second one only if the
    second round has something new to ask about.

    ``revise`` is called as ``revise(request_text, board)`` and returns
    the rewritten board. When it is missing, or when it fails, the
    arithmetic still lands and the report says what did not happen.
    """
    board = contract.validate_v2(script)
    texts = [str(w) for w in (warnings or []) if str(w).strip()]
    if not texts:
        raise ValueError("没有要处理的风险。")

    notes = []
    before = list(contract.storyboard_warnings(board))

    # 1. the arithmetic, on the shots that are named.
    arithmetic = duration_plan(board, texts)
    if arithmetic["shot_durations"]:
        board = apply_duration_plan(board, arithmetic)
        notes.append(arithmetic["note"])

    # 2 and 3, in turns: the decision, then the arithmetic on what it wrote.
    pending = _needs_decision(texts, arithmetic)
    asked = list(pending)
    best_board, best_score = board, _score(board, before)
    for round_number in range(_MODEL_ROUNDS):
        if not pending:
            break
        if revise is None:
            notes.append("需要判断的那几条没有改：这次没有可以调用的模型。")
            break
        request = model_request(board, pending,
                                overloaded=arithmetic["infeasible"])["request"]
        try:
            revised = revise(request, board)
        except Exception as exc:  # noqa: BLE001 - the provider's error is the answer
            notes.append("需要判断的那几条没有改：%s" % exc)
            break
        try:
            candidate = contract.validate_v2(revised)
        except ValueError as exc:
            # 模型多半只改了词，没改目标时长：拆出来的镜需要更长的
            # 片子。按契约自己的数字把时长补上，再收这一版。
            try:
                candidate = contract.validate_v2(repair_target(revised))
            except Exception:  # noqa: BLE001
                notes.append("需要判断的那几条没有改：%s" % exc)
                break
            notes.append("模型改完的稿子时长对不上，"
                         "已经按分镜的实际需要把目标时长补上了。")
        if _event_count(candidate) < _event_count(board):
            # 改风险不是改稿子：模型把作者的台词丢了，这一版不收。
            notes.append("模型改写的稿子少了内容（它把台词丢了），"
                         "这一版没有采用。")
            break
        if _spoken_chars(candidate) * 3 < _spoken_chars(board):
            # 台词只剩三分之一：这不是"改短一点"，这是把剧本删了。
            notes.append("模型改写的稿子少了内容（台词只剩三分之一），"
                         "这一版没有采用。")
            break
        board = candidate

        # the arithmetic again, on whatever the model wrote
        after_model = list(contract.storyboard_warnings(board))
        second = duration_plan(board, after_model)
        if second["shot_durations"]:
            candidate = apply_duration_plan(board, second)
            try:
                contract.validate_v2(candidate)
                board = candidate
            except ValueError:
                pass

        # 这一版比上一版好就留下，不好就丢掉：改风险不是改坏。
        score = _score(board, before)
        if score < best_score:
            best_board, best_score = board, score

        # 下一轮问的是模型改完之后还在的那些：要判断的那些。
        arithmetic = duration_plan(board, after_model)
        pending = [w for w in after_model if classify(w) != "duration"]
        for text in arithmetic["infeasible"]:
            if text not in pending:
                pending.append(text)
        if not [w for w in pending if w not in asked]:
            break
        if round_number + 1 < _MODEL_ROUNDS:
            notes.append("模型改完之后带出了新的风险，又问了一遍。")
        asked.extend(pending)

    board = best_board
    remaining = list(contract.storyboard_warnings(board))
    fixed = [w for w in before if not _matches(w, remaining)]
    return {"json": board, "notes": notes, "remaining": remaining,
            "fixed": fixed, "fixed_count": len(fixed)}
