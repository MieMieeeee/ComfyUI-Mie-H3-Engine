import pathlib

p = pathlib.Path("PLAN.md")
s = p.read_text(encoding="utf-8")
bar = chr(92) + "|"          # a literal backslash-pipe, for markdown tables

edits = []

edits.append((
    "### 4.2 命名方案\n\n沿用 MieNodes 既有约定",
    "### 4.2 命名方案\n\n"
    "**实施后补记（2026-11-06）**：后缀在**模块级**施加 —— 每个模块的\n"
    "`*_NODE_CLASS_MAPPINGS` / `*_NODE_DISPLAY_NAME_MAPPINGS` 各自加后缀，\n"
    "而不是只在 `__init__.py` 的聚合处加。\n\n"
    "原因是聚合处加会留下**两个名字空间**：模块字典的键是旧名，ComfyUI 注册的是新名，\n"
    "上游自己的测试大量直接查模块字典 ⇒ 23 个测试当场变红。\n"
    "模块级加则**全仓只有一个名字空间**：模块字典里的键 = ComfyUI 注册的名字\n"
    "= 工作流/提示词里出现的名字。\n\n"
    "沿用 MieNodes 既有约定"))

edits.append((
    "**完整的改名范围：**",
    "**实施后补记（2026-11-06）—— 改名有两个工具查不出来的陷阱：**\n\n"
    "1. **JS 里的类型索引表用的是裸标识符键**（`MiniMaxH3ChainPlan: [...]`），\n"
    "   不是引号字面量 ⇒ 只扫引号字面量会漏掉它们，表现为“前端拿不到高级输出名”。\n"
    "   而**裸键装不下 `|`**（不是合法标识符字符）⇒ 改名时必须**把键加上引号**。\n"
    "2. **有些地方出现的旧名，语义上就是“历史名”**：退役检测（`removed` 集合）\n"
    "   与旧版检测（`legacy` 集合）里的名字**必须保持旧名**，改了就把那条检查改坏了。\n"
    "   ⇒ 改名工具需要一张**例外表**。\n\n"
    "**完整的改名范围：**"))

edits.append((
    "| Phase 2 改名 | ⬜ 未做 | **现在升级为必须项**（§4.1.1） |",
    "| Phase 2 改名 | ✅ **已做** | 86 个类型加 `" + bar + "MieH3`；模块级施加（全仓一个名字空间）；"
    "新增 `fork_identity.py`、`tools/gen_rename_map.py`（自动生成 + `--check`）、"
    "`tools/apply_rename.py`、`tools/migrate_workflows.py`；"
    "**本机实测：两包同装，148（上游）+ 86（我们）= 234，零冲突** ✓ |"))

edits.append((
    "| R2 | 87 个类型批量改名漏改 | 节点注册不全，工作流红 | 改名表**自动生成** + CI 断言条目数相等 + **改名范围含 `tests/**` 与 baseline json** |",
    "| R2 | 批量改名漏改 | 节点注册不全，工作流红 | 改名表**自动生成**（AST 扫 13 个模块，86 条）+ "
    "CI 断言条目数相等 + **改名范围含 `tests/**` 与 baseline json**。"
    "**实施后补记**：还有两个工具查不出来的陷阱——JS 的**裸标识符键**（须加引号，"
    "`|` 不是合法标识符字符）与**语义上的历史名**（退役/旧版检测集合，须保持旧名） |"))

for old, new in edits:
    if old not in s:
        raise SystemExit("anchor not found: " + old[:70])
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("PLAN.md updated: %d edits" % len(edits))
