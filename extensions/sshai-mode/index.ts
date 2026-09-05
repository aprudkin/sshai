import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { AutocompleteItem } from "@earendil-works/pi-tui";

const STATE_ENTRY_TYPE = "sshai-execution-mode.v1";
const STATUS_KEY = "sshai-mode";

const GUIDANCE = `## SSHAI session mode

SSHAI mode is enabled for this session. Before the first applicable command, load and follow the \`sshai\` skill if it is not already loaded. For non-interactive local commands that may produce substantial or repeated output, prefer \`sshai local\` over direct shell execution. Keep complete output in the artifact and use \`sshai q\`, \`sshai diff\`, or \`--delta\` to retrieve only the evidence needed in context.

Continue using direct tools for short, predictably bounded commands, ordinary file operations, interactive or streaming commands, and workflows unsupported by \`sshai\`. This mode is advisory execution guidance, not authorization, a security boundary, or a reason to run a command that the user did not request.`;

export default function sshaiMode(pi: ExtensionAPI) {
	let enabled = false;

	function updateStatus(ctx: ExtensionContext): void {
		if (!enabled) {
			ctx.ui.setStatus(STATUS_KEY, undefined);
			return;
		}

		const { theme } = ctx.ui;
		ctx.ui.setStatus(STATUS_KEY, theme.fg("accent", theme.bold("sshai:on")));
	}

	function restore(ctx: ExtensionContext): void {
		enabled = false;
		const branch = ctx.sessionManager.getBranch();
		for (let i = branch.length - 1; i >= 0; i--) {
			const entry = branch[i];
			if (entry.type !== "custom" || entry.customType !== STATE_ENTRY_TYPE) continue;
			const data = entry.data as { enabled?: unknown } | undefined;
			if (typeof data?.enabled === "boolean") enabled = data.enabled;
			break;
		}
		updateStatus(ctx);
	}

	function setEnabled(next: boolean, ctx: ExtensionContext): void {
		if (enabled === next) return;
		enabled = next;
		pi.appendEntry(STATE_ENTRY_TYPE, { enabled });
		updateStatus(ctx);
	}

	pi.on("session_start", async (_event, ctx) => restore(ctx));
	pi.on("session_tree", async (_event, ctx) => restore(ctx));
	pi.on("session_shutdown", async (_event, ctx) => {
		ctx.ui.setStatus(STATUS_KEY, undefined);
	});

	pi.on("before_agent_start", async (event) => {
		if (!enabled) return;
		return { systemPrompt: `${event.systemPrompt}\n\n${GUIDANCE}` };
	});

	pi.registerCommand("sshai", {
		description: "Session-wide sshai preference: /sshai on, /sshai off, or /sshai status",
		getArgumentCompletions(prefix: string): AutocompleteItem[] | null {
			const items: AutocompleteItem[] = [
				{ value: "on", label: "on", description: "Enable sshai guidance for this session" },
				{ value: "off", label: "off", description: "Disable sshai guidance for this session" },
				{ value: "status", label: "status", description: "Show the current session state" },
			];
			const matches = items.filter((item) => item.value.startsWith(prefix.trim().toLowerCase()));
			return matches.length > 0 ? matches : null;
		},
		handler: async (args, ctx) => {
			const command = (args || "").trim().toLowerCase();
			if (command === "on") {
				if (enabled) {
					ctx.ui.notify("sshai mode is already enabled for this session", "info");
					return;
				}
				setEnabled(true, ctx);
				ctx.ui.notify("sshai mode enabled for this session", "info");
				return;
			}

			if (command === "off") {
				if (!enabled) {
					ctx.ui.notify("sshai mode is already disabled for this session", "info");
					return;
				}
				setEnabled(false, ctx);
				ctx.ui.notify("sshai mode disabled for this session", "info");
				return;
			}

			if (command === "status") {
				ctx.ui.notify(`sshai mode: ${enabled ? "enabled" : "disabled"}`, "info");
				return;
			}

			ctx.ui.notify("Usage: /sshai on | /sshai off | /sshai status", "warning");
		},
	});
}
