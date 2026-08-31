import AppKit
import WebKit

let indirizzoAgent = URL(string: "http://127.0.0.1:8765/")!

final class Finestra: NSObject, NSApplicationDelegate {
    var finestra: NSWindow!
    var vistaWeb: WKWebView!

    func applicationDidFinishLaunching(_ n: Notification) {
        finestra = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        finestra.title = "MacDeck"
        finestra.center()
        finestra.setFrameAutosaveName("MacDeckFinestra")

        vistaWeb = WKWebView(frame: .zero)
        vistaWeb.autoresizingMask = [.width, .height]
        finestra.contentView = vistaWeb
        vistaWeb.load(URLRequest(url: indirizzoAgent))

        finestra.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(
        _ s: NSApplication) -> Bool { true }
}
