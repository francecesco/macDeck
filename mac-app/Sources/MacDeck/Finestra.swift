import AppKit
import WebKit
import MacDeckCore

let indirizzoAgent = URL(string: "http://127.0.0.1:8765/")!

@MainActor
final class Finestra: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var finestra: NSWindow!
    var vistaWeb: WKWebView!
    var pollingSalute: Task<Void, Never>?

    func applicationDidFinishLaunching(_ n: Notification) {
        finestra = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        finestra.title = "MacDeck"
        finestra.center()
        finestra.setFrameAutosaveName("MacDeckFinestra")
        finestra.delegate = self

        vistaWeb = WKWebView(frame: .zero)
        vistaWeb.autoresizingMask = [.width, .height]
        finestra.contentView = vistaWeb

        finestra.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        Task.detached {
            let avvio = Avvio(
                agentRisponde: { await Rete.risponde(indirizzoAgent) },
                launchAgentCaricato: { Servizio.caricato() },
                kickstart: { Servizio.kickstart() },
                attendi: { try? await Task.sleep(for: .milliseconds(300)) })
            let esito = await avvio.esegui()
            await MainActor.run {
                switch esito {
                case .pronto:
                    self.mostraEditor()
                case .nonRiparte:
                    self.mostraErrore(
                        titolo: "L'agent non riparte",
                        corpo: Servizio.codaDelLog())
                case .launchAgentAssente:
                    self.mostraErrore(
                        titolo: "Manca il LaunchAgent",
                        corpo: "L'agent non e' installato come servizio.\n\n"
                             + "cd agent && .venv/bin/python -m macdeck.cli "
                             + "install-agent")
                }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(
        _ s: NSApplication) -> Bool { true }

    /// Monta web view e barra di salute, poi avvia il polling di quest'ultima.
    @MainActor func mostraEditor() {
        let barra = BarraSalute(frame: NSRect(x: 0, y: 0, width: 0, height: 24))
        barra.translatesAutoresizingMaskIntoConstraints = false
        vistaWeb.translatesAutoresizingMaskIntoConstraints = false
        let pila = NSView(frame: finestra.contentLayoutRect)
        pila.autoresizingMask = [.width, .height]
        pila.addSubview(vistaWeb)
        pila.addSubview(barra)
        NSLayoutConstraint.activate([
            vistaWeb.topAnchor.constraint(equalTo: pila.topAnchor),
            vistaWeb.leadingAnchor.constraint(equalTo: pila.leadingAnchor),
            vistaWeb.trailingAnchor.constraint(equalTo: pila.trailingAnchor),
            barra.topAnchor.constraint(equalTo: vistaWeb.bottomAnchor),
            barra.leadingAnchor.constraint(equalTo: pila.leadingAnchor),
            barra.trailingAnchor.constraint(equalTo: pila.trailingAnchor),
            barra.bottomAnchor.constraint(equalTo: pila.bottomAnchor),
            barra.heightAnchor.constraint(equalToConstant: 24),
        ])
        finestra.contentView = pila
        vistaWeb.load(URLRequest(url: indirizzoAgent))

        pollingSalute = Task { @MainActor in
            while !Task.isCancelled {
                barra.aggiorna(await Rete.salute(indirizzoAgent))
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    /// Chiusa la finestra, il polling non ha piu' senso: lo si ferma qui.
    func windowWillClose(_ notification: Notification) {
        pollingSalute?.cancel()
    }

    @MainActor func mostraErrore(titolo: String, corpo: String) {
        let contenitore = NSView(frame: finestra.contentLayoutRect)
        contenitore.autoresizingMask = [.width, .height]

        let t = NSTextField(labelWithString: titolo)
        t.font = .boldSystemFont(ofSize: 18)
        t.frame = NSRect(x: 24, y: contenitore.bounds.height - 56,
                         width: contenitore.bounds.width - 48, height: 24)
        t.autoresizingMask = [.width, .minYMargin]

        let scorrevole = NSScrollView(
            frame: NSRect(x: 24, y: 24, width: contenitore.bounds.width - 48,
                          height: contenitore.bounds.height - 96))
        scorrevole.autoresizingMask = [.width, .height]
        scorrevole.hasVerticalScroller = true
        let testo = NSTextView(frame: scorrevole.bounds)
        testo.isEditable = false
        testo.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        testo.string = corpo
        scorrevole.documentView = testo

        contenitore.addSubview(t)
        contenitore.addSubview(scorrevole)
        finestra.contentView = contenitore
    }
}

enum Rete {
    static func risponde(_ base: URL) async -> Bool {
        var r = URLRequest(url: base.appendingPathComponent("api/config"))
        r.timeoutInterval = 2.0
        guard let (_, resp) = try? await URLSession.shared.data(for: r),
              let http = resp as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    static func salute(_ base: URL) async -> Salute? {
        var r = URLRequest(url: base.appendingPathComponent("api/health"))
        r.timeoutInterval = 3.0
        guard let (dati, _) = try? await URLSession.shared.data(for: r)
        else { return nil }
        return try? Salute.leggi(dati)
    }
}

enum Servizio {
    static let etichetta = "io.macdeck.agent"

    private static func launchctl(_ argomenti: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = argomenti
        let tubo = Pipe()
        p.standardOutput = tubo
        p.standardError = tubo
        guard (try? p.run()) != nil else { return (-1, "") }
        let dati = tubo.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: dati, as: UTF8.self))
    }

    static func caricato() -> Bool {
        launchctl(["print", "gui/\(getuid())/\(etichetta)"]).0 == 0
    }

    static func kickstart() -> Bool {
        launchctl(["kickstart", "-k", "gui/\(getuid())/\(etichetta)"]).0 == 0
    }

    /// Quando l'agent non parte, il motivo e' nel log. Una pagina bianca no.
    static func codaDelLog() -> String {
        let testo = (try? String(contentsOfFile: "/tmp/macdeck.log",
                                 encoding: .utf8)) ?? "(log non leggibile)"
        return testo.split(separator: "\n").suffix(30).joined(separator: "\n")
    }
}
