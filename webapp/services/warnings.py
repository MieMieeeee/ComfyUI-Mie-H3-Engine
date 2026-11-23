"""渲染风险的分类：这条风险有多严重、叫什么、该怎么改。

契约（``mie_craft.script.contract``）给的是一串诊断原话，页面上要
显示的是一句短话、一个严重程度和一条能照着做的改法。这份对照表以前
放在浏览器里，于是「同一条风险在哪儿算严重」有两份真源。

现在对照表在这里，风险本身还是契约给的（``storyboard_warnings``）：
工作台不自己判断剧本有没有风险，只负责把契约给的诊断翻译成页面上
能读、能改的东西。装了带分类表的契约就用它的，没有就用自己的 ——
两边说的必须是同一件事，所以这里只认契约的**标记**，不认它的措辞。

三件事不许变：

* 严重程度：``err``（内容里确定存在的缺陷）> ``warn``（预算/形态
  风险）> ``info``（弱代理或只在特定条件下成立）。页面的排序和分组
  都按这个顺序。
* 兜底：认不出来的诊断仍然要有一句能看懂的话，不能没有标签，也不能
  没有级别 —— 认不出来的时候，页面说「渲染风险」，不装作知道。
* 改法：能算出来的（时长）直接改时长，要判断的（模型/画面）才交给
  模型重写。哪一条算哪一种，也在表里。
"""

from __future__ import annotations

import re

#: 分级，重的在前。
LEVELS = ("err", "warn", "info")

#: 分级 → 排序用的序号（小的在前）。
LEVEL_ORDER = {level: index for index, level in enumerate(LEVELS)}

#: 契约的风险标记 → (标签, 级别, 类别, 改法)
#:
#: 标签是页面上给作者看的那句短话；类别决定「按这条改」是改时长
#: 还是交给模型重写；改法是那句能照着做的建议 —— 光把诊断丢给模型，
#: 它不知道该动哪里：诊断说的是「哪里不够」，改法说的是「动什么」。
_META = (
    ("punctuation-only dialogue", "标点台词变提示音", "err", "model",
     "把这一句改写成有可发音内容的台词（至少留一个字），不能只留标点"),
    ("speech and action budget too tight", "慢速塞满会丢句", "warn", "duration",
     "把这一镜的时长加到装得下这些台词与动作，或者减掉一句台词；"
     "不要让最短的那句被丢掉"),
    ("shot budget at the floor", "预算贴地", "warn", "duration",
     "把这一镜的时长留出余量（比现在多出至少半秒），别让它压在最低预算上"),
    ("duration pinned at the feasibility floor", "目标时长零缓冲", "warn",
     "duration",
     "把目标时长提高约 1 秒，让每一镜都有缓冲"),
    ("single-speaker continuation clip", "单人续接缺陷入形", "warn", "model",
     "把这一镜拆成两镜，或者让画面里可见的角色接上这句台词"),
    ("long same-speaker burst", "连珠炮口型漂移", "warn", "model",
     "把同一角色连着说的几句拆散到不同片段（每段不超过 4 句），"
     "或者把收尾的反应拆成一个没有台词的第二镜"),
    ("shouting delivery", "喊叫语速偏慢", "info", "duration",
     "把目标时长加长一点，给喊叫的收尾留出 1 秒以上"),
    ("text-only crowds", "群像开场泄漏", "info", "model",
     "把没有台词的角色从这一镜里去掉，或者给他们各安排一个出场动作"),
)

#: 认不出来的诊断：仍然要给作者一句能看懂的话。
FALLBACK = ("渲染风险", "warn", "model", "按诊断把这一处改到没有风险")

#: 契约在诊断后面用「 · 」接上英文标记，分类就认这个标记。
_TAG = "·"

#: 分镜号：契约的诊断大多以分镜号开头（"scene_02 预算贴地：…"）。
_SHOT = re.compile(r"^(scene_\d+)\s")


