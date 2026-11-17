import {app} from "/scripts/app.js";

const SCRIPT_TO_PLAN = new Set([
    "MieH3ScriptToPlan",
    "MieH3ScriptToPlan|MieH3",
]);

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? "";
}

function widgetValue(node, name) {
    const widget = node?.widgets?.find((item) => item.name === name);
    return widget == null ? "" : String(widget.value ?? "");
}

function inputSource(node, name) {
    const input = node?.inputs?.find((item) => item.name === name);
    const link = input?.link == null ? null : node.graph?.links?.[input.link];
    return link ? node.graph?.getNodeById?.(link.origin_id) ?? null : null;
}

export function planInputConnected(node) {
    return (node?.inputs ?? []).some(
        (input) => input.name === "plan_json_input" && input.link != null);
}

export function findPlanProvider(start) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        if (typeof current.mieH3PlanProvider === "function") return current;
        for (const input of current.inputs ?? []) {
            const parent = inputSource(current, input.name);
            if (parent) queue.push(parent);
        }
    }
    return null;
}

export function mountPreviewBanner(root) {
    const banner = document.createElement("div");
    banner.className = "mieh3-preview-banner";
    banner.hidden = true;
    banner.style.cssText = [
        "display:flex", "gap:8px", "align-items:center",
        "margin:4px 8px", "padding:6px 8px",
        "background:#3a2a12", "color:#f3e6c8",
        "font:12px/1.4 sans-serif",
    ].join(";");
    const text = document.createElement("span");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "刷新";
    banner.append(text, button);
    root.prepend(banner);
    return {
        show(message, onRefresh) {
            text.textContent = message;
            banner.hidden = false;
            banner.style.display = "flex";
            button.onclick = (event) => {
                event.stopPropagation();
                onRefresh();
            };
        },
        hide() {
            banner.hidden = true;
        },
    };
}

function attachProvider(node) {
    if (!SCRIPT_TO_PLAN.has(nodeType(node))) return;
    node.mieH3PlanProvider = () => ({
        fetchPlan: async () => {
            const loader = inputSource(node, "storyboard");
            const response = await fetch("/mieh3/plan_preview", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    library_dir: widgetValue(loader, "library_dir"),
                    script_ref: widgetValue(loader, "name"),
                }),
            });
            let payload = {};
            try {
                payload = await response.json();
            } catch (_error) {
                payload = {};
            }
            if (!response.ok) {
                throw new Error(payload.error || `HTTP ${response.status}`);
            }
            return payload;
        },
    });
}

app.registerExtension({
    name: "mieh3.planPreview",
    nodeCreated(node) {
        attachProvider(node);
    },
});
