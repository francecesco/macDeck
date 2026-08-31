import AppKit
import WebKit
import MacDeckCore

let indirizzoAgent = URL(string: "http://127.0.0.1:8765/")!

@MainActor
final class Finestra: NSObject, NSApplicationDelegate, NSWindowDelegate,
                       WKScriptMessageHandler {
    var finestra: NSWindow!
    var vistaWeb: VistaWebConDrop!
    var pollingSalute: Task<Void, Never>?
    let registratore = RegistratoreTasti()

    func applicationDidFinishLaunching(_ n: Notification) {
        finestra = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        finestra.title = "MacDeck"
        finestra.center()
        finestra.setFrameAutosaveName("MacDeckFinestra")
        finestra.delegate = self

        let conf = WKWebViewConfiguration()
        conf.userContentController.addUserScript(Ponte.userScript())
        conf.userContentController.add(self, name: "macdeck")
        vistaWeb = VistaWebConDrop(frame: .zero, configuration: conf)
        vistaWeb.autoresizingMask = [.width, .height]
        // .fileURL si aggiunge a cio' che WebKit registra da solo: senza
        // questo si romperebbe il trascina-per-riordinare che la pagina
        // gia' fa fra uno slot e l'altro.
        vistaWeb.registerForDraggedTypes(
            vistaWeb.registeredDraggedTypes + [.fileURL])
        finestra.contentView = vistaWeb

        finestra.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // Senza questo la finestra resta bianca fino a dieci secondi mentre
        // l'avvio verifica se l'agent risponde: un piccolo placeholder scuro
        // e' meglio di un flash bianco che sembra un guasto.
        vistaWeb.loadHTMLString(Self.paginaDiAvvio, baseURL: nil)

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
                             + "install-agent"
                             + "\n\nFatto questo, chiudi e riapri MacDeck: "
                             + "da sola l'app non se ne accorge.")
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

    func userContentController(_ c: WKUserContentController,
                               didReceive m: WKScriptMessage) {
        guard let corpo = m.body as? [String: Any],
              let cmd = corpo["cmd"] as? String else { return }
        switch cmd {
        case "registraScorciatoia":
            registratore.mostra(su: finestra, base: indirizzoAgent) { combo in
                guard let combo else { return }   // annullato: niente da dire
                let dati = try! JSONSerialization.data(
                    withJSONObject: ["keys": combo])
                let json = String(decoding: dati, as: UTF8.self)
                self.vistaWeb.evaluateJavaScript(
                    "window.macdeck && window.macdeck.onScorciatoia && "
                    + "window.macdeck.onScorciatoia(\(json))")
            }
        default:
            break                      // un comando ignoto non e' un guasto
        }
    }

    /// Placeholder scuro mostrato mentre l'avvio verifica se l'agent
    /// risponde: sfondo coerente con l'editor, cosi' non fa un lampo bianco.
    private static let paginaDiAvvio = """
    <!doctype html>
    <html><head><meta charset="utf-8"><style>
      html, body { height: 100%; margin: 0; background: #1e1e1e;
                   display: flex; align-items: center; justify-content: center; }
      p { color: #999; font: 13px -apple-system, sans-serif; }
    </style></head>
    <body><p>avvio l'agent...</p></body></html>
    """
}
