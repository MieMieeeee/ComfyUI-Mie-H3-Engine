# Plan: H3 Engine & Short-Drama Workbench Remediation and High-Leverage Enhancements

Date: 2026-11-23
Repository: `F:\ComfyUI-Mie-H3-Engine`

## Mandatory Execution Constraints (for Codex CLI)

1. **Encoding Safety (CRITICAL on Windows)**:
   - This repository contains UTF-8 files with Chinese strings and comments.
   - **NEVER** read or write repository files using PowerShell text redirection (`Get-Content`, `Set-Content`, `Out-File`, `cat >`) without explicit UTF-8 encoding, as Windows default codepage (GBK) will corrupt Chinese characters into mojibake.
   - Always use native file read/edit tools or Python scripts with explicit `encoding="utf-8"` (without BOM for source code files).
   - After every edit, inspect `git diff` on the touched lines to confirm zero mojibake.
2. **Test & Environment Discipline**:
   - **Do NOT** create any virtualenv (`.venv`, `venv`, etc.) and **do NOT** run `pip install`.
   - Always pass `--basetemp .pytest_tmp_codex -p no:cacheprovider` when running `pytest` so temporary files stay inside the writable workspace root.
   - Clean up `.pytest_tmp_codex` before finishing so `git status` leaves no untracked test litter.
   - Verification commands to run:
     - `python -m pytest webapp_tests -q --ignore=webapp_tests/test_ui_browser.py --ignore=webapp_tests/test_e2e_webui.py --basetemp .pytest_tmp_codex -p no:cacheprovider`
     - `python tests/_mie_plan_preview_unit_test.py`
     - `python tests/_mie_checkpoint_identity_unit_test.py`
     - `python tests/_mie_mtime_selection_unit_test.py`
     - `python tests/_mie_png_verification_strict_unit_test.py`
3. **Scope**:
   - Only modify files required by the batches below and their corresponding tests in `webapp_tests/` and `tests/`. Do not touch untracked user notes in `notes/`.

---

## Batch 1: P0 Functional & Isolation Fixes

### 1.1 Fix `/llm` Page API Key & Local Host Saving + Display (`webapp/templates/llm.html`, `webapp/static/app.js`)

**Root Cause**:
- `webapp/static/app.js` (`saveKey` at L252 and `saveHost` at L274) queries `document.querySelector('input[data-key="' + platform.key + '"]')` and `document.querySelector('input[data-host="' + platform.key + '"]')`.
- `webapp/templates/llm.html` (L67–80) omits `data-key` and `data-host` on both `<input>` elements, causing `querySelector` to return `null` and every save to send `""` (wiping keys/hosts while reporting `"已保存"`).
- `webapp/templates/llm.html` L68 binds `:value="p.host || 'http://127.0.0.1:11434'"`, where `p.host` is a boolean (`true`), rendering `"true"` instead of the actual host URL (`p.endpoint`).
- `webapp/templates/llm.html` L76 binds `:value="p.has_key ? p.key_masked : ''"` on `<input type="password">`, risking submitting the masked display string (`sk-xxx…xxxx (51)`) as the real secret.
- `webapp/templates/llm.html` L18 has a typo: `<code>mia_llm_keys.json</code>` instead of `<code>mie_llm_keys.json</code>`.

**Changes Required**:
1. In `webapp/templates/llm.html`:
   - L18: Change `<code>mia_llm_keys.json</code>` to `<code>mie_llm_keys.json</code>`.
   - L67–71 (`p.host` template):
     ```html
     <template x-if="p.host">
       <input class="input" :data-host="p.key"
              :value="p.endpoint || 'http://127.0.0.1:11434'"
              :aria-label="p.name + ' 的本地服务地址'"
              @change="saveHost(p, $event.target.value)" placeholder="http://host:port">
     </template>
     ```
   - L72–80 (`!p.host` template):
     ```html
     <template x-if="!p.host">
       <div>
         <div class="small" :class="p.has_key ? 'ok' : 'warn'"
              x-text="p.has_key ? p.key_masked : '未设置'"></div>
         <input class="input" type="password" :data-key="p.key"
                :aria-label="p.name + ' 的 API Key'"
                @change="saveKey(p, $event.target.value); $event.target.value = ''"
                :placeholder="p.has_key ? '已设置（粘贴新 Key 覆盖）' : '粘贴 API Key'">
       </div>
     </template>
     ```
