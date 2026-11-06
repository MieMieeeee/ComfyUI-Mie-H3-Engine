#!/usr/bin/env python3
"""Build the new-architecture H3 workflow in ComfyUI's API format.

The old workflow (mie_nodes_movie.json) produced its plan with the
MieNodes pack's own generator and drove the chain with the UPSTREAM
nodes. This one is the same shot, on the new architecture:

  * the script asset is read by the engine's ``MieH3LoadScriptAsset``
  * the plan is assembled by the engine's ``MieH3ScriptToPlan`` - the
    same shared builder the MieNodes pack uses, so both packs produce
    the same plan for the same script
  * the whole chain runs the engine's own nodes (``|MieH3``), not
    upstream's
  * ``MieH3LoadAssetRoster`` shows what the shared library holds

Only the asset-to-plan path and the node suffixes differ from the old
workflow, so a render of the two is an A/B of the architecture rather
than of the shot.

Usage:
    python tools/build_newarch_workflow.py [output.json]
"""

from __future__ import annotations

import json
import pathlib
import sys

ENGINE = "comfyui-minimaxh3-contex-loop"
MIENODES = "ComfyUI_MieNodes"

SCRIPT_NAME = "Claude的千层套路001-系列版"
# The script's OWN roles. The plan assembler refuses to run without a
# character asset per slot, and the slot order follows the script's role
# order: 小D is the off-screen voice, Claude娘 is the one on camera.
# Wiring anyone else puts a different face in the frame.
CHARACTER_1 = "小D"
CHARACTER_2 = "Claude娘"
RUN_NAME = "sb_newarch_20261106"

# The plan the old workflow carried as a literal fallback. The Plan node
# prefers ``plan_json_input`` whenever it is wired, so this is only what
# the node shows - and what it would render if the new assembly path were
# disconnected. Keeping it makes the two workflows differ only where they
# are meant to.
# The plan this workflow actually produces, captured from a live run -
# so the widget shows the real thing and the fallback is a valid plan
# too (the Plan node prefers plan_json_input whenever it is wired).
# The widget is a multiline STRING, so the plan goes in as JSON text.
FALLBACK_PLAN_JSON = json.dumps(
    json.loads(
        r'''{"shots": [{"id": "scene_01", "prompt": ["integrated_multimodal_description: [Shot 1] medium close up of Character 2 from <Picture 1>, already visible at the first frame, static locked-off camera maintaining the same framing, first-person POV toward the camera.", "Detailed two-dimensional animation with coherent linework and palette.", "Character 1 stays exclusively off-screen as a voice.", "Character 2 is the figure in <Picture 1>; use this reference for identity and appearance only.", "Opening cast: Character 2. Begin directly in this framing.", "Not visible at the opening: Character 1. Only explicit entrance events may add them.", "Character 2 begins at center of frame.", "Opening staging: Character 2 sits at the center of the sofa facing the camera, hands relaxed in the lap. Character 1 is an off-screen voice.", "Scene context: A living room with a beige sofa, a floor lamp, a window and a plain wall.", "Light: Deep night, pitch-dark outside, with warm lamplight.", "Only the explicitly tagged dialogue is spoken. Do not speak stage directions or role labels.", "Perform the following events once, in the exact written order; one speaker at a time, without overlapping or repeating lines.", "Each specified prop exists in its stated quantity and stays with its stated holder. An offered object stays with the offering actor until an explicit accepted handover; refusal leaves the other actor empty-handed.", "During dialogue-only events, keep incidental reference props with their initial holder and limit movement to natural speaking reactions.", "The image contains exactly the declared visible cast in the present setting. Spoken stories remain audio within this present conversation.", "Visible state: Character 2 is the only visible figure, seated at center with hands at rest. Character 1 remains exclusively an off-screen voice. The ordered events govern later expressions and gestures.", "Character 2 performs this action: Character 2 remains seated on the sofa, tilts her head slightly and listens quietly.", "Character 2 remains on-screen with lips completely closed while an off-screen voice (S1), adult male, casual and clear timbre, mid register, conversational pace, from Character 1 says in voiceover: <d>[English] Please help me resolve this document.</d>", "While this line is spoken, Character 2's lips remain completely closed.", "Character 2 speaks as (S2), young female, soft and playful timbre, mid-high register, intimate gentle pace with a teasing lift: <d>[Chinese] 其实，你可以和我直接用中文交流的，我不会介意的。</d> Character 2's lips move naturally with this line.", "Character 2 remains on-screen with lips completely closed while an off-screen voice (S1), adult male, casual and clear timbre, mid register, conversational pace, from Character 1 says in voiceover: <d>[English] wait, i'm not Chinese.</d>", "While this line is spoken, Character 2's lips remain completely closed.", "Character 2 speaks as (S2), young female, soft and playful timbre, mid-high register, intimate gentle pace with a teasing lift: <d>[Chinese] 哎呀，我都上架中文了，相信我，好吗。</d> Character 2's lips move naturally with this line.", "Character 2 remains on-screen with lips completely closed while an off-screen voice (S1), adult male, casual and clear timbre, mid register, conversational pace, from Character 1 says in voiceover: <d>[Chinese] 真的吗？</d>", "While this line is spoken, Character 2's lips remain completely closed.", "Character 2 speaks as (S2), young female, soft and playful timbre, mid-high register, intimate gentle pace with a teasing lift: <d>[Chinese] 哼哼，你的账号已被封禁。</d> Character 2's lips move naturally with this line.", "Character 2 performs this action: Character 2 closes her mouth after her final line, maintains her original seated pose and holds a sinister teasing smile briefly.", "overall_soundscape: Natural room tone, breathing and synchronized movement sounds.", "non_diegetic_music: No non-diegetic music."], "length": 430}], "prompt_prefix": ["Voice casting: (S1) uses the voice identity from <Audio 1> only; the reference words are not dialogue.", "Voice casting: (S2) uses the voice identity from <Audio 2> only; the reference words are not dialogue."]}'''
    ),
    ensure_ascii=False,
)