def _contract_table():
    """共享契约里的分类表（有就用，没有就用自己的）。

    契约的形状认两种：一张 ``(标记, 标签, 级别, 类别, 改法)`` 的表，
    或者一个 ``{标记: (标签, 级别, 类别, 改法)}`` 的字典。装的契约
    没有这一项时，返回空 —— 工作台用自己的表，行为不变。
    """
    try:
        from mie_craft.script import contract
    except Exception:  # noqa: BLE001 - 没有契约就没有共享表
        return ()
    for name in ("WARNING_CLASSIFICATION", "WARNING_META"):
        table = getattr(contract, name, None)
        if isinstance(table, dict):
            rows = []
            for needle, meta in table.items():
                if isinstance(meta, (list, tuple)) and len(meta) >= 3:
                    rows.append((str(needle), str(meta[0]), str(meta[1]),
                                 str(meta[2]),
                                 str(meta[3]) if len(meta) > 3 else ""))
            if rows:
                return tuple(rows)
        if isinstance(table, (list, tuple)) and table:
            rows = []
            for row in table:
                if isinstance(row, (list, tuple)) and len(row) >= 4:
                    rows.append((str(row[0]), str(row[1]), str(row[2]),
                                 str(row[3]),
                                 str(row[4]) if len(row) > 4 else ""))
            if rows:
                return tuple(rows)
    return ()


def table() -> tuple:
    """这张对照表：优先用共享契约的，没有就用工作台自己的。"""
    return _contract_table() or _META


def source() -> str:
    """这份表是从哪儿来的：``mie_craft`` 或 ``workbench``。"""
    return "mie_craft" if _contract_table() else "workbench"


def _tag_of(text: str) -> str:
    """诊断末尾的英文标记。

    契约把标记放在最后，用「 · 」隔开：``… · punctuation-only
    dialogue renders as a notification chime``。
    """
    at = str(text).rfind(_TAG)
    return str(text)[at + 1:].strip() if at >= 0 else ""


def classify(text: str) -> dict:
    """一条诊断的完整分类。

    认不出标记时用兜底：标签、级别、类别、改法都还是有的 —— 页面
    不会因为一条新诊断就空出一块。
    """
    raw = str(text or "")
    tag = _tag_of(raw)
    meta = None
    for row in table():
        needle = row[0]
        if needle and needle in tag:
            meta = row
            break
    if meta is None:
        label, level, kind, fix = FALLBACK
    else:
        label, level, kind, fix = meta[1], meta[2], meta[3], meta[4]
    if level not in LEVEL_ORDER:
        level = FALLBACK[1]
    if kind not in ("model", "duration"):
        kind = FALLBACK[2]
    match = _SHOT.match(raw)
    return {
        "text": raw,
        "label": label,
        "level": level,
        "kind": kind,
        "fix": fix,
        "shot": match.group(1) if match else "",
    }


def classify_all(texts) -> list:
    """一批诊断的分类，按严重程度排，同一级别按分镜号排。"""
    items = [classify(text) for text in (texts or [])]
    return sorted(items, key=lambda item: (
        LEVEL_ORDER[item["level"]],
        item["shot"]))


def chips(items) -> list:
    """按标签分组的芯片：每一类一条，带这一类里最重的级别。

    页面的芯片顺序就是严重程度顺序：红的先说。
    """
    groups: dict = {}
    for item in items or []:
        group = groups.get(item["label"])
        if group is None:
            groups[item["label"]] = {"label": item["label"],
                                     "level": item["level"], "count": 1}
            continue
        group["count"] += 1
        if LEVEL_ORDER[item["level"]] < LEVEL_ORDER[group["level"]]:
            group["level"] = item["level"]
    return sorted(groups.values(),
                  key=lambda group: LEVEL_ORDER[group["level"]])


def summary(texts) -> dict:
    """校验回执里带着走的那份风险分类。"""
    items = classify_all(texts)
    return {
        "warnings": [str(text) for text in (texts or [])],
        "warning_details": items,
        "warning_chips": chips(items),
        "warning_levels": list(LEVELS),
        "warning_source": source(),
    }