2. In `webapp/static/app.js`:
   - Update `saveKey(platform, rawValue)` (L252): if `typeof rawValue === "string"` use `rawValue`, otherwise read `document.querySelector('input[data-key="' + platform.key + '"]')`.
   - Update `saveHost(platform, rawValue)` (L274): if `typeof rawValue === "string"` use `rawValue`, otherwise read `document.querySelector('input[data-host="' + platform.key + '"]')`; after `api.post("/api/llm/host", ...)`, update `platform.endpoint = result.host || host`.

### 1.2 Allow Keyless Local LLM Platforms (`host: True`, e.g. Ollama) in `autodraft._connector()` (`webapp/services/autodraft.py`)

**Root Cause**:
- `webapp/services/autodraft.py` L34–44 (`_connector()`) checks `if not llm.read_keys().get(key): raise ValueError(...)` before checking whether the selected platform is a local host (`row.get("host")` / `row.get("needs_key") is False`).

**Changes Required**:
- In `webapp/services/autodraft.py` `_connector()`:
  ```python
  def _connector():
      """The current platform's connector, with the key pre-checked."""
      key = llm.current_platform()
      if not key:
          raise ValueError("没有选择当前大模型平台：先到大模型页选一个平台并配置 Key")
      row = next((r for r in llm.list_platforms() if r["key"] == key), None)
      if row is None:
          raise ValueError("当前平台 %s 不在平台列表里" % key)
      if not row.get("host") and not llm.read_keys().get(key):
          raise ValueError("平台 %s 还没有配置 API Key：先到大模型页设置" % key)
      return llm.build_connector(row)
  ```

### 1.3 Fix Narrow Viewport (`<= 640px`) CSS Hiding `/assets` List & `/video` Form (`webapp/static/app.css`)

**Root Cause**:
- `webapp/static/app.css` L657–660 defines `@media (max-width: 640px) { .narrow-picker { display: block; } .split-list { display: none; } }`.
- Only `/scripts` has `.narrow-picker` immediately preceding `.split-list`. On `/assets` and `/video`, `.split-list` holds the entire asset list or the entire video submission form, which becomes completely hidden (`display: none`) on mobile viewports (`<= 640px`).

**Changes Required**:
- In `webapp/static/app.css` L657–660: change `.split-list { display: none; }` inside `@media (max-width: 640px)` to `.narrow-picker + .split-list { display: none; }` so only a `.split-list` paired with `.narrow-picker` is hidden on mobile, while `/assets` and `/video` stack their `.split-list` normally.

### 1.4 Fix Asset Library Owner Resolution in ComfyUI `/mieh3/plan_preview` (`plan_preview.py`, `tests/_mie_plan_preview_unit_test.py`)

**Root Cause**:
- Commit `9f94717` removed global `MIE_ASSET_LIBRARY` injection from `__init__.py` and updated `mie_nodes.py` to resolve empty `library_dir` via `asset_root.asset_library_dir()`.
- `plan_preview.py` L39 still calls `asset_library.validate_library_dir(library_dir)` directly with `""`, bypassing `asset_root.asset_library_dir()`.

**Changes Required**:
- In `plan_preview.py`:
  - Import `asset_root` (`try: from . import asset_root except ImportError: import asset_root`).
  - Add helper `_library_dir(explicit: str) -> str`:
    ```python
    def _library_dir(explicit: str) -> str:
        return str(explicit or "").strip() or str(asset_root.asset_library_dir())
    ```
  - Use `_library_dir(library_dir)` at the start of `resolve_script_file(library_dir, script_ref)`.
