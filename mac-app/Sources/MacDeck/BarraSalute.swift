import AppKit
import MacDeckCore

/// Due indicatori in fondo alla finestra: agent e deck.
///
/// Non mostra il permesso Accessibilita': quel banner e' della pagina e
/// resta uno solo. La barra dice quello che la pagina NON puo' dire.
final class BarraSalute: NSView {
    private let agente = NSTextField(labelWithString: "")
    private let deck = NSTextField(labelWithString: "")

    override init(frame: NSRect) {
        super.init(frame: frame)
        let riga = NSStackView(views: [agente, deck])
        riga.orientation = .horizontal
        riga.spacing = 18
        riga.translatesAutoresizingMaskIntoConstraints = false
        addSubview(riga)
        NSLayoutConstraint.activate([
            riga.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            riga.centerYAnchor.constraint(equalTo: centerYAnchor),
        ])
        for e in [agente, deck] { e.font = .systemFont(ofSize: 11) }
        aggiorna(nil)
    }

    required init?(coder: NSCoder) { fatalError("non si usa xib") }

    func aggiorna(_ s: Salute?) {
        guard let s else {
            agente.stringValue = "⚪︎ agent non raggiungibile"
            deck.stringValue = "⚪︎ deck: non so"
            return
        }
        agente.stringValue = "🟢 agent"
        deck.stringValue = s.deckInRete
            ? "🟢 deck \(s.deck!)"
            : "⚪︎ deck non in rete"
    }
}
