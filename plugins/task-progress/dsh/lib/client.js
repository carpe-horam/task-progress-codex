/**
 * Task Progress — DeepSeekRuntime (dsh) client half.
 *
 * Codex renders the progress card because the MCP server binds `app.html` to the
 * `render_progress` tool as an MCP App resource. DeepSeekRuntime has no MCP Apps
 * host, so this module supplies the missing host:
 *
 *   - `tool.call.toolview` keyed by the MCP wire name renders the same `app.html`
 *     inside the conversation;
 *   - the small proxy below answers the MCP Apps handshake
 *     (`ui/initialize`, `ui/request-display-mode`) and relays every other
 *     JSON-RPC message to `/task-progress/rpc`, which the host half forwards to
 *     the same MCP server the agent's tools use.
 *
 * The panel therefore needs no data of its own: it is told the session through
 * `ui/notifications/tool-input` and then reads progress with `tools/call`, exactly
 * as it does inside Codex.
 */
(() => {
  "use strict";

  /**
   * The tool whose call opens the card. The host half registers the MCP tools
   * under their own names (no `mcp__` prefix), so the wire name is plain.
   */
  const RENDER_TOOL = "render_progress";
  const PANEL_SRC = "/task-progress/app.html";
  const RPC_URL = "/task-progress/rpc";

  const DEFAULT_HEIGHT = 520;
  const MIN_HEIGHT = 240;
  const MAX_HEIGHT = 900;

  const CSS = [
    ".dsh-task-progress{display:block;width:100%;min-width:0;position:relative}",
    ".dsh-task-progress-frame{display:block;width:100%;border:0;background:transparent;overflow:hidden}",
    ".dsh-task-progress-frame iframe{display:block;width:100%;border:0;background:transparent}",
    ".dsh-task-progress-fullscreen{position:fixed;inset:0;z-index:2147483000;background:transparent}",
    ".dsh-task-progress-fullscreen iframe{height:100%}",
    ".dsh-task-progress-close{position:absolute;top:20px;right:24px;z-index:1;border:0;cursor:pointer;",
    "border-radius:999px;padding:6px 14px;font:inherit;font-size:12px;line-height:1.6;",
    "background:var(--dsw-alias-interactive-bg-hover,#f1f2f4);color:var(--dsw-alias-label-primary,#1a1a1a)}",
    ".dsh-task-progress-close:hover{background:var(--dsw-alias-interactive-bg-active,#e4e6ea)}",
    ".dsh-task-progress-error{padding:12px 14px;border-radius:var(--dsw-radius-md,10px);",
    "background:var(--dsw-alias-interactive-bg-hover,#f1f2f4);color:var(--dsw-alias-label-secondary,#6b6f76);",
    "font-size:12px;line-height:1.6}",
    ".dsh-task-progress-page{display:flex;flex-direction:column;gap:0;width:100%;box-sizing:border-box}",
    ".dsh-task-progress-page .dsh-task-progress-frame{display:flex;width:100%;flex:1 1 auto;min-height:0}",
    ".dsh-task-progress-page iframe{display:block;width:100%;height:100%;border:0}",
  ].join("");

  /**
   * The card and the sidebar panel must agree on the current Task Progress
   * session. The card learns it from the tool call (or the host); publishing it
   * here keeps one panel open while the agent keeps working.
   */
  const DIAG_URL = "/task-progress/diag";

  /** Report what this surface actually did; the host keeps the last few entries. */
  function reportDiag(kind, data) {
    try {
      void fetch(DIAG_URL, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ kind, data }),
        keepalive: true,
      }).catch(() => {});
    } catch {}
  }

  /**
   * The shell's colour scheme, read from the theme service rather than from a DOM
   * attribute: `ThemeSnapshot.active.colorScheme` is the value the shell itself
   * renders with, while `documentElement` carries no reliable light/dark marker
   * (guessing it left a dark shell rendering a white panel).
   */
  function hostTheme(themeService) {
    try {
      const scheme = themeService?.getTheme?.()?.active?.colorScheme;
      if (scheme === "dark" || scheme === "light") return scheme;
    } catch {}
    return "light";
  }

  /**
   * Follow shell theme switches through the service's `theme/change` event, once
   * on mount and on every later change. Returns a disposer.
   */
  function watchHostTheme(ctx, onChange) {
    const themeService = ctx?.get?.("theme");
    try {
      onChange(hostTheme(themeService));
    } catch {}
    try {
      const stop = ctx?.on?.("theme/change", () => {
        try { onChange(hostTheme(themeService)); } catch {}
      });
      if (typeof stop === "function") return stop;
    } catch {}
    return () => {};
  }

  const sessionStore = { current: null, listeners: new Set() };

  function publishSession(id) {
    if (!id || id === sessionStore.current) return;
    sessionStore.current = id;
    for (const listener of sessionStore.listeners) {
      try { listener(id); } catch {}
    }
  }

  function installStyles(ctx) {
    try {
      const styles = ctx.get("styles");
      if (styles && typeof styles.insert === "function") {
        styles.insert(CSS);
        return;
      }
    } catch {}
    try {
      const tag = document.createElement("style");
      tag.textContent = CSS;
      document.head.appendChild(tag);
    } catch {}
  }

  function createCard(React, ctx) {
    const h = React.createElement;

    /**
     * Task Progress keys its data by the UUID it returned from `init_progress` /
     * `init_task_network`, not by the host session id, so the card reads the
     * session from the tool call (arguments or result) first and only then falls
     * back to a host session that already looks like one of its ids.
     */
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    function firstSession(...candidates) {
      for (const candidate of candidates) {
        if (typeof candidate === "string" && UUID.test(candidate)) return candidate;
      }
      return null;
    }

    /** Pull the MCP tool result out of whichever shape this DSH version supplies. */
    function readToolResult(block) {
      const direct = block?.result ?? block?.toolResult;
      if (direct && (Array.isArray(direct.content) || direct.structuredContent)) return direct;
      return null;
    }

    function readArgs(block) {
      return block?.args ?? block?.arguments ?? block?.input ?? null;
    }

    return function TaskProgressCard(props) {
      const block = props.block;
      const args = readArgs(block);
      const toolResult = React.useMemo(() => readToolResult(block), [block]);
      const progressSession = React.useMemo(() => firstSession(
        args?.session_id,
        toolResult?.structuredContent?.session_id,
        toolResult?.structuredContent?.progress?.session_id,
        props.sessionId,
      ), [args, toolResult, props.sessionId]);
      // Fall back to the session the host recorded for the most recent progress
      // tool call: the Chat node does not reliably expose call arguments.
      const [hostSession, setHostSession] = React.useState(null);
      const activeSession = progressSession || hostSession;
      React.useEffect(() => {
        publishSession(activeSession);
        if (!activeSession || !props?.sessionId) return;
        // Tell the host which progress session belongs to this conversation, so
        // the right-sidebar tab of any other conversation stays separate.
        try {
          void fetch("/task-progress/bind", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ dsh: props.sessionId, session: activeSession }),
            keepalive: true,
          }).catch(() => {});
        } catch {}
      }, [activeSession, props?.sessionId]);
      const frameRef = React.useRef(null);
      const [height, setHeight] = React.useState(DEFAULT_HEIGHT);
      const [fullscreen, setFullscreen] = React.useState(false);

      React.useEffect(() => {
        if (progressSession) return undefined;
        let cancelled = false;
        fetch("/task-progress/session")
          .then(response => response.ok ? response.json() : null)
          .then(payload => {
            if (!cancelled && typeof payload?.session === "string" && payload.session) {
              setHostSession(payload.session);
            }
          })
          .catch(() => {});
        return () => { cancelled = true; };
      }, [progressSession]);

      const post = React.useCallback((payload) => {
        const frame = frameRef.current;
        if (!frame || !frame.contentWindow) return;
        frame.contentWindow.postMessage(payload, window.location.origin);
      }, []);

      React.useEffect(() => {
        const onMessage = async (event) => {
          const frame = frameRef.current;
          if (!frame || event.source !== frame.contentWindow) return;
          const message = event.data;
          if (!message || message.jsonrpc !== "2.0" || typeof message.method !== "string") return;

          // Handshake answers stay local: they describe this seat, not the server.
          if (message.method === "ui/initialize") {
            if (message.id === undefined) return;
            post({
              jsonrpc: "2.0",
              id: message.id,
              result: {
                protocolVersion: message.params?.protocolVersion || "2026-01-26",
                hostContext: {
                  displayMode: fullscreen ? "fullscreen" : "inline",
                  availableDisplayModes: ["inline", "fullscreen"],
                  theme: hostTheme(ctx?.get?.("theme")),
                },
                hostCapabilities: {},
              },
            });
            return;
          }

          if (message.method === "ui/request-display-mode") {
            const wanted = message.params?.mode === "fullscreen" ? "fullscreen" : "inline";
            let granted = "inline";
            if (wanted === "fullscreen") {
              // Progress now lives in the right column, so "open the progress
              // page" should surface that column instead of covering the window
              // with an overlay. Fall back to the overlay when the shell has no
              // layout service to open it.
              const layout = ctx?.get?.("layout");
              if (layout && typeof layout.openRightbar === "function") {
                try {
                  layout.openRightbar(true, false);
                  granted = "inline";
                } catch {
                  setFullscreen(true);
                  granted = "fullscreen";
                }
              } else {
                setFullscreen(true);
                granted = "fullscreen";
              }
            } else {
              setFullscreen(false);
              granted = "inline";
            }
            if (message.id !== undefined) {
              post({ jsonrpc: "2.0", id: message.id, result: { mode: granted } });
            }
            return;
          }

          if (message.method === "ui/notifications/size-changed") {
            const next = Number(message.params?.height);
            if (Number.isFinite(next) && next > 0) {
              setHeight(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(next))));
            }
            return;
          }

          // Host-only notifications are never relayed: the server does not know them.
          if (message.method.startsWith("ui/")) return;

          if (message.id === undefined) {
            try {
              await fetch(RPC_URL, {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify(message),
              });
            } catch {}
            return;
          }

          try {
            const response = await fetch(RPC_URL, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify(message),
            });
            const payload = await response.json();
            post(payload);
          } catch (cause) {
            post({
              jsonrpc: "2.0",
              id: message.id,
              error: { code: -32000, message: String(cause?.message || cause) },
            });
          }
        };

        window.addEventListener("message", onMessage);
        return () => window.removeEventListener("message", onMessage);
      }, [fullscreen, post]);

      const onLoad = React.useCallback(() => {
        reportDiag("card-frame-loaded", { session: activeSession, height });
        if (!activeSession) return;
        // The notifications Codex sends while the tool runs, so `app.html` adopts
        // the session and then reads progress with its own `tools/call`.
        post({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-input",
          params: { arguments: { session_id: activeSession } },
        });
        if (toolResult) {
          post({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: toolResult });
        }
      }, [post, activeSession, toolResult]);

      // The frame is mounted before the session is known; tell it as soon as the
      // host answers, without waiting for a reload.
      React.useEffect(() => {
        if (!activeSession || !frameRef.current?.contentWindow) return;
        post({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-input",
          params: { arguments: { session_id: activeSession } },
        });
      }, [post, activeSession]);

      React.useEffect(() => watchHostTheme(ctx, (theme) => {
        post({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { theme } });
      }), [ctx, post]);

      const body = h("iframe", {
        ref: frameRef,
        src: PANEL_SRC,
        title: "Task Progress",
        onLoad,
        style: fullscreen ? { height: "100%" } : { height: `${height}px` },
      });

      if (fullscreen) {
        return h("div", { className: "dsh-task-progress dsh-task-progress-fullscreen" },
          h("button", {
            type: "button",
            className: "dsh-task-progress-close",
            onClick: () => setFullscreen(false),
          }, "关闭全屏"),
          body);
      }

      return h("div", { className: "dsh-task-progress" },
        !activeSession
          ? h("div", { className: "dsh-task-progress-error" },
              "尚未关联任务：面板会在助手调用 render_progress 后自动绑定当前会话。")
          : null,
        h("div", { className: "dsh-task-progress-frame" }, body));
    };
  }

  /** The persistent panel: the same app.html, opened as a sidebar page. */
  /**
   * Session hooks need `React`, which only exists inside these factories — the
   * first version declared them at module scope and every panel render threw
   * "React is not defined".
   */
  function createSessionHooks(React) {
    function useSession() {
      const [value, setValue] = React.useState(sessionStore.current);
      React.useEffect(() => {
        sessionStore.listeners.add(setValue);
        setValue(sessionStore.current);
        return () => { sessionStore.listeners.delete(setValue); };
      }, []);
      return value;
    }

    /**
     * The conversation's task session plus a host revision counter that changes on
     * every progress tool call. Without the counter the embedded app never learned
     * that new work had been recorded: it only re-read on mount, so the tab looked
     * frozen until it was closed and reopened.
     */
    function useHostBinding(enabled, conversationId) {
      const [binding, setBinding] = React.useState({ session: null, revision: 0 });
      React.useEffect(() => {
        if (!enabled) return undefined;
        let cancelled = false;
        const query = conversationId ? `?dsh=${encodeURIComponent(conversationId)}` : "";
        const load = () => {
          fetch(`/task-progress/session${query}`)
            .then(response => response.ok ? response.json() : null)
            .then(payload => {
              if (cancelled || typeof payload?.session !== "string" || !payload.session) return;
              const revision = Number.isFinite(payload.revision) ? payload.revision : 0;
              setBinding(previous => (
                previous.session === payload.session && previous.revision === revision
                  ? previous
                  : { session: payload.session, revision }
              ));
              publishSession(payload.session);
            })
            .catch(() => {});
        };
        load();
        const timer = window.setInterval(load, 3000);
        return () => { cancelled = true; window.clearInterval(timer); };
      }, [enabled, conversationId]);
      return binding;
    }

    return { useSession, useHostBinding };
  }

  function createPanel(React, ctx) {
    const h = React.createElement;
    const { useHostBinding } = createSessionHooks(React);

    /**
     * The tab body is the real `app.html` and nothing else: an earlier revision
     * also drew a native title, progress bar and task tree as a fallback, which
     * showed the same facts twice next to the embedded UI.
     */
    return function TaskProgressPanel(props) {
      const conversationId = props?.sessionId;
      // Pin the view identity to the conversation: switching away and back
      // remounts this frame, and a fresh random id would register as a second
      // fullscreen view of a session that still holds the previous lease.
      const frameSrc = conversationId
        ? `${PANEL_SRC}?view_id=tab-${encodeURIComponent(conversationId)}`
        : PANEL_SRC;
      // Only the conversation-scoped binding decides what this tab shows: the
      // shared store is a card convenience and would reintroduce cross-talk.
      const binding = useHostBinding(true, conversationId);
      const session = binding.session;
      const revision = binding.revision;
      const frameRef = React.useRef(null);
      const pageRef = React.useRef(null);
      const [frameHeight, setFrameHeight] = React.useState(0);
      const [failed, setFailed] = React.useState("");

      const post = React.useCallback((payload) => {
        const frame = frameRef.current;
        if (!frame || !frame.contentWindow) return;
        frame.contentWindow.postMessage(payload, window.location.origin);
      }, []);

      // Fill the tab's box; fall back to the viewport when the shell container
      // reports no height of its own.
      React.useEffect(() => {
        const measure = () => {
          const box = pageRef.current;
          const height = box ? box.clientHeight : 0;
          setFrameHeight(height > 120 ? height : Math.max(320, (window.innerHeight || 800) - 160));
        };
        measure();
        window.addEventListener("resize", measure);
        let observer = null;
        try {
          if (typeof ResizeObserver === "function" && pageRef.current) {
            observer = new ResizeObserver(measure);
            observer.observe(pageRef.current);
          }
        } catch {}
        return () => {
          window.removeEventListener("resize", measure);
          try { observer?.disconnect(); } catch {}
        };
      }, []);

      React.useEffect(() => {
        const onMessage = async (event) => {
          const frame = frameRef.current;
          if (!frame || event.source !== frame.contentWindow) return;
          const message = event.data;
          if (!message || message.jsonrpc !== "2.0" || typeof message.method !== "string") return;

          if (message.method === "ui/initialize") {
            if (message.id === undefined) return;
            post({
              jsonrpc: "2.0",
              id: message.id,
              result: {
                protocolVersion: message.params?.protocolVersion || "2026-01-26",
                // Fullscreen is the honest mode for a whole tab: app.html collapses
                // to its compact view in inline mode once a newer view supersedes it.
                hostContext: {
                  displayMode: "fullscreen",
                  availableDisplayModes: ["inline", "fullscreen"],
                  theme: hostTheme(ctx?.get?.("theme")),
                },
                hostCapabilities: {},
              },
            });
            return;
          }

          if (message.method === "ui/request-display-mode") {
            if (message.id !== undefined) post({ jsonrpc: "2.0", id: message.id, result: { mode: "fullscreen" } });
            return;
          }

          if (message.method === "ui/notifications/size-changed") return;
          if (message.method.startsWith("ui/")) return;

          if (message.id === undefined) {
            try {
              await fetch(RPC_URL, {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify(message),
              });
            } catch {}
            return;
          }

          try {
            const response = await fetch(RPC_URL, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify(message),
            });
            post(await response.json());
          } catch (cause) {
            setFailed(String(cause?.message || cause));
            post({ jsonrpc: "2.0", id: message.id, error: { code: -32000, message: String(cause?.message || cause) } });
          }
        };

        window.addEventListener("message", onMessage);
        return () => window.removeEventListener("message", onMessage);
      }, [post]);

      const tellSession = React.useCallback(() => {
        if (!session) return;
        post({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-input",
          params: { arguments: { session_id: session } },
        });
      }, [post, session]);

      /**
       * Read one full snapshot and hand it to the frame as a tool result.
       * `receiveResult` in app.html ignores anything whose `view` is not `full`,
       * so this asks for exactly that shape.
       */
      const pushSnapshot = React.useCallback(async () => {
        if (!session) return;
        try {
          const response = await fetch(RPC_URL, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              jsonrpc: "2.0", id: 1, method: "tools/call",
              params: { name: "get_progress", arguments: { session_id: session, view: "full" } },
            }),
          });
          const payload = await response.json();
          const result = payload?.result;
          if (!result || result.isError) {
            reportDiag("panel-sync-skipped", { session, revision });
            return;
          }
          post({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result });
          reportDiag("panel-sync", {
            session,
            revision,
            stateRevision: result?.structuredContent?.state_revision ?? null,
            view: result?.structuredContent?.view ?? null,
          });
        } catch (cause) {
          reportDiag("panel-sync-failed", { session, revision, error: String(cause?.message || cause) });
        }
      }, [post, session]);

      const onLoad = React.useCallback(() => {
        reportDiag("panel-frame-loaded", { session });
        tellSession();
        void pushSnapshot();
      }, [reportDiag, session, tellSession, pushSnapshot]);

      // The host bumps the revision on every progress tool call: push the new
      // snapshot so the tab updates in place instead of waiting for a reopen.
      React.useEffect(() => {
        void pushSnapshot();
      }, [pushSnapshot, revision]);

      // Belt and braces: poll the snapshot too. app.html's own polling does not
      // refresh this embed (its read path reports no `view`), and relying on the
      // revision signal alone left the tab frozen when that signal was missed.
      React.useEffect(() => {
        if (!session) return undefined;
        const timer = window.setInterval(() => { void pushSnapshot(); }, 5000);
        return () => window.clearInterval(timer);
      }, [pushSnapshot, session]);

      React.useEffect(() => {
        reportDiag("panel-mounted", { session, frameHeight, viewport: window.innerHeight || 0 });
      }, [session, frameHeight]);

      // A session can arrive after the frame has loaded; tell it without a reload.
      React.useEffect(() => { tellSession(); }, [tellSession]);

      React.useEffect(() => watchHostTheme(ctx, (theme) => {
        post({ jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: { theme } });
      }), [ctx, post]);

      return h("div", {
        className: "dsh-task-progress-page",
        ref: pageRef,
        style: { height: frameHeight ? `${frameHeight}px` : "100%" },
      },
        failed
          ? h("div", { className: "dsh-task-progress-error" }, `读取进度失败：${failed}`)
          : null,
        h("div", { className: "dsh-task-progress-frame", style: { height: frameHeight ? `${frameHeight}px` : "100%" } },
          h("iframe", {
            ref: frameRef,
            // app.html keeps the first session it adopts and drops every result for
            // another one, so a new session needs a new frame; the pinned view id
            // still makes the remount renew its own lease.
            key: `${frameSrc}#${session ?? "none"}`,
            src: frameSrc,
            title: "Task Progress",
            onLoad,
            style: { height: frameHeight ? `${frameHeight}px` : "100%" },
          })));
    };
  }

  /** Keeps a thrown panel recoverable and visible instead of an empty page. */
  function createBoundary(React) {
    const h = React.createElement;
    class Boundary extends React.Component {
      constructor(props) {
        super(props);
        this.state = { message: "" };
      }

      static getDerivedStateFromError(error) {
        return { message: String(error?.message || error) };
      }

      render() {
        if (this.state.message) {
          return h("div", { className: "dsh-task-progress-page" },
            h("div", { className: "dsh-task-progress-error" },
              `进度面板渲染失败：${this.state.message}`));
        }
        return this.props.children;
      }
    }
    return function PanelBoundary(props) {
      return h(Boundary, null, h(props.component));
    };
  }

  /**
   * Right-edge dock. DeepSeekRuntime exposes no public way to add a tab to its
   * right sidebar (`sidebar.right.pane.tab` keys are owned by the controller, and
   * `rightbar.session` is a single slot that would replace the shipped column),
   * so the panel docks to the right edge as an overlay and leaves the
   * conversation untouched.
   */
  /**
   * Right-sidebar tab, registered the way dsh-context does it: the
   * `sidebarRightTabs` service owns the tab list, and the pane body and its title
   * are keyed slots dispatched with the same id. Without the service the attempt
   * is skipped rather than failing the plugin, so an older shell still works.
   */
  const TAB_ID = "task-progress";

  function installRightTab(ctx, React) {
    const Panel = createPanel(React, ctx);
    const TabTitle = function TaskProgressTabTitle() {
      return React.createElement("span", null, "任务进度");
    };

    return ctx.inject(["sidebarRightTabs", "slots"], (scope) => {
      const disposers = [];
      const keep = (disposer) => {
        if (typeof disposer === "function") disposers.push(disposer);
      };
      try {
        const tabs = scope.sidebarRightTabs;
        if (tabs && typeof tabs.register === "function") {
          keep(tabs.register({
            id: TAB_ID,
            kind: TAB_ID,
            title: () => "任务进度",
            guide: [{
              id: TAB_ID,
              order: 20,
              title: () => "任务进度",
              description: () => "在右侧栏跟踪当前会话的多步骤任务进度",
            }],
          }));
        }
        keep(scope.slots.inject("sidebar.right.pane.tab", () => scope.slots.register({
          name: "sidebar.right.pane.tab",
          key: TAB_ID,
        }, Panel)));
        keep(scope.slots.inject("sidebar.right.pane.tab.title", () => scope.slots.register({
          name: "sidebar.right.pane.tab.title",
          key: TAB_ID,
        }, TabTitle)));
      } catch (error) {
        for (const disposer of disposers) { try { disposer(); } catch {} }
        ctx.logger?.warn?.(`dsh-task-progress: right tab unavailable: ${String(error)}`);
        return () => {};
      }
      return () => {
        for (const disposer of disposers) { try { disposer(); } catch {} }
      };
    });
  }

  window.__ModuleLoader__.load({
    id: "dsh-task-progress-plugin",
    factory: (require) => {
      const React = require("react");
      // Components are built inside apply: they need that context to read the
      // shell's colour scheme and session state.
      let Card = null;
      return {
        name: "dsh-task-progress-plugin",
        inject: ["slots"],
        apply(ctx) {
          installStyles(ctx);
          Card = createCard(React, ctx);
          // The card stays with the tool call, matching where Codex renders it.
          ctx.slots.inject("tool.call.toolview", () => ctx.slots.register({
            name: "tool.call.toolview",
            key: RENDER_TOOL,
          }, Card));
          // Persistent progress lives in the right sidebar, so it never covers
          // the conversation the way a main-panel entry would.
          const disposeTab = installRightTab(ctx, React);
          if (typeof disposeTab === "function") ctx.effect(() => disposeTab);
        },
      };
    },
  });
})();