- Add a test case in `tests/_mie_plan_preview_unit_test.py` verifying that when `library_dir=""`, `resolve_script_file("", "Demo")` resolves against `asset_root.asset_library_dir()`.

---

## Batch 2: P1 Logic, Thread-Safety, Boundary & Workflow Wiring Fixes

### 2.1 Fix `/history` Alpine `x-text` SyntaxError, Full Attempts Count & Thread-Safe Artifact Attachment (`webapp/templates/history.html`, `webapp/services/runner.py`)

**Root Cause**:
- `webapp/templates/history.html` L125–126 has unquoted `x-text=" · 续跑"` and `x-text=" · 磁盘记录"`, which are invalid JavaScript expressions in Alpine.js.
- `webapp/services/runner.py` `_jobs_grouped()` (L722–744) only counts `attempt_total` over the current page slice `window` instead of all filtered `works`, producing contradictory headers like `"共 21 部作品 · 12 次尝试"`.
- `webapp/services/runner.py` L510 uses a module-level mutable global `_READY_ARTIFACTS` without locking across Flask threads (`threaded=True`), and L694 computes an unused variable `finished`.

**Changes Required**:
1. In `webapp/templates/history.html` L125–126:
   - Change `x-text=" · 续跑"` to `x-text="' · 续跑'"` and `x-text=" · 磁盘记录"` to `x-text="' · 磁盘记录'"`.
2. In `webapp/services/runner.py`:
   - Remove `_READY_ARTIFACTS` global dictionary. Add optional parameter `prepared: list = None` to `_attach_artifacts(record: dict, outputs: dict = None, index: list = None, prepared: list = None)` and use `artifacts = list(prepared) if prepared is not None else comfy.collect_artifacts(run_name, index)`.
   - In `_jobs_grouped()`: remove unused `finished = next(...)`; compute `attempt_total = sum(len(w["attempts"]) for w in works)` across all filtered works; pass `prepared=prepared` directly to `_attach_artifacts(record, index=index, prepared=prepared)`.

### 2.2 Fix `comfy.group_artifacts_by_run()` Off-By-One Bucketing & 0.7 Layout Support (`webapp/services/comfy.py`)

**Root Cause**:
- In `webapp/services/comfy.py` `_walk_output()` (L270), `entry["parts"]` is `path.relative_to(root).parts` where `root` is `output/h3_chains` or `output/live_storyboards`.
- For `output/h3_chains/<run>/final/film.mp4`, `parts` is `("<run>", "final", "film.mp4")`, so the run directory is `parts[0]`. However, `group_artifacts_by_run()` (L321–326) uses `parts[1]` (`"final"` / `"segments"`), causing every `h3_chains` lookup in `buckets.get(run_name)` to miss.
- `_role_of(relpath)` (L373) and `_walk_runs(root)` (L483) only check legacy `final/` and `segments/`, missing 0.7 layout directories `exports/videos/...` and `generation/clips/`.
- `output_files()` (L201–210) makes an unnecessary network call to `/object_info` and always returns `[]`.

**Changes Required**:
1. In `webapp/services/comfy.py` `group_artifacts_by_run()`:
   - When `len(parts) >= 2`, `owner = parts[0]`.
   - When `len(parts) == 1`, keep the flat filename stem extraction (`<run>_<YYYY-MM-DD>.mp4` -> `<run>`).
2. In `webapp/services/comfy.py` `_role_of(relpath)`:
   - Recognize deliverable videos when `parts[-2] == "final"` OR `("exports" in parts and "videos" in parts)` and `name.endswith((".mp4", ".webm", ".mkv", ".mov"))`.
   - Recognize segments when `"segments" in parts or "blend_segments" in parts or ("generation" in parts and "clips" in parts)`.
3. In `webapp/services/comfy.py` `_walk_runs(root)`:
   - In addition to `(run_dir / "final").glob("*.mp4")` and `(run_dir / "segments").glob("*.mp4")`, also include `sorted((run_dir / "exports" / "videos").rglob("*.mp4"))` if `(run_dir / "exports" / "videos").is_dir()` and `sorted((run_dir / "generation" / "clips").glob("*.mp4"))` if `(run_dir / "generation" / "clips").is_dir()`.
