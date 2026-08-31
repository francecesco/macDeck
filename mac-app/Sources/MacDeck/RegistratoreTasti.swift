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
    private var messaggio: NSTextField?
    private var completamento: ((String?) -> Void)?

    private static let messaggioIniziale =
        "Premi la combinazione.\n\nAlcune scorciatoie di sistema "
        + "(cmd+shift+4, cmd+space) non arrivano fin qui: per quelle "
        + "scrivila a mano nel campo."

    private static let messaggioErrore =
        "Non sono riuscito a tradurre quella combinazione. Riprova, "
        + "oppure scrivila a mano nel campo."

    func mostra(su finestra: NSWindow,
                base: URL,
                completamento: @escaping (String?) -> Void) {
        // Il guardiano e' nostro, non di beginSheet: in index.html si e'
        // gia' deciso di non fare affidamento sulla modalita' del foglio
        // come garanzia di sicurezza (vedi il commento su onScorciatoia).
        // Farci affidamento qui invece farebbe dire al codice due cose
        // diverse sulla stessa domanda.
        guard foglio == nil else { return }

        let f = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 130),
                         styleMask: [.titled], backing: .buffered, defer: false)
        let t = NSTextField(labelWithString: Self.messaggioIniziale)
        t.frame = NSRect(x: 20, y: 45, width: 340, height: 70)
        t.maximumNumberOfLines = 4
        let annulla = NSButton(title: "Annulla", target: self,
                               action: #selector(annullaPremuto))
        annulla.frame = NSRect(x: 270, y: 12, width: 90, height: 26)
        f.contentView?.addSubview(t)
        f.contentView?.addSubview(annulla)
        foglio = f
        messaggio = t

        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { ev in
            let mods = modificatori(daFlag: ev.modifierFlags.rawValue)
            let codice = Int(ev.keyCode)
            // Key code 53 e' Escape su ogni tastiera Mac. Senza modificatori
            // annulla come il pulsante; con un modificatore (cmd+escape) e'
            // una combinazione legittima e si registra come le altre.
            if codice == 53 && mods.isEmpty {
                self.chiudi(conEsito: nil)
                return nil
            }
            let caratteri = ev.charactersIgnoringModifiers ?? ""
            Task { @MainActor in
                let combo = await Rete.canonicalizza(
                    base: base,
                    keyCode: codice,
                    modificatori: mods,
                    caratteri: caratteri)
                guard let combo else {
                    // Traduzione fallita: non e' un annullamento. Il foglio
                    // resta aperto, il messaggio lo dice, e si puo' riprovare
                    // o passare al campo di testo. Un fallimento silenzioso
                    // e' esattamente il modo in cui questo progetto si e'
                    // gia' fatto male due volte.
                    self.messaggio?.stringValue = Self.messaggioErrore
                    return
                }
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
        messaggio = nil
        let c = completamento
        completamento = nil
        c?(combo)
    }
}
