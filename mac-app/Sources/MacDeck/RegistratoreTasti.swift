import AppKit
import MacDeckCore

/// Un foglio che ascolta i tasti veri e restituisce la combinazione.
///
/// Limite dichiarato: le combinazioni che macOS si riserva (cmd+shift+4,
/// cmd+space) non arrivano mai qui, perche' il sistema le consuma prima.
/// Il pannello lo dice, invece di restare in ascolto per finta.
@MainActor
final class RegistratoreTasti: NSObject {
    private var monitor: Any?
    private var foglio: NSWindow?
    private var completamento: ((String?) -> Void)?

    func mostra(su finestra: NSWindow,
                base: URL,
                completamento: @escaping (String?) -> Void) {
        let f = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 130),
                         styleMask: [.titled], backing: .buffered, defer: false)
        let t = NSTextField(labelWithString:
            "Premi la combinazione.\n\nAlcune scorciatoie di sistema "
            + "(cmd+shift+4, cmd+space) non arrivano fin qui: per quelle "
            + "scrivila a mano nel campo.")
        t.frame = NSRect(x: 20, y: 45, width: 340, height: 70)
        t.maximumNumberOfLines = 4
        let annulla = NSButton(title: "Annulla", target: self,
                               action: #selector(annullaPremuto))
        annulla.frame = NSRect(x: 270, y: 12, width: 90, height: 26)
        f.contentView?.addSubview(t)
        f.contentView?.addSubview(annulla)
        foglio = f

        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { ev in
            let mods = modificatori(daFlag: ev.modifierFlags.rawValue)
            let codice = Int(ev.keyCode)
            let caratteri = ev.charactersIgnoringModifiers ?? ""
            Task { @MainActor in
                let combo = await Rete.canonicalizza(
                    base: base,
                    keyCode: codice,
                    modificatori: mods,
                    caratteri: caratteri)
                self.chiudi(conEsito: combo)
            }
            return nil                 // il tasto non prosegue
        }

        self.completamento = completamento
        finestra.beginSheet(f) { _ in }
    }

    @objc private func annullaPremuto() {
        chiudi(conEsito: nil)
    }

    /// Chiude e avvisa **una volta sola**, comunque sia finita.
    ///
    /// Il ramo che conta e' l'annullamento: senza questo, il foglio sparirebbe
    /// e la pagina resterebbe ad aspettare una scorciatoia che non arriva.
    private func chiudi(conEsito combo: String? = nil) {
        if let m = monitor { NSEvent.removeMonitor(m); monitor = nil }
        if let f = foglio { f.sheetParent?.endSheet(f); foglio = nil }
        let c = completamento
        completamento = nil
        c?(combo)
    }
}