4. Simplify `output_files()` to `return []` without calling `_json(base_url() + "/object_info")`.

### 2.3 Fix Intake Camera Enums, Visible Event Index Mapping & Double-Escaped Newlines (`webapp/templates/scripts.html`, `webapp/static/app.js`)

**Root Cause**:
- `webapp/templates/scripts.html` L605–619 offers `slow_zoom_in` and `slow_zoom_out` for `intake.design.camera.movement`, which are rejected by `mie_craft.script.contract.MOVEMENTS = ("locked", "gentle_follow")`, and omits `two_shot` from `framing`.
- `webapp/static/app.js` L2388–2416 (`intakeMoveEvent`, `intakeDuplicateEvent`, `intakeRemoveEvent`) uses `index` from `intakeVisibleEvents` (`draft.events.filter(e => !e.removed)`) directly as an index into `draft.events`, shifting targets once any AI-added event has `removed: true`.
- `webapp/static/app.js` L2520, L2657, L2683 use `"\\n"` instead of `"\n"`.

**Changes Required**:
1. In `webapp/templates/scripts.html` L605–619:
   - Add `<option value="two_shot">双人</option>` to `intake.design.camera.framing`.
   - Change `intake.design.camera.movement` options to:
     ```html
     <option value="locked">固定</option>
     <option value="gentle_follow">缓跟</option>
     ```
2. In `webapp/static/app.js` (`intakeMoveEvent`, `intakeDuplicateEvent`, `intakeRemoveEvent`):
   - Resolve visible items through `this.intakeVisibleEvents`:
     - In `intakeMoveEvent(index, delta)`: find `visible = this.intakeVisibleEvents`, check `target = index + delta` within `0 <= target < visible.length`, locate their real indices in `draft.events` (`fromIdx = draft.events.indexOf(visible[index])`, `toIdx = draft.events.indexOf(visible[target])`), and move/swap properly in `draft.events`.
     - In `intakeDuplicateEvent(index)`: locate `item = this.intakeVisibleEvents[index]` and `realIdx = draft.events.indexOf(item)`, then splice the duplicate at `realIdx + 1`.
     - In `intakeRemoveEvent(index)`: locate `item = this.intakeVisibleEvents[index]` and `realIdx = draft.events.indexOf(item)`; if `item.addition`, set `item.removed = true`, else `draft.events.splice(realIdx, 1)`.
3. In `webapp/static/app.js` L2520, L2657, L2683:
   - Replace `"\\n"` with `"\n"` and `"\\n\\n"` with `"\n\n"`.

### 2.4 Fix Workflow Reference Wiring Cleanup, Audio Slot Bounds & Multi-Media Asset Loading (`webapp/services/workflow.py`, `mie_nodes.py`)

**Root Cause**:
- `webapp/services/workflow.py` L176–200 does not remove pre-existing `ref_images.ref_image_*` and `ref_audios.ref_audio_*` inputs from the template's `MiniMaxH3ReferenceToVideo` node before wiring the current script's roles, leaving stale links when a script has fewer roles than the preset template.
- `webapp/services/workflow.py` wires `ref_audios.ref_audio_%d` for every character without checking `MieH3AssetToSlots.MAX_AUDIOS` (3), which produces invalid output index `>= 14` if `len(asset_nodes) > 3`.
- `mie_nodes.py` L188 (`MieH3LoadAsset.load()`) raises `ValueError` when `len(images) != 1 or len(clips) > 1`, breaking characters/scenes in the asset library that have multiple reference images or multiple voice samples.

**Changes Required**:
1. In `webapp/services/workflow.py`:
   - Before the `picture_index = 0` loop on `sampler`, delete any existing keys in `sampler["inputs"]` starting with `"ref_images.ref_image_"` or `"ref_audios.ref_audio_"`.
   - Cap `ref_audios` wiring at 3 (`for index, (role, node_id) in enumerate(asset_nodes[:3]): ...`), and if `script_json.get("audio_mode") == "reference"` and `len(asset_nodes) > 3`, raise `ValueError("reference audio mode supports at most 3 character slots")`.
