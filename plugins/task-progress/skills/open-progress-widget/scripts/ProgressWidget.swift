import AppKit
import WebKit

// Only the top-level loopback viewer may operate the native window or save exports.
@MainActor
final class WidgetController: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var entry: URL!
    var expandedFrame: NSRect?
    var compact = false
    var exportDirectory: URL!
    var observation: NSKeyValueObservation?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let config = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
              let value = config["url"] as? String, let url = URL(string: value),
              url.scheme == "http", url.host == "127.0.0.1", url.port != nil,
              url.user == nil, url.password == nil, url.path == "/widget", url.fragment?.isEmpty == false else {
            NSApp.terminate(nil); return
        }
        entry = url
        if let directory = config["export_directory"] as? String, directory.hasPrefix("/") {
            exportDirectory = URL(fileURLWithPath: directory, isDirectory: true)
        } else {
            exportDirectory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first!
                .appendingPathComponent("Task Progress Exports", isDirectory: true)
        }
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 900)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: min(1180, screen.width - 64), height: min(800, screen.height - 64)),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Task Progress"
        window.minSize = NSSize(width: 340, height: 340)
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .darkAqua)
        window.delegate = self
        window.center()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "widgetControl")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        window.contentView = webView
        observation = webView.observe(\.title, options: [.new]) { [weak self] view, _ in
            DispatchQueue.main.async { self?.window.title = view.title ?? "Task Progress" }
        }
        webView.load(URLRequest(url: entry))
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let receipt: [String: Any] = ["window_opened": window.isVisible,
            "pid": ProcessInfo.processInfo.processIdentifier, "always_on_top": window.level == .floating,
            "native_bridge": 1]
        if let data = try? JSONSerialization.data(withJSONObject: receipt) {
            FileHandle.standardOutput.write(data + Data([10]))
        }
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关闭任务进度", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; mainMenu.addItem(appItem); NSApp.mainMenu = mainMenu
    }

    func state() -> [String: Any] {
        ["pinned": window.level == .floating, "compact": compact,
         "level": window.level.rawValue, "width": window.frame.width, "height": window.frame.height]
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let origin = message.frameInfo.request.url,
              origin.scheme == entry.scheme, origin.host == entry.host, origin.port == entry.port,
              origin.path == "/widget", let body = message.body as? [String: Any],
              let id = body["id"] as? String, id.hasPrefix("widget-"), id.count < 100,
              let action = body["action"] as? String else { return }
        var response: [String: Any] = ["id": id]
        do {
            switch action {
            case "state": response["result"] = state()
            case "pin":
                guard let pinned = body["value"] as? Bool else { throw WidgetError.invalidControl }
                window.level = pinned ? .floating : .normal
                response["result"] = state()
            case "compact":
                guard let value = body["value"] as? Bool else { throw WidgetError.invalidControl }
                if value != compact {
                    if value {
                        expandedFrame = window.frame
                        let screen = window.screen?.visibleFrame ?? window.frame
                        window.setContentSize(NSSize(width: min(480, screen.width), height: min(680, screen.height - 40)))
                    } else if let frame = expandedFrame { window.setFrame(frame, display: true) }
                    compact = value
                }
                response["result"] = state()
            case "theme":
                guard let theme = body["value"] as? String, ["dark", "light"].contains(theme) else { throw WidgetError.invalidControl }
                window.appearance = NSAppearance(named: theme == "dark" ? .darkAqua : .aqua)
                response["result"] = ["theme": theme]
            case "saveExports": response["result"] = try saveExports(body["value"])
            default: throw WidgetError.invalidControl
            }
        } catch { response["error"] = error.localizedDescription }
        if let data = try? JSONSerialization.data(withJSONObject: response), let json = String(data: data, encoding: .utf8) {
            webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('task-progress-native-result',{detail:\(json)}))", completionHandler: nil)
        }
    }

    enum WidgetError: LocalizedError {
        case invalidControl, invalidExport
        var errorDescription: String? {
            switch self {
            case .invalidControl: return "窗口操作无效。"
            case .invalidExport: return "导出文件不完整或格式不受支持。"
            }
        }
    }

    func saveExports(_ value: Any?) throws -> [String: Any] {
        guard let contents = value as? [[String: Any]], contents.count == 2 else { throw WidgetError.invalidExport }
        var files: [(String, Data)] = []
        for item in contents {
            guard item["type"] as? String == "resource", let resource = item["resource"] as? [String: Any],
                  let uri = resource["uri"] as? String, let url = URL(string: uri), url.scheme == "file" else { throw WidgetError.invalidExport }
            let name = url.lastPathComponent
            guard name.hasPrefix("Task-Progress-"), !name.contains("/"), !name.contains("\\"), name.utf8.count <= 240 else { throw WidgetError.invalidExport }
            let data: Data
            if name.hasSuffix(".xlsx"), resource["mimeType"] as? String == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               let blob = resource["blob"] as? String, let decoded = Data(base64Encoded: blob) { data = decoded }
            else if name.hasSuffix(".html"), resource["mimeType"] as? String == "text/html", let text = resource["text"] as? String {
                data = Data(text.utf8)
            } else { throw WidgetError.invalidExport }
            guard !data.isEmpty, data.count <= 20 * 1024 * 1024 else { throw WidgetError.invalidExport }
            files.append((name, data))
        }
        guard Set(files.map { String($0.0.suffix(5)) }) == Set([".html", ".xlsx"]),
              Set(files.map { String($0.0.dropLast(5)) }).count == 1 else { throw WidgetError.invalidExport }
        try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
        let info = try exportDirectory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard info.isDirectory == true, info.isSymbolicLink != true else { throw WidgetError.invalidExport }
        var written: [URL] = [], staged: [URL] = []
        do {
            for (name, data) in files {
                let temporary = exportDirectory.appendingPathComponent(".task-progress-" + UUID().uuidString)
                staged.append(temporary)
                try data.write(to: temporary, options: .withoutOverwriting)
                var destination = exportDirectory.appendingPathComponent(name)
                if FileManager.default.fileExists(atPath: destination.path) {
                    destination = exportDirectory.appendingPathComponent(UUID().uuidString + "-" + name)
                }
                try FileManager.default.moveItem(at: temporary, to: destination)
                written.append(destination)
            }
        } catch {
            for url in staged + written { try? FileManager.default.removeItem(at: url) }
            throw error
        }
        return ["saved": true, "files": written.map { ["name": $0.lastPathComponent, "path": $0.path] },
                "output_directory": exportDirectory.path]
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        let local = url.scheme == "http" && url.host == entry.host && url.port == entry.port
        decisionHandler(local || url.scheme == "about" || url.scheme == "blob" ? .allow : .cancel)
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let controller = WidgetController()
    application.setActivationPolicy(.accessory)
    application.delegate = controller
    application.run()
}