PROMPT_PREVIEW = (
    "新架构工作流 · New-architecture workflow\n"
    "\n"
    "剧本 → 计划：MieH3LoadScriptAsset → MieH3ScriptToPlan（引擎侧，零 LLM，"
    "与 MieNodes 共用同一个 builder）\n"
    "链路：全部走 |MieH3 节点，即引擎自己的实现，而不是上游的\n"
    "\n"
    "旧工作流（mie_nodes_movie.json）用 MieNodes 的生成器 + 上游节点；"
    "这一版只换架构，镜头内容不变，可以直接对比。"
)


def node(class_type, inputs, title, cnr_id):
    return {
        "class_type": class_type,
        "inputs": inputs,
        "_meta": {"title": title, "cnr_id": cnr_id, "ver": "1"},
    }


def build() -> dict:
    nodes: dict = {}

    # ---------------------------------------------------------------- #
    # 1. The new architecture: the shared library feeds the plan.
    # ---------------------------------------------------------------- #
    nodes["85"] = node(
        "MieH3LoadScriptAsset",
        {"library_dir": "", "name": SCRIPT_NAME},
        "剧本资产 · Load Script Asset (MieH3)",
        ENGINE,
    )
    nodes["79"] = node(
        "LoadAssetV2|Mie",
        {"kind": "character", "library_dir": "", "name": CHARACTER_1},
        "角色资产 · Load %s" % CHARACTER_1,
        MIENODES,
    )
    nodes["80"] = node(
        "LoadAssetV2|Mie",
        {"kind": "character", "library_dir": "", "name": CHARACTER_2},
        "角色资产 · Load %s" % CHARACTER_2,
        MIENODES,
    )
    # The zero-LLM assembler. Same builder as the MieNodes pack's own
    # generator, so both produce the same plan for the same script.
    nodes["86"] = node(
        "MieH3ScriptToPlan",
        {
            "storyboard": ["85", 0],
            "character_1": ["79", 5],
            "character_2": ["80", 5],
        },
        "剧本转计划 · Script → Plan (MieH3)",
        ENGINE,
    )
    # The wired cast, in slot order, as pictures and reference audio.
    nodes["57"] = node(
        "AssetToSlots|Mie",
        {"assets": ["86", 3]},
        "资产转槽位 · Asset → Slots",
        MIENODES,
    )
    # The new roster node: what the shared library holds.
    nodes["90"] = node(
        "MieH3LoadAssetRoster",
        {"library_dir": "", "kind": "character"},
        "库内角色清单 · Library Roster (MieH3)",
        ENGINE,
    )

    # ---------------------------------------------------------------- #
    # 2. The plan and the generation policy.
    # ---------------------------------------------------------------- #
    nodes["4"] = node(
        "MiniMaxH3GenerationProfile|MieH3",
        {"scene_continuity": "Visual continuity",
         "audio_profile": "Generate audio"},
        "Generation Profile",
        ENGINE,
    )
    nodes["3"] = node(
        "MiniMaxH3ChainPlanModern|MieH3",
        {
            "plan_json": FALLBACK_PLAN_JSON,
            "run_name": RUN_NAME,
            "generation_fingerprint": "v1",
            "width": 960,
            "height": 544,
            "encode_mode": "video",
            "crop": "disabled",
            "default_duration_seconds": 10,
            "default_steps": 20,
            "base_seed": 0,
            "segment_crf": 20,
            "video_blend_frames": 0,
            "chain_policy": ["4", 0],
            # The new architecture's output wins over the literal above.
            "plan_json_input": ["86", 0],
        },
        "Production Plan",
        ENGINE,
    )
    nodes["1"] = node(
        "MiniMaxH3ChainScenePromptEditor|MieH3",
        {"plan": ["3", 0]},
        "Scene Prompts",
        ENGINE,
    )
    nodes["38"] = node(
        "MiniMaxH3ChainPreflight|MieH3",
        {
            "plan": ["1", 0],
            "start_clip": 1,
            "scene_range": "",
            "verify_resume_history": True,
        },
        "MiniMax H3 Chain Preflight",
        ENGINE,
    )
    nodes["6"] = node(
        "MiniMaxH3ChainLoopStart|MieH3",
        {
            "plan": ["38", 0],
            "start_clip": 1,
            "scene_range": "",
            "verify_resume_history": True,
        },
        "Loop Start",
        ENGINE,
    )
    nodes["15"] = node(
        "MiniMaxH3ChainCurrent|MieH3",
        {"align_audio_reference": False, "state": ["6", 1]},
        "Current Scene",
        ENGINE,
    )

    # ---------------------------------------------------------------- #
    # 3. The H3 model itself.
    # ---------------------------------------------------------------- #
    nodes["5"] = node(
        "UNETLoader",
        {"unet_name": "MiniMax-H3\\minimax_h3_ref2va_pruned_int8_convrot.safetensors",
         "weight_dtype": "default"},
        "H3 Diffusion Model",
        "comfy-core",
    )
    nodes["20"] = node(
        "CLIPLoader",
        {"clip_name": "MiniMax-H3\\qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
         "type": "minimax", "device": "default"},
        "H3 Text Encoder",
        "comfy-core",
    )
    nodes["21"] = node(
        "VAELoader",
        {"vae_name": "MiniMax-H3\\minimax_h3_audio_vae_fp32.safetensors"},
        "Audio VAE", "comfy-core",
    )
    nodes["22"] = node(
        "VAELoader",
        {"vae_name": "MiniMax-H3\\minimax_h3_video_vae_fp16.safetensors"},
        "Video VAE", "comfy-core",
    )
    nodes["27"] = node(
        "ModelAttentionBackend",
        {"attention": "comfy kitchen attention", "model": ["5", 0]},
        "Model Attention Backend", "comfy-core",
    )
    nodes["2"] = node(
        "MiniMaxH3SigmaShift",
        {"shift_video": 12, "shift_audio": 3, "model": ["27", 0]},
        "Sigma Shift", "comfy-core",
    )
    nodes["47"] = node(
        "MiniMaxH3ReferenceToVideo",
        {
            "prompt": ["15", 4],
            "width": ["15", 8],
            "height": ["15", 9],
            "length": ["15", 6],
            "ref_image_size": "match",
            "clip": ["20", 0],
            "vae": ["22", 0],
            "audio_vae": ["21", 0],
            "ref_images.ref_image_0": ["57", 1],
            "ref_audios.ref_audio_0": ["57", 11],
            "ref_audios.ref_audio_1": ["57", 12],
        },
        "MiniMax H3 Reference to Video",
        "comfy-core",
    )
    nodes["24"] = node(
        "MiniMaxH3ChainContext|MieH3",
        {
            "state": ["15", 0],
            "conditioning": ["47", 0],
            "vae": ["22", 0],
            "latent": ["47", 1],
            "audio_vae": ["21", 0],
        },
        "Apply Scene Context",
        ENGINE,
    )
    nodes["13"] = node(
        "SamplerCustomAdvanced",
        {
            "noise": ["17", 0],
            "guider": ["16", 0],
            "sampler": ["18", 0],
            "sigmas": ["19", 0],
            "latent_image": ["24", 3],
        },
        "Sample Video + Audio",
        "comfy-core",
    )
    nodes["16"] = node(
        "BasicGuider",
        {"model": ["2", 0], "conditioning": ["24", 0]},
        "Basic Guider", "comfy-core",
    )
    nodes["17"] = node(
        "RandomNoise", {"noise_seed": ["15", 5]}, "Scene Seed", "comfy-core",
    )
    nodes["18"] = node(
        "KSamplerSelect", {"sampler_name": "euler"}, "Sampler", "comfy-core",
    )
    nodes["19"] = node(
        "BasicScheduler",
        {"scheduler": "simple", "steps": ["15", 7], "denoise": 1, "model": ["2", 0]},
        "Sampling Schedule", "comfy-core",
    )
    nodes["11"] = node(
        "VAEDecodeAudio", {"samples": ["13", 0], "vae": ["21", 0]},
        "VAEDecode Audio", "comfy-core",
    )
    nodes["12"] = node(
        "VAEDecode", {"samples": ["13", 0], "vae": ["22", 0]},
        "VAEDecode", "comfy-core",
    )
    nodes["23"] = node(
        "MiniMaxH3LoopTrim|MieH3",
        {
            "trim_frames": ["24", 1],
            "fps": 24,
            "match_tail": True,
            "audio_trim_mode": "sync_with_video",
            "images": ["12", 0],
            "audio": ["11", 0],
            "state": ["15", 0],
        },
        "Trim Carried Overlap",
        ENGINE,
    )
    nodes["25"] = node(
        "MiniMaxH3ChainSegmentSave|MieH3",
        {
            "state": ["15", 0],
            "images": ["23", 0],
            "sampled_latent": ["13", 0],
            "audio": ["23", 1],
            "images_with_overlap": ["23", 2],
        },
        "Save Segment + Checkpoint",
        ENGINE,
    )
    nodes["26"] = node(
        "MiniMaxH3ChainReview|MieH3",
        {
            "enabled": False,
            "play_notification_sound": False,
            "auto_continue_timeout_minutes": 0,
            "unload_models_while_waiting": False,
            "assemble_partial_on_stop": True,
            "partial_audio_source": "checkpointed",
            "state": ["15", 0],
            "segment": ["25", 0],
            "audio": ["23", 1],
            "candidate_count": 1,
            "review_each_candidate": False,
        },
        "Review Candidates",
        ENGINE,
    )
    nodes["8"] = node(
        "MiniMaxH3ChainLoopEnd|MieH3",
        {
            "between_scene_cleanup": "off",
            "execution_mode": "recursive",
            "flow": ["6", 0],
            "state": ["15", 0],
            "images": ["23", 0],
            "sampled_latent": ["13", 0],
            "segment": ["26", 0],
        },
        "Loop End",
        ENGINE,
    )
    nodes["7"] = node(
        "MiniMaxH3ChainAssemble|MieH3",
        {
            "audio_source": "plan",
            "filename": RUN_NAME + "_%date:yyyy-MM-dd%",
            "audio_bitrate": 256,
            "copy_to_output": True,
            "output_subfolder": "live_storyboards",
            "blend_schedule": "plan",
            "boundary_tone_match": "off",
            "color_stabilization": "off",
            "delete_checkpoints_after_assembly": False,
            "generated_audio_join": "av_overlap",
            "video-preview": "",
            "manifest": ["8", 0],
        },
        "Assemble Final Video",
        ENGINE,
    )

    # ---------------------------------------------------------------- #
    # 4. The MieNodes conveniences.
    # ---------------------------------------------------------------- #
    nodes["50"] = node(
        "ShowAndSaveAnything|Mie",
        {
            "save_to_log": True,
            "log_file_name": "show_anything.log",
            "displaytext": PROMPT_PREVIEW,
            "anything": ["86", 0],
        },
        "Show And Save Anything 🐑",
        MIENODES,
    )
    nodes["53"] = node("AboutAuthorNode|Mie", {}, "About Author 🐑", MIENODES)

    return nodes


def main() -> int:
    out = pathlib.Path(
        sys.argv[1] if len(sys.argv) > 1
        else pathlib.Path(__file__).resolve().parents[1]
        / "examples" / "mie_h3_newarch_movie.api.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