2. In `mie_nodes.py` `MieH3LoadAsset.load()` (L186–195):
   - Require `len(images) >= 1` (raise `ValueError(f"{kind} {name!r} must have at least one reference image")` only when `not images`), and use `images[0]` and `clips[0]` when `clips` is non-empty.

### 2.5 Atomic Settings/Keys Writes & Scene/Snapshot Provenance (`webapp/services/llm.py`, `webapp/services/theme.py`, `webapp/services/runner.py`)

**Changes Required**:
1. In `webapp/services/llm.py` (`_save_settings`, `write_keys`) and `webapp/services/theme.py` (`save_theme`):
   - Use `jsonfile.write_json(...)` instead of raw `Path.write_text(...)`, and guard `SETTINGS_FILE` read-modify-write with a `threading.Lock()` in `jsonfile.py` (e.g. `jsonfile.settings_lock`).
2. In `webapp/services/runner.py`:
   - Record `"scene_hash": snapshot.asset_fingerprint("scene", scene_asset) if scene_asset else ""` in job records (and include `"scene_hash"` in `_save_jobs()` slim keys).
   - In `_inputs_changed(record)`, also check if `record.get("scene_asset")` and `record.get("scene_hash")` changed via `snapshot.changed_assets("scene", {"scene_1": record["scene_asset"]}, {"scene_1": record["scene_hash"]})`.
   - In `_active_snapshots()`, protect snapshot directories for all jobs currently stored in `_jobs` (up to `_JOB_HISTORY`) that have a `snapshot_dir`, or pass `keep=max(snapshot.KEEP, len(_jobs))` so any job visible in the history table can still use `"▶ 从这次继续"`.

---

## Batch 3: High-Leverage Workbench Completions

### 3.1 Render Interrupt Endpoint & UI Stop Button (`webapp/flask_app.py`, `webapp/templates/history.html`, `webapp/static/app.js`)

**Changes Required**:
1. In `webapp/flask_app.py`:
   - Add route `POST /api/render/interrupt` calling `ok = comfy.interrupt()` and returning `jsonify({"ok": ok})`, status `200` if `ok` else `502`.
2. In `webapp/templates/history.html` & `webapp/static/app.js` (`historyPage`):
   - Add `interruptRender()` method in `historyPage()` calling `api.post("/api/render/interrupt", {})`, setting `this.message = "已向 ComfyUI 发送停止请求…"`, and calling `await this.load()`.
   - Show a `<button class="btn danger" type="button" x-show="hasActiveJob" @click="interruptRender()">⏹ 停止当前渲染</button>` in `.panel-head-actions` on `history.html`, and on active attempt rows (`x-show="j.status === 'running' || j.status === 'queued'"`).

### 3.2 Real Compiled Per-Shot Prompt Preview (`assemble_plan` + `plan_sha256`) on `/video` (`webapp/services/runner.py`, `webapp/templates/video.html`, `webapp/static/app.js`)

**Changes Required**:
1. In `webapp/services/runner.py` `plan_preview(script_name, role_assets, scene_asset="")`:
   - Build lightweight `slot_bundles` from the wired `role_assets` and `scene_asset` using `library.get_character(asset_name)` / `library.get_scene(scene_asset)` metadata:
     - For each role index `slot` (`1..len(roles)`), if `role_assets.get(role["id"])` is wired, load character metadata/detail and populate `slot_bundles[slot] = {"kind": "character", "name": detail["name"], "description": detail["description"], "voice": detail.get("voice") or "", "image": True if detail.get("image_count") else None, "audio": {"waveform": True, "sample_rate": 24000} if detail.get("audio_count") else None}`.
     - If `scene_asset` is provided, load scene detail and populate `slot_bundles[MAX_CHARACTER_SLOTS + 1] = {"kind": "scene", "name": s_detail["name"], "description": s_detail["description"], "ambience": s_detail.get("ambience") or "", "image": True if s_detail.get("image_count") else None}` (where `MAX_CHARACTER_SLOTS = 8`).
   - Call `assembled = assemble_plan(storyboard=script["json"], slot_bundles=slot_bundles)` and `plan_json_str = plan_to_json_string(assembled["plan"])`.
   - Compute `plan_sha256 = hashlib.sha256(plan_json_str.encode("utf-8")).hexdigest()[:8]`.
   - Include `"compiled_plan": assembled["plan"]`, `"plan_sha256": plan_sha256`, and `"summary_lines": assembled.get("summary_lines") or []` in the returned dictionary (if `assemble_plan` raises `ValueError` when partially wired, catch it and return `"compile_error": str(exc)` while keeping existing fields intact for backward compatibility).
2. In `webapp/static/app.js` (`videoPage`) & `webapp/templates/video.html`:
   - Store `compiledPlan: null`, `planSha256: ""`, `compileError: ""` on `videoPage`.
   - In `video.html` right pane (`split-detail`), show the `plan_sha256` badge (`x-show="planSha256"`) and a collapsible `<details class="preview-block" x-show="compiledPlan">` displaying `prompt_prefix` and each compiled shot's `id`, `length` frames, and English prompt lines.

### 3.3 Theme Switcher in `base.html` (`webapp/templates/base.html`, `webapp/static/app.js`)

**Changes Required**:
1. In `webapp/templates/base.html` inside `.main-tabs-status`:
   - Add a compact `<select class="input" style="width: auto; padding: 3px 8px; font-size: 12px;" aria-label="切换主题" @change="switchTheme($event.target.value)">` iterating over Jinja `{% for t in themes %}<option value="{{ t.id }}" {% if t.id == theme_id %}selected{% endif %}>{{ t.name }}</option>{% endfor %}`.
2. In `webapp/static/app.js` (`appShell()`):
   - Add `async switchTheme(themeId)` that posts to `/api/theme`, fetches `/api/themes`, and updates `document.body.dataset.theme = themeId` and `#theme-injector` CSS declarations in place.

### 3.4 CI Gate & Regression Tests (`tools/ci_gate.py`, `webapp_tests/`)

**Changes Required**:
1. In `tools/ci_gate.py` `MUST_PASS`:
   - Add `"_mie_checkpoint_identity_unit_test.py"` and `"_mie_plan_preview_unit_test.py"`.
2. In `webapp_tests/test_verify_regressions.py` (or a new test file `webapp_tests/test_remediation_20261123.py`):
   - Add tests verifying:
     - `llm.html` renders `data-key` and `data-host` attributes, binds `p.endpoint` for host input, and has `mie_llm_keys.json` spelling.
     - `autodraft._connector()` succeeds for a keyless `host: True` platform (`ollama`) when selected as current.
     - `app.css` scopes mobile `.split-list { display: none; }` to `.narrow-picker + .split-list`.
     - `history.html` has properly quoted `x-text="' · 续跑'"` and `x-text="' · 磁盘记录'"`.
     - `runner.jobs(grouped=True, per_page=1)` returns total `attempts` across all works, not just page 1.
     - `comfy.group_artifacts_by_run()` buckets `h3_chains/<run>/final/a.mp4` under `<run>` (`parts[0]`), and recognizes 0.7 layout `exports/videos/...` and `generation/clips/...`.
     - `scripts.html` Intake camera movement dropdown uses `locked` and `gentle_follow` (and `two_shot` framing), and `app.js` event move/duplicate/remove maps through `intakeVisibleEvents` and uses real `"\n"`.
     - `workflow.build()` strips stale template `ref_images.*` / `ref_audios.*` when building a 1-role script, and `MieH3LoadAsset.load()` accepts an asset with 2 images and 2 audio clips by picking the first of each.
     - `POST /api/render/interrupt` calls `comfy.interrupt()`.
     - `POST /api/scripts/<name>/plan` returns `compiled_plan` and 8-char `plan_sha256` when roles are wired.
